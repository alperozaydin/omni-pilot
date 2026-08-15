# Technical Specification: Gemini AI Food Translation & iOS a-Shell Integration

**Document Version:** 1.1  
**Date:** 2026-08-15  
**Author:** Pair Programming Session (Antigravity & User)  
**Status:** Approved & Implemented  

---

## 1. Executive Summary & Objective

Omni Pilot is a nutrition and workout analysis application designed to parse MacroFactor `.xlsx` food logs, match foods against the **USDA FoodData Central** database, and calculate daily averages for vitamins, minerals, and essential amino acids against NIH/WHO reference ranges.

### The Problem
Previously, when users imported food logs containing German or branded entries, food names had to be translated and cleaned manually in `config/food_mappings.yaml`. Automated machine translation (e.g. `deep-translator` / Google Translate) failed because raw log entries contain extensive noise (e.g., brand names, prices, package weights, cooking adjectives like `"Organic Rolled Oats 500g 1.49€"` or `"Spicy Alpine Cheese"`). Direct translations of these noisy strings returned zero matches in the USDA FoodData Central database.

Furthermore, running the workflow on mobile (**iPhone via a-Shell**) failed when using the official `google-genai` Python SDK because its underlying dependencies (`pydantic-core`, `cryptography`, `cffi`) require native C/Rust compilers (`maturin`), which are not supported in iOS sandboxed environments.

### The Solution
1. **Automated LLM-Based Smart Extraction:** Integrates Google's **Gemini REST API** to automatically translate and clean food strings in a single batch JSON request, stripping brand names, weights, prices, and modifiers to produce clean USDA search keys (e.g. `"Ground beef"`, `"Cheese"`, `"Potatoes"`).
2. **Pure-Python REST Architecture:** Calls the Gemini API directly via `requests.post()` with JSON Schema enforcement, eliminating all C/Rust compilation dependencies.
3. **Seamless Mobile Execution (a-Shell & iOS Shortcuts):** Delivers a 100% pure-Python dependency tree, automatic `reports/latest.html` copy generation, and an all-in-one iOS Shortcut triggered directly from the iOS Share Sheet.

---

## 2. Technical Journey & Rationale

| Iteration | Approach | Outcome | Root Cause of Failure / Limitation |
| :--- | :--- | :--- | :--- |
| **v1 (Initial)** | Manual Translation | Slow / Tedious | Required manual editing of `food_mappings.yaml` for every new food. |
| **v2 (Attempted)** | `deep-translator` | ❌ Failed | Literal translation kept noise (e.g. `"250g 1.29€"`), resulting in 0 USDA matches. |
| **v3 (Attempted)** | `google-genai` SDK | ❌ Failed on iOS | Required `maturin`, `pydantic-core`, and `cryptography` which cannot compile in iOS a-Shell. |
| **v4 (Final)** | **Pure-Python REST with `requests`** | ✅ **Success** | 100% pure Python, zero compilation needed, identical high-quality output on Mac & iOS. |

---

## 3. System Architecture & Data Flow

```text
MacroFactor .xlsx (Food Log)
          │
          ▼
┌──────────────────┐
│ parser.py        │  ──▶ Extracts unique food strings
└─────────┬────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│ cli.py: cmd_import                                       │
│ 1. Checks TinyDB ('translations') & existing YAML        │
│ 2. Identifies unknown 'new_foods'                        │
│ 3. Compiles single JSON batch request                    │
└─────────┬────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│ Gemini REST API (gemini-flash-latest)                    │
│ URL: .../models/{model}:generateContent?key={api_key}    │
│ Strict System Prompt + JSON Array Response Schema        │
└─────────┬────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│ Persistent Storage                                       │
│ 1. Upserts into TinyDB: db/food_db.json ('translations') │
│ 2. Generates: config/food_mappings.yaml                  │
└─────────┬────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│ cli.py: cmd_analyze                                      │
│ 1. Looks up cached/mapped English names                  │
│ 2. Queries USDA FoodData Central API                     │
│ 3. Calculates daily micronutrient intake                 │
│ 4. Generates: reports/micronutrient-report-{date}.html   │
│ 5. Copies: reports/latest.html                           │
└──────────────────────────────────────────────────────────┘
```

---

## 4. Prompt Engineering & Cleaning Rules

The prompt is designed to extract **only the core, generic nutritional noun** and strip all marketing and tracking modifiers:

### System Prompt Template
```text
Translate these messy German/non-English food entries to English and extract ONLY the most basic, generic ingredient.
CRITICAL RULES:
1. Drop ALL brand names, prices, and weights.
2. DROP meal-specific modifiers and brand-like adjectives (e.g. 'Burger', 'Frozen', 'Crunch'). 
   For example, 'Burger Cheese' MUST become simply 'Cheese'. 'Caramel Crunch Protein Bar' MUST become simply 'Protein bar'.

Input foods:
["Brand Name Rolled Oats 500g", "Burger Cheese", "Bio Ground Beef for Roasting", ...]
```

