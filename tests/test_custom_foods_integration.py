"""End-to-end: the real analyze command with custom foods. Only the network is faked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import openpyxl
import pytest
import yaml
from tinydb import TinyDB

import omni_pilot.cli as cli
from omni_pilot.enricher import USDA_SEARCH_URL

REF_RANGES_PATH = str(Path(__file__).resolve().parents[1] / "config" / "reference_ranges.yaml")


def _nutrients(**by_number: float) -> list[dict]:
    """Abridged /food/{id} nutrients from {"n203": 0.88, ...}."""
    return [{"number": key[1:], "amount": value} for key, value in by_number.items()]


# Abridged /food/{id} responses. Values are real SR Legacy figures, trimmed.
USDA_FOODS = {
    170457: {"fdcId": 170457, "description": "Tomatoes, red, ripe, raw, year round average", "dataType": "SR Legacy",
             "foodNutrients": _nutrients(n203=0.88, n204=0.2, n205=3.89, n291=1.2, n301=10.0, n430=7.9)},
    # No vitamin K (430) value: exercises partial coverage
    170845: {"fdcId": 170845, "description": "Cheese, mozzarella, whole milk", "dataType": "SR Legacy",
             "foodNutrients": _nutrients(n203=22.2, n204=22.1, n205=2.4, n291=0.0, n301=505.0)},
    169249: {"fdcId": 169249, "description": "Lettuce, green leaf, raw", "dataType": "SR Legacy",
             "foodNutrients": _nutrients(n203=1.36, n204=0.15, n205=2.87, n291=1.3, n301=36.0, n430=126.3)},
}
# A search hit (search endpoint shape) for the one food that goes through USDA search.
RICE_HIT = {
    "fdcId": 900001, "description": "Rice, white, long-grain, regular, enriched, cooked", "dataType": "SR Legacy",
    "foodNutrients": [
        {"nutrientNumber": number, "value": value}
        for number, value in (("203", 2.69), ("204", 0.28), ("205", 28.17), ("291", 0.4), ("301", 10.0), ("430", 0.0))
    ],
}


class FakeUsda:
    """Stands in for USDA FoodData Central (search and fetch-by-ID), recording every request."""

    def __init__(self):
        self.searches: list[str] = []
        self.fetched_ids: list[int] = []

    def __call__(self, url, params=None, timeout=None):
        response = Mock(status_code=200)
        if url == USDA_SEARCH_URL:
            self.searches.append(params["query"])
            response.json.return_value = {"foods": [RICE_HIT]}
        else:
            fdc_id = int(url.rsplit("/", 1)[1])
            self.fetched_ids.append(fdc_id)
            response.json.return_value = USDA_FOODS[fdc_id]
        return response


def _write_export(path: Path) -> None:
    """A minimal MacroFactor export: the "Food Log" sheet in the parser's column layout."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Food Log"
    ws.append(["Date", "Time", "Food Name", "Serving Size", "Serving Qty", "Serving Weight (g)",
               "Calories (kcal)", "Fat (g)", "Carbs (g)", "Protein (g)"])
    ws.append(["2026-08-09", "12:00", "Salat Caprese", "g", 1, 264, 195.0, 11.1, 7.9, 12.9])
    ws.append(["2026-08-09", "13:00", "Misch Salat Rohkost", "g", 1, 250, 48.0, 1.0, 4.0, 3.0])
    ws.append(["2026-08-09", "19:00", "Reis", "g", 1, 200, 260.0, 0.6, 56.3, 5.4])
    ws.append(["2026-08-09", "20:00", "Wasser", "ml", 1, 500, 0.0, 0.0, 0.0, 0.0])
    wb.save(path)


