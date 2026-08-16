.PHONY: help import analyze test

help:
	@echo "Available commands:"
	@echo "  make import FILE=<path>  - Import MacroFactor xlsx and generate food mappings"
	@echo "  make analyze FILE=<path> - Analyze micronutrient intake (with HTML report)"
	@echo "  make test                - Run tests with pytest"

import:
ifndef FILE
	$(error FILE is not set. Usage: make import FILE=data/your-export.xlsx)
endif
	@test -f "$(FILE)" || (echo "Error: File '$(FILE)' does not exist." && exit 1)
	PYTHONPATH=src uv run python -m omni_pilot.cli import "$(FILE)"

analyze:
ifndef FILE
	$(error FILE is not set. Usage: make analyze FILE=data/your-export.xlsx)
endif
	@test -f "$(FILE)" || (echo "Error: File '$(FILE)' does not exist." && exit 1)
	PYTHONPATH=src uv run python -m omni_pilot.cli analyze "$(FILE)" --html

test:
	uv run pytest

