.PHONY: help analyze test lint lint-fix sync-iphone infra-diff infra-deploy

ICLOUD_DEST := $(HOME)/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot

help:
	@echo "Available commands:"
	@echo "  make analyze FILE=<path> - Analyze micronutrient intake (with HTML report)"
	@echo "  make test                - Run tests with pytest"
	@echo "  make lint                - Lint code with ruff"
	@echo "  make lint-fix            - Lint and auto-fix issues with ruff"
	@echo "  make sync-iphone         - Sync repo code to iCloud for the a-Shell (iPhone) copy"
	@echo "  make infra-diff          - Show pending changes to the AWS stack (cdk diff)"
	@echo "  make infra-deploy        - Deploy the AWS stack (cdk deploy)"

analyze:
ifndef FILE
	$(error FILE is not set. Usage: make analyze FILE=data/your-export.xlsx)
endif
	@test -f "$(FILE)" || (echo "Error: File '$(FILE)' does not exist." && exit 1)
	PYTHONPATH=src uv run python -m omni_pilot.cli analyze "$(FILE)" --html

test:
	uv run pytest

lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

infra-diff:
	cd infra && cdk diff

infra-deploy:
	cd infra && cdk deploy

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
		--exclude='infra/' \
		--exclude='db/' \
		--exclude='config/settings.yaml' \
		--exclude='config/food_mappings.yaml' \
		--exclude='config/supplements.yaml' \
		--exclude='config/custom_foods.yaml' \
		./ "$(ICLOUD_DEST)/"
