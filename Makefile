.PHONY: help analyze test sync-iphone

ICLOUD_DEST := $(HOME)/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot

help:
	@echo "Available commands:"
	@echo "  make analyze FILE=<path> - Analyze micronutrient intake (with HTML report)"
	@echo "  make test                - Run tests with pytest"
	@echo "  make sync-iphone         - Sync repo code to iCloud for the a-Shell (iPhone) copy"

analyze:
ifndef FILE
	$(error FILE is not set. Usage: make analyze FILE=data/your-export.xlsx)
endif
	@test -f "$(FILE)" || (echo "Error: File '$(FILE)' does not exist." && exit 1)
	PYTHONPATH=src uv run python -m omni_pilot.cli analyze "$(FILE)" --html

test:
	uv run pytest

sync-iphone:
	uv pip compile pyproject.toml -o requirements.txt
	rsync -av --delete \
		--exclude='.git/' \
		--exclude='.venv/' \
		--exclude='__pycache__/' \
		--exclude='*.pyc' \
		--exclude='.DS_Store' \
		--exclude='reports/' \
		--exclude='.worktrees/' \
		--exclude='data/' \
		--exclude='db/' \
		--exclude='config/settings.yaml' \
		--exclude='config/food_mappings.yaml' \
		--exclude='config/supplements.yaml' \
		./ "$(ICLOUD_DEST)/"
