# Omni Pilot

Omni Pilot is an intelligent nutrition and workout analysis application. The first module, **Micronutrient Analysis (v1)**, analyzes your daily micronutrient intake from MacroFactor food logs, compares it against DGE/NIH/WHO reference values, and surfaces deficiencies or excesses.

MacroFactor tracks calories and macros well, but its micronutrient tracking can be incomplete. Omni Pilot bridges this gap by automatically translating and cleaning messy food log strings with Google's **Gemini API**, querying the **USDA FoodData Central** database for complete vitamin, mineral, and amino acid profiles, and generating interactive visual reports on both desktop and mobile (**iPhone via a-Shell**).

> **Not medical advice.** Omni Pilot estimates nutrient intake from food logs and public reference data, so its figures can be incomplete or wrong. Use it for general information only, and talk to a doctor or registered dietitian before changing your diet or supplements.

---

## Key Features

- **MacroFactor Log Parsing:** Reads food logs directly from `.xlsx` exports.
- **Smart AI Food Translation (Gemini API):** Automatically translates messy German or branded food entries into clean, USDA-searchable generic terms (e.g., `"Bio Haferflocken 500g"` → `"cereals, oats, regular and quick, not fortified, dry"`, `"Burger Cheese"` → `"Cheese"`).
- **USDA FoodData Central Enrichment:** Fetches complete micronutrient and essential amino acid profiles.
- **Local TinyDB Cache:** Caches translations and USDA food profiles in `db/food_db.json` to prevent redundant network calls.
- **Shared S3 Storage:** The food database, mappings, custom recipes and every analysed export live in a private S3 bucket, so the Mac, the iPhone and cloud sessions all work on the same data. Requests are signed by hand (AWS Signature Version 4), so no AWS SDK is needed and it still runs in a-Shell.
- **Reference Range Comparison:** Evaluates average daily intake against configurable reference values in `config/reference_ranges.yaml`. Targets come from the German DGE/ÖGE reference values (3rd edition, 2025) for men aged 25 to under 51; where DGE gives no value (choline, omega-3 ALA), the NIH value is used, and EPA + DHA keeps the widely recommended 250 mg. DGE sets no upper limits, so every upper limit is NIH's. Essential amino acid targets follow FAO/WHO/UNU (2007), per kg of body weight.
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

With S3 configured, `analyze` first pulls the database, mappings and custom recipes from S3, and after the reports uploads the export it analysed and pushes the files that changed.

1. **Parser:** Extracts food log entries, daily totals, and food weights from MacroFactor `.xlsx`.
2. **Gemini Translator:** Automatically cleans and translates new food entries on the fly, saving mappings to TinyDB and `config/food_mappings.yaml`.
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

### 4. Connect the S3 Storage (optional)

