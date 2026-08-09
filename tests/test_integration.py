from __future__ import annotations

import os
import pytest
from tinydb import TinyDB

from omni_pilot.parser import parse_food_log, extract_unique_foods
from omni_pilot.enricher import enrich_all_foods
from omni_pilot.analyzer import analyze
from omni_pilot.config import load_reference_ranges
from omni_pilot.reporter import print_terminal_report, generate_html_report


XLSX_PATH = "data/MacroFactor-20260809145742.xlsx"
REF_RANGES_PATH = "config/reference_ranges.yaml"


@pytest.mark.skipif(
    not os.path.exists(XLSX_PATH),
    reason="MacroFactor xlsx not found",
)
class TestIntegration:
    def test_parse_and_extract(self):
        entries = parse_food_log(XLSX_PATH)
        assert len(entries) == 223
        foods = extract_unique_foods(entries)
        assert len(foods) == 81

    def test_full_pipeline_with_mock_usda(self, tmp_path):
        """Full pipeline test with dummy micro data (no real API calls)."""
        entries = parse_food_log(XLSX_PATH)
        foods = extract_unique_foods(entries)

        # Create a simple enriched dict — map everything to a dummy micro profile
        dummy_micros = {
            "vitamin_a_mcg": 100.0,
            "vitamin_c_mg": 10.0,
            "vitamin_d_mcg": 1.0,
            "calcium_mg": 50.0,
            "iron_mg": 2.0,
            "zinc_mg": 1.5,
            "magnesium_mg": 20.0,
            "manganese_mg": 0.3,
            "phosphorus_mg": 80.0,
            "potassium_mg": 150.0,
            "selenium_mcg": 5.0,
            "copper_mg": 0.1,
            "sodium_mg": 200.0,
            "fiber_g": 2.0,
            "choline_mg": 30.0,
            "omega3_ala_g": 0.05,
            "b1_thiamine_mg": 0.1,
            "b2_riboflavin_mg": 0.1,
            "b3_niacin_mg": 1.0,
            "b5_pantothenic_acid_mg": 0.5,
            "b6_pyridoxine_mg": 0.1,
            "b12_cobalamin_mcg": 0.5,
            "folate_mcg": 20.0,
            "vitamin_e_mg": 0.5,
            "vitamin_k_mcg": 5.0,
            "histidine_g": 0.3,
            "isoleucine_g": 0.5,
            "leucine_g": 0.8,
            "lysine_g": 0.7,
            "methionine_g": 0.2,
            "cysteine_g": 0.1,
            "phenylalanine_g": 0.4,
            "tyrosine_g": 0.3,
            "threonine_g": 0.4,
            "tryptophan_g": 0.1,
            "valine_g": 0.5,
        }
        enriched = {food: dummy_micros for food in foods}

        ref_ranges = load_reference_ranges(REF_RANGES_PATH)
        result = analyze(entries, enriched, ref_ranges)

        assert result["period"]["days"] > 0
        assert result["coverage"]["mapped_entries"] > 0
        assert "vitamin_a_mcg" in result["nutrients"]

        # Terminal report should not error
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        print_terminal_report(result, settings)

        # HTML report should generate
        html_path = str(tmp_path / "test_report.html")
        generate_html_report(result, html_path)
        assert os.path.exists(html_path)
