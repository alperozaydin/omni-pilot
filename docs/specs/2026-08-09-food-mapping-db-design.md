# Food Mapping Database Design

## Objective
Currently, `food_mappings.yaml` is regenerated on every `make import`, and only foods from the *new* Excel file are preserved. If a user previously translated a food but didn't eat it in the current log, the translation is lost.
This design introduces a central translation database so that once a German food is mapped to English, the system remembers it permanently across all future imports.

## Architecture & Storage
1. **Database:** We will utilize the existing `db/food_db.json` TinyDB database. 
2. **Table:** We will create a new table named `translations` to store the German -> English mappings. This acts as the permanent source of truth.

## Workflow

### 1. `make import` (The "Read" Phase)
- `parser.py` parses the new `.xlsx` file and extracts all unique foods.
- `cli.py` opens `db/food_db.json` and fetches all known translations from the `translations` table.
- `generate_food_mappings()` is updated to pre-fill known translations from the database.
- `config/food_mappings.yaml` is generated containing **all** foods found in the current `.xlsx` log. 
  - If a food is in the `translations` table, its English equivalent is pre-filled.
  - If a food is unknown, its value is left blank (`""`) for the user to fill out.

### 2. `make analyze` (The "Write" Phase)
- After the user has filled in the missing blanks in `config/food_mappings.yaml`, they run `make analyze`.
- `cli.py` reads the updated `config/food_mappings.yaml`.
- **New Step:** Before proceeding to USDA lookup, `cli.py` writes/updates all non-blank mappings from the YAML into the `translations` TinyDB table. This permanently expands the database with the user's new inputs.
- The pipeline then proceeds normally (enrichment and analysis).

## Spec Self-Review
- [x] Placeholders: None.
- [x] Internal Consistency: The architecture matches the feature descriptions. 
- [x] Scope check: Scope is narrow and well-defined (update CLI to read/write translations table).
- [x] Ambiguity check: Clearly defined that all foods from current log will appear in YAML, and non-blank items will be saved back.
