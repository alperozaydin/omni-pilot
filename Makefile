.PHONY: help import analyze test

# Default file for import and analysis (can be overridden via make import FILE=...)
FILE ?= data/MacroFactor-example.xlsx

help:
	@echo "Available commands:"
	@echo "  make import [FILE=...]  - Import MacroFactor xlsx and generate food mappings"
	@echo "  make analyze [FILE=...] - Analyze micronutrient intake (with HTML report)"
	@echo "  make test               - Run tests with pytest"

import:
	PYTHONPATH=src uv run python -m omni_pilot.cli import "$(FILE)"

analyze:
	PYTHONPATH=src uv run python -m omni_pilot.cli analyze "$(FILE)" --html

test:
	uv run pytest
