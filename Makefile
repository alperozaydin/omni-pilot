.PHONY: help analyze pull push update-mapping test lint lint-fix sync-iphone infra-diff infra-deploy

ICLOUD_DEST := $(HOME)/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot

# A variable's text exactly as typed, single-quoted for the shell: $(value) keeps make from
# expanding $ in it, and each ' becomes '"'"'. Food names can hold quotes, colons and $.
shell_quote = '$(subst ','"'"',$(value $(1)))'

help:
	@echo "Available commands:"
	@echo "  make analyze FILE=<path> - Analyze micronutrient intake (with HTML report)"
	@echo "  make pull                - Download data files and MacroFactor exports from S3"
	@echo "  make push                - Upload changed data files to S3"
	@echo "  make update-mapping FOOD=<name> QUERY=<text> - Set a food's USDA lookup text (\"skip\" excludes it)"
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

pull:
	PYTHONPATH=src uv run python -m omni_pilot.cli pull --exports

push:
	PYTHONPATH=src uv run python -m omni_pilot.cli push

update-mapping:
ifndef FOOD
	$(error FOOD is not set. Usage: make update-mapping FOOD="Haferflocken" QUERY="cereals, oats, dry")
endif
ifndef QUERY
	$(error QUERY is not set. Usage: make update-mapping FOOD="Haferflocken" QUERY="cereals, oats, dry")
endif
	PYTHONPATH=src uv run python -m omni_pilot.cli update-mapping $(call shell_quote,FOOD) $(call shell_quote,QUERY)

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