def _write_custom_foods(path: Path, tomato: int, mozzarella: int) -> None:
    path.write_text(
        "caprese:\n"
        "  foods: [Salat Caprese]\n"
        "  ingredients:\n"
        f"    - {{fdc_id: 170457, amount: {tomato}}}\n"
        f"    - {{fdc_id: 170845, amount: {mozzarella}}}\n"
        "green_salad:\n"
        "  foods: [Misch Salat Rohkost]\n"
        "  ingredients:\n"
        "    - {fdc_id: 169249, amount: 100}\n"
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch, mocker):
    """Config, export and database in tmp_path; USDA faked; Gemini forbidden."""
    xlsx = tmp_path / "export.xlsx"
    _write_export(xlsx)
    _write_custom_foods(tmp_path / "custom_foods.yaml", tomato=75, mozzarella=25)
    (tmp_path / "food_mappings.yaml").write_text(yaml.dump({"mappings": {
        "Reis": "rice, white, long-grain, regular, cooked",
        "Wasser": "skip",
        # A translation from before the recipe existed: must be ignored, and kept
        "Salat Caprese": "caprese salad",
    }}))
    (tmp_path / "settings.yaml").write_text(yaml.dump({
        "usda_api_key": "fake-usda-key",  # no gemini_api_key
        "database_path": str(tmp_path / "food_db.json"),
        "mappings_path": str(tmp_path / "food_mappings.yaml"),
        "custom_foods_path": str(tmp_path / "custom_foods.yaml"),
    }))
    monkeypatch.chdir(tmp_path)  # reports/ and the default supplements path resolve inside tmp_path
    monkeypatch.setenv("COLUMNS", "300")  # keep Rich from wrapping the Custom foods line

    usda = FakeUsda()
    mocker.patch("omni_pilot.enricher.requests.get", side_effect=usda)
    mocker.patch("omni_pilot.enricher.time.sleep")
    mocker.patch(
        "omni_pilot.translator.requests.post",
        side_effect=AssertionError("Gemini must never be called in tests"),
    )
    analyze_spy = mocker.spy(cli, "analyze")

    def run():
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze", str(xlsx),
            "--settings", str(tmp_path / "settings.yaml"),
            "--ref-ranges", REF_RANGES_PATH,
            "--html",
        ])
        cli.main()
        return analyze_spy.spy_return, (tmp_path / "reports" / "latest.html").read_text()

    return tmp_path, usda, run


def test_custom_foods_end_to_end(workspace, capsys):
    tmp_path, usda, run = workspace

    # --- Run 1: ingredients fetched once by ID; custom foods never searched or translated
    result, html = run()
    out = capsys.readouterr().out

    assert sorted(usda.fetched_ids) == [169249, 170457, 170845]
    assert usda.searches and all("rice" in query for query in usda.searches)
    assert "3/4 foods resolved." in out
    # Recipes are listed heaviest first: caprese (264 g) before green_salad (250 g)
    assert "Custom foods: Salat Caprese → caprese (⚠ off 27%), Misch Salat Rohkost → green_salad (off 6%)" in out

    nutrients = result["nutrients"]
    # Caprese: 198 g tomato x 10 + 66 g mozzarella x 505; lettuce 250 g x 36; rice 200 g x 10 (mg/100 g)
    assert nutrients["calcium_mg"]["daily_avg"] == pytest.approx(463.1)
    # Only the mozzarella's 66 g lacks vitamin K: 648 of 714 analysed grams measured
    assert nutrients["vitamin_k_mcg"]["coverage_pct"] == 90.8
    assert result["coverage"]["skipped_foods"] == ["Wasser"]
    assert result["coverage"]["unresolved_foods"] == []
    assert result["coverage"]["low_confidence_foods"] == []

    assert "<h2>Custom foods</h2>" in html
    assert "Cheese, mozzarella, whole milk (FDC 170845) — 25%" in html
    assert "Salat Caprese — 264 g — ⚠ macros off 27%" in html
    assert "Misch Salat Rohkost — 250 g — macros off" in html

    db = TinyDB(str(tmp_path / "food_db.json"))
    assert sorted(entry["fdc_id"] for entry in db.table("usda_foods").all()) == [169249, 170457, 170845]
    db.close()
    # The old translation stays in the YAML, unused while the recipe claims the food
    mappings = yaml.safe_load((tmp_path / "food_mappings.yaml").read_text())["mappings"]
    assert mappings["Salat Caprese"] == "caprese salad"

    # --- Run 2: nothing changed, so USDA isn't contacted at all
    fetched, searched = len(usda.fetched_ids), len(usda.searches)
    run()
    assert (len(usda.fetched_ids), len(usda.searches)) == (fetched, searched)

    # --- Run 3: amounts tuned in the YAML; recomputed from the cache, no USDA requests
    _write_custom_foods(tmp_path / "custom_foods.yaml", tomato=82, mozzarella=18)
    result, html = run()

    assert (len(usda.fetched_ids), len(usda.searches)) == (fetched, searched)
    # 216.48 g tomato x 10 + 47.52 g mozzarella x 505 + lettuce 90 + rice 20
    assert result["nutrients"]["calcium_mg"]["daily_avg"] == pytest.approx(371.62)
    assert "Salat Caprese — 264 g — macros off 4%" in html
    assert "⚠ macros off" not in html
