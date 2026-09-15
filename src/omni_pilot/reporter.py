"""Report generation for micronutrient analysis."""
from __future__ import annotations

import os

from jinja2 import Template
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
        table.add_column("Nutrient", style="bold", min_width=30)
        table.add_column("Unit", justify="center", min_width=6)
        table.add_column("Daily Avg", justify="right", min_width=10)
        table.add_column("Target", justify="right", min_width=10)
        table.add_column("Status", justify="center", min_width=12)
        table.add_column("Data", justify="right", min_width=7)

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
        .warnings { margin-top: 2rem; padding: 1rem; background: #16213e; border-radius: 8px; }
        .warnings p { margin: 0.3rem 0; font-size: 0.9rem; }
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
            <thead><tr><th>Nutrient</th><th>Unit</th><th>Daily Avg</th><th>Target</th><th>Status</th></tr></thead>
            <tbody>
            {% for n in category_nutrients %}
            <tr>
                <td>{{ n.name }}</td>
                <td>{{ n.unit }}</td>
                <td>{{ "%.1f"|format(n.daily_avg) }}</td>
                <td>{{ "%.1f"|format(n.target) if n.target is not none else "—" }}</td>
                <td class="status-{{ n.status }}">{{ n.status_label }}</td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}
    {% endfor %}
    {% if skipped_foods or unresolved_foods %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if unresolved_foods %}<p>⚠ Unresolved: {{ unresolved_foods|join(", ") }}</p>{% endif %}
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
                n["status_label"] = f"{emoji} {label}"
                cat_nutrients.append(n)
        categories.append((category, cat_nutrients))

    template = Template(HTML_TEMPLATE)
    html = template.render(
        period=result["period"],
        summary=_summary_line(nutrients),
        coverage_line=_coverage_line(coverage),
        categories=categories,
        skipped_foods=coverage["skipped_foods"],
        unresolved_foods=coverage["unresolved_foods"],
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
