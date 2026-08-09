# Omni Pilot

Omni Pilot is a nutrition and workout analysis application. The first module, **Micronutrient Analysis (v1)**, analyzes your daily micronutrient intake from MacroFactor food logs, compares it against WHO/NIH recommended ranges, and surfaces deficiencies or excesses. 

MacroFactor tracks calories and macros well, but its micronutrient tracking can be incomplete. Omni Pilot bridges this gap by using the USDA FoodData Central database to give you a complete picture of your vitamin, mineral, and essential amino acid intake on a daily average basis.

## Features
- Reads food logs from MacroFactor `.xlsx` exports.
- Uses the comprehensive USDA FoodData Central API for accurate micronutrient profiles.
- Caches food data locally in a TinyDB database to avoid redundant API calls.
- Compares your average daily intake against configurable reference ranges (NIH/WHO).
- Supports adding daily supplement intake (e.g., multivitamins, Omega-3).
- Generates a Rich CLI terminal report and an optional HTML report.

## Architecture

```text
MacroFactor .xlsx
       │
       ▼
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│   Parser    │────▶│    Enricher      │────▶│   TinyDB     │
│(xlsx → dict)│     │(USDA FoodData    │     │(food_db.json)│
│             │     │ Central API)     │     │              │
└─────────────┘     └──────────────────┘     └──────┬───────┘
                                                    │
                                                    ▼
                                             ┌──────────────┐
                                             │   Analyzer   │
                                             │(daily avg vs │
                                             │ ref ranges)  │
                                             └──────┬───────┘
                                                    │
                                             ┌──────┴───────┐
                                             ▼              ▼
                                       ┌──────────┐  ┌──────────┐
                                       │ Rich CLI │  │ HTML     │
                                       │ Report   │  │ Report   │
                                       └──────────┘  └──────────┘
```

1. **Parser**: Reads the MacroFactor `.xlsx` export ("Food Log" sheet) and extracts food entries, computing total weight.
2. **Enricher**: Looks up mapped foods in the USDA API and caches the micronutrient profile in `db/food_db.json`. 
3. **Analyzer**: Calculates the daily average micronutrient intake across all logged days, includes supplement amounts from `config/supplements.yaml`, and compares the totals against the reference ranges in `config/reference_ranges.yaml`.
4. **Reporter**: Displays a color-coded status summary (🟢 OK, 🟡 Low, 🔴 Deficient, 🟠 High) in the terminal and can export an HTML version.

## Workflow

The typical usage workflow involves three steps: **Import**, **Map**, and **Analyze**.

### 1. Import Data
First, import your MacroFactor export. This will parse your food logs and generate or update the `config/food_mappings.yaml` file with all unique foods found in your log.

```bash
make import FILE=data/MacroFactor-example.xlsx
```

### 2. Map Foods
Open `config/food_mappings.yaml`. You will need to map your logged foods to English, USDA-searchable equivalents. 
- For standard foods, provide the English generic equivalent (e.g., `"Haferflocken": "rolled oats"`).
- For items you want to exclude from micronutrient analysis (like "Quick Add" or "Dessert"), set the mapping to `"skip"`.
- If you take daily supplements, define them in `config/supplements.yaml`.

### 3. Analyze
Once foods are mapped, run the analysis command. This will fetch missing data from the USDA API, calculate your daily averages, and output the report.

```bash
make analyze FILE=data/MacroFactor-example.xlsx
```
*(The `make analyze` command includes the `--html` flag by default, which generates an HTML report in the `reports/` directory).*

## Development

The project uses `uv` for fast dependency and environment management.

- Run tests:
  ```bash
  make test
  ```
- Adding dependencies:
  ```bash
  uv add <package>
  ```