Without an `s3` section the app works on the local files only and says so (`S3: not configured — local files only`). To share the data between devices, uncomment the `s3` section in `config/settings.yaml` and fill in the access key of the `omni-pilot-app` user (see [Deploying the Bucket](#deploying-the-bucket)):

```yaml
s3:
  bucket: "omni-pilot-data"
  region: "eu-central-1"
  access_key_id: "YOUR_ACCESS_KEY_ID"
  secret_access_key: "YOUR_SECRET_ACCESS_KEY"
```

Each value can also come from an environment variable, which wins over `settings.yaml`: `OMNI_PILOT_S3_BUCKET`, `OMNI_PILOT_S3_REGION`, `OMNI_PILOT_S3_ACCESS_KEY_ID`, `OMNI_PILOT_S3_SECRET_ACCESS_KEY`. This is meant for Claude cloud sessions, which start from a fresh clone with no `settings.yaml` of their own. If only some of the values are set, every command stops and names the missing one rather than quietly falling back to local files.

Then fetch the data once:

```bash
make pull
```

For a new, empty bucket, run `make push` once from the device that has the data (with no sync state it uploads every file as new) before `make pull` on the others.

---

## Desktop Usage

### Run Analysis & Generate Report
A single autonomous command parses your log, translates any new foods with Gemini, enriches them with USDA data, and generates terminal/HTML reports:

```bash
make analyze FILE=data/MacroFactor-Export.xlsx
# or
PYTHONPATH=src uv run python -m omni_pilot.cli analyze "data/MacroFactor-Export.xlsx" --html
```

With S3 configured this also pulls before it starts and saves to S3 when it is done (see [Sharing Data Across Devices](#sharing-data-across-devices-s3)).

### Pull and Push by Hand

```bash
make pull   # download the data files and any MacroFactor exports missing from data/
make push   # upload the data files that changed since the last pull
# upload an export first, then the data files:
PYTHONPATH=src uv run python -m omni_pilot.cli push --export data/MacroFactor-Export.xlsx
```

Use these around hand edits of `config/custom_foods.yaml`: pull, edit, push. The next pull overwrites an edit that was not pushed. `config/food_mappings.yaml` is written by the app and hand edits to it are overwritten; change a food's lookup text with `make update-mapping` instead (below).

### Change a Food's Lookup Text

Each logged food has a lookup text: the English, USDA-searchable name Gemini wrote for it, or `skip`. When Gemini got it wrong, or a food should not count (a supplement already in `supplements.yaml`), set it with:

```bash
make update-mapping FOOD="Haferflocken" QUERY="cereals, oats, dry"
make update-mapping FOOD="Magnesium Kapsel" QUERY=skip
# without make (iPhone / a-Shell):
python -m omni_pilot.cli update-mapping "Haferflocken" "cereals, oats, dry"
```

The food must be one you logged (the name exactly as in MacroFactor; a near miss is refused with suggestions). The command writes the database and `config/food_mappings.yaml`, pulls from S3 before and pushes after when S3 is configured, and takes effect on the next `analyze`. A custom recipe for the food still takes precedence.

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
python -m omni_pilot.cli analyze "data/data.xlsx" --html
view reports/latest.html
```

*(The `view reports/latest.html` command uses iOS QuickLook to instantly pop up the interactive HTML report on your screen).*

With the `s3` section in the iPhone's `settings.yaml`, `analyze` pulls the latest data from S3 first and saves its results there afterwards, so the iPhone needs a network connection.

---

### C. Automated iOS Shortcut

Install the preconfigured **Omni Pilot** Shortcut to automate the entire analysis and preview workflow directly from your iOS Share Sheet:

- **iCloud Link:** [Install Omni Pilot Shortcut](https://www.icloud.com/shortcuts/74960fb7132a438fb2cdec58b9ac8439)
- **Local File:** [`shortcuts/Omni-Pilot.shortcut`](shortcuts/Omni-Pilot.shortcut)

---

## Sharing Data Across Devices (S3)

The S3 bucket holds the one shared copy of `food_db.json`, `food_mappings.yaml`, `custom_foods.yaml` and the MacroFactor exports. Each device keeps a local working copy at `database_path`, `mappings_path` and `custom_foods_path` (leave them at the defaults). iCloud Drive only carries the code to the iPhone (`make sync-iphone`).

- **Pull: S3 always wins.** A pull replaces the local files with the ones in S3, without checking for local changes that were never pushed. A local file that was never in S3 is kept, and the next push uploads it.
- **Push never overwrites a newer file.** Each upload is conditional on the file in S3 still being the version that was pulled. If another device (or a daily routine) changed it meanwhile, the push is refused and nothing after it is uploaded. After `analyze`, run it again: it pulls the new version first. After `make push`, run `make pull` (it replaces your local changes), redo them, then push again.
- **`analyze` saves its results.** After the reports, it uploads the analysed export (named `exports/<first date>_<last date>_<hash>.xlsx`, so the same file is stored once) and then the changed data files. If that fails, the reports are still written, a red `Results were not saved to S3: … Run analyze again.` line follows, and the command exits with code 1. If the pull at the start fails, nothing runs and no report is written.
- The bucket keeps old versions of every file for 30 days after they are replaced, so a bad push can be undone in the AWS console.

---

## Deploying the Bucket

The bucket (`omni-pilot-data`, `eu-central-1`, private, versioned) and the `omni-pilot-app` IAM user (read, write and list on that bucket only, no delete) are defined with AWS CDK in `infra/`. You need the AWS CLI with a profile for your account and the `cdk` CLI (`npm install -g aws-cdk`, at least as new as the `aws-cdk-lib` in `uv.lock`).

1. Once per account and region, bootstrap CDK:
   ```bash
   cdk bootstrap aws://<account-id>/eu-central-1
   ```
2. Review what will change:
   ```bash
   make infra-diff
   ```
3. Deploy:
   ```bash
   make infra-deploy
   ```
4. In the AWS console, open IAM → Users → `omni-pilot-app` → Security credentials and create one access key. Paste it into the `s3` section of `config/settings.yaml` on the Mac and on the iPhone (and into the `OMNI_PILOT_S3_*` variables of a cloud environment). The stack deliberately creates no key, so the secret never lands in CloudFormation.

Destroying the stack keeps the bucket and its data.

---

## Development & Testing

- **Run unit and integration tests:**
  ```bash
  make test
  # or
  uv run pytest
  ```
- **Run the AWS stack tests** (need the `infra` dependency group):
  ```bash
  uv run --group infra pytest infra/tests
  ```
- **Recompile requirements:**
  ```bash
  uv pip compile pyproject.toml -o requirements.txt
  ```

---

## License

Released under the [MIT License](LICENSE).
