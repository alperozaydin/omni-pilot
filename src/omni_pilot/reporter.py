"""Report generation for micronutrient analysis."""
from __future__ import annotations

import os

from jinja2 import Template
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from omni_pilot.matcher import GOOD_DISTANCE

# Status emoji and color mapping
STATUS_DISPLAY = {
    "ok": ("🟢", "OK", "green"),
    "low": ("🟡", "Low", "yellow"),
    "deficient": ("🔴", "Deficient", "red"),
    "high": ("🟠", "High", "dark_orange"),
    "unknown": ("⚪", "Unknown", "dim"),
}

# Nutrient category grouping by key
NUTRIENT_CATEGORIES = {
    "Vitamins": [
        "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
        "vitamin_k_mcg", "b1_thiamine_mg", "b2_riboflavin_mg", "b3_niacin_mg",
        "b5_pantothenic_acid_mg", "b6_pyridoxine_mg", "b12_cobalamin_mcg",
        "folate_mcg",
    ],
    "Minerals": [
        "calcium_mg", "iron_mg", "zinc_mg", "magnesium_mg", "manganese_mg",
        "phosphorus_mg", "potassium_mg", "selenium_mcg", "copper_mg", "sodium_mg",
    ],
    "Other": [
        "fiber_g", "choline_mg", "omega3_ala_g", "omega3_epa_dha_mg",
    ],
    "Amino Acids": [
        "histidine_g", "isoleucine_g", "leucine_g", "lysine_g",
        "methionine_cysteine_g", "phenylalanine_tyrosine_g",
        "threonine_g", "tryptophan_g", "valine_g",
    ],
}


def _summary_line(nutrients: dict) -> str:
    """Build the summary counts line."""
    counts = {"ok": 0, "low": 0, "deficient": 0, "high": 0, "unknown": 0}
    for n in nutrients.values():
        status = n["status"]
        counts[status] = counts.get(status, 0) + 1
    total = sum(counts.values())
    ok = counts["ok"]
    parts = [
        f"{ok}/{total} in range",
        f"{counts['low']} low",
        f"{counts['deficient']} deficient",
        f"{counts['high']} high",
    ]
    floor_count = sum(1 for n in nutrients.values() if n["is_floor"])
    if floor_count:
        parts.append(f"{floor_count} from partial data")
    return " · ".join(parts)


def _coverage_line(coverage: dict) -> str:
    """Build the coverage stats line."""
    mapped = coverage["mapped_entries"]
    total = coverage["total_food_entries"]
    skipped = coverage["skipped_entries"]
    unresolved = coverage["unresolved_entries"]
    return f"{mapped}/{total} entries analyzed ({skipped} skipped, {unresolved} unresolved)"


def _low_confidence_line(coverage: dict) -> str | None:
    """Name the foods whose USDA match fits the logged macros poorly, if any."""
    foods = coverage["low_confidence_foods"]
    if not foods:
        return None
    described = []
    for food in foods:
        text = f"{food['name']} → {food['usda_name']}"
        if food["macro_distance"] is not None:
            text += f" (off {food['macro_distance'] * 100:.0f}%)"
        described.append(text)
    share = coverage["low_confidence_weight_pct"]
    return f"Low-confidence matches ({share:.0f}% of analysed weight): {', '.join(described)}"


def _macro_check(distance: float | None, prefix: str = "") -> str:
    """How a custom food's recipe macros compare with the logged ones."""
    if distance is None:
        return f"{prefix}not checked"
    text = f"{prefix}off {distance * 100:.0f}%"
    return f"⚠ {text}" if distance > GOOD_DISTANCE else text


def _custom_foods_line(coverage: dict) -> tuple[str, bool] | None:
    """Name each custom food with its recipe and macro check, and whether any is off."""
    recipes = coverage["custom_recipes"]
    if not recipes:
        return None
    described = []
    flagged = False
    for recipe in recipes:
        for food in recipe["foods"]:
            distance = food["macro_distance"]
            described.append(f"{food['name']} → {recipe['recipe']} ({_macro_check(distance)})")
            flagged = flagged or (distance is not None and distance > GOOD_DISTANCE)
    return f"Custom foods: {', '.join(described)}", flagged