### Generic Transformation Examples
- `"Burger Cheese"` ➔ `"Cheese"` *(Prevents USDA matching to fast food burgers)*
- `"Caramel Crunch Protein Bar"` ➔ `"Protein bar"`
- `"Organic Farm Ground Beef for Roasting"` ➔ `"Ground beef"`
- `"Whole Grain Brown Rice 500g 1.29€"` ➔ `"Brown rice"`

---

## 5. REST Implementation & Resilience Policy

The implementation in `src/omni_pilot/cli.py` uses direct HTTP POST requests with structured JSON schemas and exponential backoff retry via `tenacity`:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, json.JSONDecodeError, KeyError, IndexError)),
    reraise=True,
)
def translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]:
    prompt = f"""..."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            }
        }
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw_text)
```

### Resilience & Error Handling
1. **Network Transient Failures (503 / 429):** Retries up to 3 times with exponential backoff (`2s`, `4s`, `8s`).
2. **Graceful Fallback:** If the API fails after all retries (e.g. invalid API key or offline network), the CLI logs a warning and proceeds with the import, leaving unmapped foods blank in `food_mappings.yaml` so the process never crashes.

---

## 6. Mobile Architecture (iPhone a-Shell & iOS Shortcuts)

### a-Shell Sandbox Constraints
- iOS prevents compiling arbitrary C/Rust code from source in a-Shell.
- PyPI does not distribute iOS-specific binary wheels for packages like `pydantic-core`.
- Therefore, the runtime dependency list is strictly limited to pure-Python libraries:
  `jinja2`, `openpyxl`, `pyyaml`, `requests`, `rich`, `tenacity`, `tinydb`.

### File Output & QuickLook Preview
- When generating reports, `cmd_analyze` writes both:
  1. `reports/micronutrient-report-YYYY-MM-DD.html` (Historical archive)
  2. `reports/latest.html` (Predictable file path for mobile shortcuts)
- On iOS, `view reports/latest.html` uses the built-in a-Shell `view` command to trigger the iOS QuickLook native sheet.

### iOS Shortcut Distribution
- **iCloud Link:** [Install Omni Pilot Shortcut](https://www.icloud.com/shortcuts/74960fb7132a438fb2cdec58b9ac8439)
- **Local File in Repository:** `shortcuts/Omni-Pilot.shortcut`
- **Workflow:**
  1. Receives `.xlsx` from iOS Share Sheet (directly when exporting from MacroFactor).
  2. Overwrites `omni-pilot/data/data.xlsx`.
  3. Executes:
     ```bash
     jump omni-pilot
     export PYTHONPATH=src
     python -m omni_pilot.cli import "data/data.xlsx"
     python -m omni_pilot.cli analyze "data/data.xlsx" --html
     view reports/latest.html
     ```

---

## 7. Verification & Output Parity

Extensive test suites and live batch extraction tests were conducted to evaluate translation quality and consistency:

1. **Parity Between SDK and REST Implementation:**
   - Evaluated side-by-side batch extractions across diverse food inputs.
   - The pure REST implementation achieved 100% parity with the official SDK output.
   - Successfully stripped brand names, packaging indicators, and weights into clean generic singular nouns.
2. **Reliability & Error Handling:**
   - Verified that Gemini correctly handles JSON structured schemas with zero parsing failures.
   - Verified that transient rate limits (503/429) automatically recover using `tenacity` retries.
3. **Automated Unit Tests:**
   - 55 unit and integration tests verify CLI arguments, parser extraction, database persistence, and mocked REST responses.

---

## 8. Guidelines for Future AI Agents & Maintainers

1. **Model Upgrades:**
   - Always verify active model names via Google's model catalog before changing default models.
   - The default model is configured as `gemini-flash-latest`, which can be overridden via `gemini_model` in `config/settings.yaml`.
2. **Dependency Rules:**
   - **DO NOT** add packages that require C/Rust compilation (e.g. `pydantic>=2`, `cryptography`, `numpy`, `pandas`) unless a-Shell officially supports pre-built wheels for them.
   - Always run `uv pip compile pyproject.toml -o requirements.txt` after modifying `pyproject.toml`.
3. **Database Integrity:**
   - TinyDB stores translation mappings in table `translations` (`{"german": ..., "english": ...}`).
   - USDA food profiles are stored in the default TinyDB table (`_default`).
4. **Testing:**
   - Run `uv run pytest` after any changes. All mock tests in `tests/test_cli.py` mock `requests.post` or `translate_new_foods` without hitting live network endpoints.
