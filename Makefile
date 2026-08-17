.PHONY: help analyze test

help:
	@echo "Available commands:"
	@echo "  make analyze FILE=<path> - Analyze micronutrient intake (with HTML report)"
	@echo "  make test                - Run tests with pytest"

analyze:
ifndef FILE
	$(error FILE is not set. Usage: make analyze FILE=data/your-export.xlsx)
endif
	@test -f "$(FILE)" || (echo "Error: File '$(FILE)' does not exist." && exit 1)
	PYTHONPATH=src uv run python -m omni_pilot.cli analyze "$(FILE)" --html

test:
	uv run pytest