def print_terminal_report(result: dict, settings: dict) -> None:
    """Print a Rich-formatted micronutrient report to the terminal."""
    console = Console()
    show_ok = settings.get("output", {}).get("show_ok_nutrients", True)
    show_aminos = settings.get("output", {}).get("show_amino_acids", True)

    period = result["period"]
    nutrients = result["nutrients"]
    coverage = result["coverage"]

    # Header panel
    header_text = Text()
    header_text.append("Micronutrient Analysis Report\n", style="bold")
    header_text.append(
        f"Period: {period['start']} → {period['end']} ({period['days']} days)"
    )
    console.print(Panel(header_text, expand=True))
    console.print()

    # Summary and coverage
    console.print(f"  Summary: {_summary_line(nutrients)}")
    console.print(f"  Coverage: {_coverage_line(coverage)}")
    console.print()

    # Nutrient tables by category
    for category, keys in NUTRIENT_CATEGORIES.items():
        if category == "Amino Acids" and not show_aminos:
            continue

        # Filter to nutrients that exist in the result
        category_nutrients = [
            (k, nutrients[k]) for k in keys if k in nutrients
        ]
        if not category_nutrients:
            continue

        # Filter out OK nutrients if configured
        if not show_ok:
            category_nutrients = [
                (k, n) for k, n in category_nutrients if n["status"] != "ok"
            ]
            if not category_nutrients:
                continue

        table = Table(title=f"── {category} ──", show_header=True, expand=True)
        # Widths are sized so the whole row, Data column included, survives an
        # 80-column terminal (a-Shell on iPhone). The coverage figure is the
        # deliverable here, so it must not be the first thing a narrow terminal
        # clips.
        table.add_column("Nutrient", style="bold", min_width=18)
        table.add_column("Unit", justify="center", min_width=4)
        table.add_column("Daily Avg", justify="right", min_width=9)
        table.add_column("Target", justify="right", min_width=6)
        table.add_column("Status", justify="center", min_width=12)
        table.add_column("Data", justify="right", min_width=6)

        for key, n in category_nutrients:
            emoji, label, color = STATUS_DISPLAY.get(
                n["status"], ("⚪", "Unknown", "dim")
            )
            if n["is_floor"]:
                label = f"{label}*"
            target_str = (
                f"{n['target']:.1f}" if n["target"] is not None else "—"
            )
            coverage_str = (
                f"{n['coverage_pct']:.1f}%" if n["coverage_pct"] is not None else "—"
            )
            status_text = Text(f"{emoji} {label}", style=color)
            table.add_row(
                n["name"],
                n["unit"],
                f"{n['daily_avg']:.1f}",
                target_str,
                status_text,
                coverage_str,
            )

        console.print(table)
        console.print()

    if any(n["is_floor"] for n in nutrients.values()):
        console.print(
            "  * computed from partial USDA data — the true value can only be higher",
            style="dim",
        )
        console.print()

    # Warnings
    if coverage["skipped_foods"]:
        foods_str = ", ".join(coverage["skipped_foods"])
        console.print(f"  ⚠ Skipped foods: {foods_str}", style="yellow")
    if coverage["unresolved_foods"]:
        foods_str = ", ".join(coverage["unresolved_foods"])
        console.print(f"  ⚠ Unresolved foods: {foods_str}", style="red")
    low_confidence_line = _low_confidence_line(coverage)
    if low_confidence_line:
        # Text, not a markup string: food and USDA names can contain "[...]".
        console.print(Text(f"  ⚠ {low_confidence_line}", style="yellow"))

    custom_foods_line = _custom_foods_line(coverage)
    if custom_foods_line:
        line, flagged = custom_foods_line
        # Text, not a markup string: food names can contain "[...]".
        console.print(Text(f"  {line}", style="yellow" if flagged else "dim"))


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Micronutrient Analysis Report</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1a1a2e; color: #eee; padding: 2rem;
        }
        .header {
            text-align: center; margin-bottom: 2rem;
            padding: 1.5rem; background: #16213e; border-radius: 12px;
        }
        .header h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
        .header .period { color: #8892b0; }
        .summary { text-align: center; margin-bottom: 1rem; color: #ccd6f6; }
        .coverage { text-align: center; margin-bottom: 2rem; color: #8892b0; font-size: 0.9rem; }
        .category { margin-bottom: 2rem; }
        .category h2 {
            font-size: 1.1rem; margin-bottom: 0.5rem; padding-bottom: 0.3rem;
            border-bottom: 1px solid #233554;
        }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 0.5rem; color: #8892b0; font-size: 0.85rem; border-bottom: 1px solid #233554; }
        td { padding: 0.5rem; border-bottom: 1px solid #1a1a2e; }
        tr:hover { background: #16213e; }
        .status-ok { color: #64ffda; }
        .status-low { color: #ffd93d; }
        .status-deficient { color: #ff6b6b; }
        .status-high { color: #ff9f43; }
        .data-col { text-align: right; color: #8892b0; }
        .footnote { margin-top: 1rem; color: #8892b0; font-size: 0.85rem; }
        .warnings { margin-top: 2rem; padding: 1rem; background: #16213e; border-radius: 8px; }
        .warnings p { margin: 0.3rem 0; font-size: 0.9rem; }
        .custom-foods { margin-top: 2rem; padding: 1rem; background: #16213e; border-radius: 8px; font-size: 0.9rem; }
        .custom-foods h2 { font-size: 1.1rem; margin-bottom: 0.5rem; }
        .custom-foods h3 { font-size: 1rem; margin: 1rem 0 0.3rem; color: #ccd6f6; }
        .custom-foods ul { margin: 0.2rem 0 0.5rem 1.5rem; }
        .custom-foods .used-for { color: #8892b0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Micronutrient Analysis Report</h1>
        <div class="period">{{ period.start }} → {{ period.end }} ({{ period.days }} days)</div>
    </div>
    <div class="summary">{{ summary }}</div>
    <div class="coverage">{{ coverage_line }}</div>
    {% for category, category_nutrients in categories %}
    {% if category_nutrients %}
    <div class="category">
        <h2>{{ category }}</h2>
        <table>
            <thead>
                <tr>
                    <th>Nutrient</th>
                    <th>Unit</th>
                    <th>Daily Avg</th>
                    <th>Target</th>
                    <th>Status</th>
                    <th class="data-col">Data</th>
                </tr>
            </thead>
            <tbody>
            {% for n in category_nutrients %}
            <tr>
                <td>{{ n.name }}</td>
                <td>{{ n.unit }}</td>
                <td>{{ "%.1f"|format(n.daily_avg) }}</td>
                <td>{{ "%.1f"|format(n.target) if n.target is not none else "—" }}</td>
                <td class="status-{{ n.status }}">{{ n.status_label }}</td>
                <td class="data-col">
                    {% if n.coverage_pct is not none %}{{ "%.1f"|format(n.coverage_pct) }}%{% else %}—{% endif %}
                </td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}
    {% endfor %}
    {% if has_floor %}
    <p class="footnote">* computed from partial USDA data — the true value can only be higher</p>
    {% endif %}
    {% if skipped_foods or unresolved_foods or low_confidence_line %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if unresolved_foods %}<p>⚠ Unresolved: {{ unresolved_foods|join(", ") }}</p>{% endif %}
        {% if low_confidence_line %}<p>⚠ {{ low_confidence_line }}</p>{% endif %}
    </div>
    {% endif %}
    {% if custom_recipes %}
    <div class="custom-foods">
        <h2>Custom foods</h2>
        {% for r in custom_recipes %}
        <h3>{{ r.recipe }}</h3>
        <ul>
            {% for i in r.ingredients %}
            <li>{{ i.usda_name }} (FDC {{ i.fdc_id }}) — {{ "%.0f"|format(i.share_pct) }}%</li>
            {% endfor %}
        </ul>
        <p class="used-for">Used for:</p>
        <ul>
            {% for f in r.foods %}
            <li>{{ f.name }} — {{ "%.0f"|format(f.grams) }} g — {{ f.check }}</li>
            {% endfor %}
        </ul>
        {% endfor %}
    </div>
    {% endif %}
</body>
</html>
"""


def generate_html_report(result: dict, output_path: str) -> None:
    """Generate an HTML report file."""
    nutrients = result["nutrients"]
    coverage = result["coverage"]

    # Build categories data
    categories = []
    for category, keys in NUTRIENT_CATEGORIES.items():
        cat_nutrients = []
        for key in keys:
            if key in nutrients:
                n = nutrients[key].copy()
                emoji, label, _ = STATUS_DISPLAY.get(
                    n["status"], ("⚪", "Unknown", "dim")
                )
                if n["is_floor"]:
                    label = f"{label}*"
                n["status_label"] = f"{emoji} {label}"
                cat_nutrients.append(n)
        categories.append((category, cat_nutrients))

    template = Template(HTML_TEMPLATE)
    html = template.render(
        period=result["period"],
        summary=_summary_line(nutrients),
        coverage_line=_coverage_line(coverage),
        categories=categories,
        has_floor=any(n["is_floor"] for n in nutrients.values()),
        skipped_foods=coverage["skipped_foods"],
        unresolved_foods=coverage["unresolved_foods"],
        low_confidence_line=_low_confidence_line(coverage),
        custom_recipes=[
            {
                "recipe": recipe["recipe"],
                "ingredients": recipe["ingredients"],
                "foods": [
                    {**food, "check": _macro_check(food["macro_distance"], "macros ")}
                    for food in recipe["foods"]
                ],
            }
            for recipe in coverage["custom_recipes"]
        ],
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)

