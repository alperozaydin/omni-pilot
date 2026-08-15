# Omni Pilot

Omni Pilot is an intelligent nutrition and workout analysis application. The first module, **Micronutrient Analysis (v1)**, analyzes your daily micronutrient intake from MacroFactor food logs, compares it against WHO/NIH recommended ranges, and surfaces deficiencies or excesses.

MacroFactor tracks calories and macros well, but its micronutrient tracking can be incomplete. Omni Pilot bridges this gap by automatically translating and cleaning messy food log strings with Google's **Gemini API**, querying the **USDA FoodData Central** database for complete vitamin, mineral, and amino acid profiles, and generating interactive visual reports on both desktop and mobile (**iPhone via a-Shell**).

---

## Key Features

- **MacroFactor Log Parsing:** Reads food logs directly from `.xlsx` exports.
- **Smart AI Food Translation (Gemini API):** Automatically translates messy German or branded food entries into clean, USDA-searchable generic terms (e.g., `"Bio Haferflocken 500g"` → `"Oats"`, `"Burger Cheese"` → `"Cheese"`).
- **USDA FoodData Central Enrichment:** Fetches complete micronutrient and essential amino acid profiles.
- **Local TinyDB Cache:** Caches translations and USDA food profiles in `db/food_db.json` to prevent redundant network calls and enable offline analysis.
- **Reference Range Comparison:** Evaluates average daily intake against configurable NIH/WHO reference ranges.
- **Supplements Support:** Automatically adds daily supplement contributions (e.g., multivitamins, Omega-3) configured in `config/supplements.yaml`.
- **Dual Reporting:** Generates rich color-coded terminal reports and interactive, styled HTML reports (including a convenient `reports/latest.html` copy).
- **iPhone / a-Shell & Shortcuts Ready:** 100% pure Python dependencies with zero C/Rust build requirements, designed to run smoothly on iOS inside **a-Shell** and integrate into **iOS Shortcuts**.

---

## Architecture

```text
MacroFactor .xlsx
       │
       ▼
┌─────────────┐     ┌──────────────────────┐     ┌──────────────┐
│   Parser    │────▶│ Gemini AI Translator │────▶│  TinyDB      │
│(xlsx → dict)│     │(Clean & Map Terms)   │     │(food_db.json)│
└─────────────┘     └──────────────────────┘     └──────┬───────┘
                                                        │
                    ┌──────────────────────┐            │
                    │ USDA FoodData API    │◀───────────┤
                    │ (Nutrient Enrichment)│            │
                    └──────────┬───────────┘            │
                               │                        ▼
                               │                 ┌──────────────┐
                               └────────────────▶│   Analyzer   │
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

1. **Parser:** Extracts food log entries, daily totals, and food weights from MacroFactor `.xlsx`.
2. **Gemini Translator:** Automatically cleans and maps new food entries in a single batch REST request, saving mappings to TinyDB and `config/food_mappings.yaml`.
3. **USDA Enricher:** Looks up mapped generic names in the USDA database and caches nutrient data in `db/food_db.json`.
4. **Analyzer:** Computes daily micronutrient averages, merges daily supplements, and evaluates intake against targets.
5. **Reporter:** Renders color-coded status tables (🟢 OK, 🟡 Low, 🔴 Deficient, 🟠 High) in terminal and exports standalone HTML reports.

---

## Setup & Configuration

### 1. Install Dependencies (Mac / Linux)

This project uses `uv` for fast dependency management:

```bash
git clone <repository-url>
cd omni-pilot
uv sync
```

### 2. Configure Configuration Files

Create your local settings and mapping files from the templates:

```bash
cp config/settings.example.yaml config/settings.yaml
cp config/supplements.example.yaml config/supplements.yaml
cp config/food_mappings.example.yaml config/food_mappings.yaml
```

### 3. Set Up API Keys in `config/settings.yaml`

Open `config/settings.yaml` and add your API keys:

```yaml
# USDA FoodData Central API key (Required)
# Get a free key at: https://fdc.nal.usda.gov/api-key-signup.html
usda_api_key: "YOUR_USDA_API_KEY"

# Gemini API key for smart food translation (Recommended)
# Get a free developer key at: https://aistudio.google.com/
gemini_api_key: "YOUR_GEMINI_API_KEY"
gemini_model: "gemini-flash-latest"  # default model

# Output preferences
output:
  show_amino_acids: true
  show_ok_nutrients: true
```

---

## Desktop Usage

### 1. Import Food Log
Imports your MacroFactor export and uses Gemini to automatically translate and map new foods:

```bash
make import FILE=data/MacroFactor-Export.xlsx
# or
PYTHONPATH=src uv run python -m omni_pilot.cli import "data/MacroFactor-Export.xlsx"
```

### 2. Run Analysis & Generate Report
Fetches USDA micronutrients, calculates daily intake vs targets, and generates an HTML report:

```bash
make analyze FILE=data/MacroFactor-Export.xlsx
# or
PYTHONPATH=src uv run python -m omni_pilot.cli analyze "data/MacroFactor-Export.xlsx" --html
```

---

## Running on iPhone with a-Shell & iOS Shortcuts

Omni Pilot is built with **100% pure Python dependencies** so it can run entirely on your iPhone inside **a-Shell** and trigger automatically via **iOS Shortcuts**.

### A. Initial One-Time Setup in a-Shell

1. Open the **a-Shell** app on your iPhone.
2. Bookmark your project folder synced via iCloud Drive:
   ```bash
   pickFolder
   ```
   Select your `omni-pilot` folder in iCloud Drive / Files.
3. Jump into the folder and install dependencies:
   ```bash
   jump omni-pilot
   pip install -r requirements.txt
   ```

---

### B. Daily Usage in a-Shell

Whenever you want to run Omni Pilot directly in a-Shell:

```bash
jump omni-pilot
export PYTHONPATH=src
python -m omni_pilot.cli import "data/data.xlsx"
python -m omni_pilot.cli analyze "data/data.xlsx" --html
view reports/latest.html
```

*(The `view reports/latest.html` command uses iOS QuickLook to instantly pop up the interactive HTML report on your screen).*

---

### C. Automated iOS Shortcut

Install the preconfigured **Omni Pilot** Shortcut to automate the entire import, analysis, and preview workflow directly from your iOS Share Sheet:

- **iCloud Link:** [Install Omni Pilot Shortcut](https://www.icloud.com/shortcuts/74960fb7132a438fb2cdec58b9ac8439)
- **Local File:** [`shortcuts/Omni-Pilot.shortcut`](shortcuts/Omni-Pilot.shortcut)

---

## Development & Testing

- **Run unit and integration tests:**
  ```bash
  make test
  # or
  uv run pytest
  ```
- **Recompile requirements:**
  ```bash
  uv pip compile pyproject.toml -o requirements.txt
  ```
