# Technical Specification: iCloud Database Synchronization & Pure Configuration Architecture

**Document Version:** 1.0  
**Date:** 2026-08-16  
**Author:** Pair Programming Session (Antigravity & User)  
**Status:** Approved & Implemented  

---

## 1. Executive Summary & Objective

Omni Pilot is a nutrition and micronutrient analysis application designed to parse MacroFactor `.xlsx` food logs, translate and clean food strings using Google Gemini API, and enrich food entries with the USDA FoodData Central database.

### The Problem
Previously:
1. The food database (`db/food_db.json`) and mapping files (`config/food_mappings.yaml`) were isolated locally within each repository clone and gitignored for privacy.
2. Running Omni Pilot on an iPhone (via **a-Shell**) and on macOS (Desktop) caused translations and cached USDA micronutrient records to drift apart, requiring redundant API lookups on each device.
3. Path configuration was scattered across optional CLI flags (`--db`, `--mappings`, `--mappings-output`) and hardcoded relative paths.
4. Failed or unmapped Gemini translations previously populated `food_mappings.yaml` with empty string values (`""`), which led to invalid USDA queries.

### The Solution
1. **Private Cross-Device Cloud Sync:** Uses **Apple iCloud Drive** to share `food_db.json` and `food_mappings.yaml` across devices with zero external servers and zero recurring costs.
2. **Single Source of Truth (`settings.yaml`):** All storage paths (`database_path`, `mappings_path`) are centralized in `config/settings.yaml`, supporting `~` user home directory and environment variable expansion.
3. **Streamlined CLI Interface:** Removed redundant `--db`, `--mappings`, and `--mappings-output` flags.
4. **Strict Translation Integrity:** 
   - `generate_food_mappings` only persists valid, non-empty mappings.
   - If Gemini translation fails or returns an unexpected count, `cmd_import` terminates immediately with `sys.exit(1)` to prevent saving partial or corrupted records.

---

## 2. Technical Journey & Architectural Decisions

| Iteration | Proposed Architecture | Decision / Outcome | Rationale |
| :--- | :--- | :--- | :--- |
| **v1 (Initial Proposal)** | External Cloud DB (Supabase / Upstash KV) | ❌ Rejected | Adds external infrastructure, API keys, and third-party privacy overhead. |
| **v2 (Attempted)** | Hardcoded macOS iCloud auto-detection in Python code | ❌ Rejected | Violates clean code principles by baking platform/user-specific path strings into source code. |
| **v3 (Attempted)** | Multi-tier precedence: CLI flag ➔ `settings.yaml` ➔ local fallback + auto-copy bootstrap | ❌ Rejected | Over-complicated; duplicated path configuration across multiple layers. |
| **v4 (Final Implementation)** | **Pure Configuration-Driven (`settings.yaml`) with iCloud Drive** | ✅ **Approved & Implemented** | `settings.yaml` is the single source of truth; zero hardcoded paths; clean CLI; 100% portable. |

---

## 3. System Architecture & Data Flow

```text
 macOS Desktop (~/Projects/omni-pilot)                       iPhone (a-Shell)
┌──────────────────────────────────────────────┐       ┌─────────────────────────────────────┐
│ Local config/settings.yaml:                  │       │ a-Shell (after 'jump omni-pilot'):  │
│ database_path:                               │       │ Running directly inside             │
│   ~/Library/Mobile Documents/                │       │ iCloud Drive / Shortcuts/omni-pilot │
│     iCloud~is~workflow~my~workflows/         │       │                                     │
│     Documents/omni-pilot/db/food_db.json     │       │ Local config/settings.yaml:         │
│ mappings_path:                               │       │ database_path: "db/food_db.json"    │
│   ~/Library/Mobile Documents/.../            │       │ mappings_path:                      │
│     config/food_mappings.yaml                │       │   "config/food_mappings.yaml"       │
└──────────────────────┬───────────────────────┘       └──────────────────┬──────────────────┘
                       │                                                  │
                       │           Apple iCloud Drive Sync                │
                       ▼                                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                    iCloud Drive / Shortcuts / omni-pilot /                                 │
│                                                                                            │
│   • db/food_db.json (TinyDB: translations + USDA micronutrient cache)                      │
│   • config/food_mappings.yaml (Clean generic food mapping overrides)                       │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Details & Implementation

### A. Path Resolution (`src/omni_pilot/config.py`)
`resolve_path` takes the setting key and default path, reading from the loaded `settings` dictionary:
```python
def resolve_path(
    setting_key: str,
    default_path: str,
    settings: dict | None = None,
) -> str:
    """Resolve file path from settings dict with ~ and env expansion, falling back to default_path."""
    raw_path = settings.get(setting_key, default_path) if settings else default_path
    resolved = os.path.expanduser(os.path.expandvars(str(raw_path)))
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return resolved
```

### B. Strict Mapping Persistence (`src/omni_pilot/parser.py`)
`generate_food_mappings` only saves foods with non-empty string mappings:
```python
def generate_food_mappings(
    foods: list[str],
    existing_mappings: dict[str, str],
    output_path: str,
) -> None:
    """Generate or merge food_mappings.yaml. Only non-empty mappings are saved."""
    merged: dict[str, str] = {}
    for food in sorted(foods):
        if food in existing_mappings and existing_mappings[food] and str(existing_mappings[food]).strip():
            merged[food] = str(existing_mappings[food]).strip()

    for food, mapping in existing_mappings.items():
        if food not in merged and mapping and str(mapping).strip():
            merged[food] = str(mapping).strip()

    data = {"mappings": merged}
    ...
```

### C. CLI Error Exit Policy (`src/omni_pilot/cli.py`)
If Gemini translation encounters any error or length mismatch, `cmd_import` terminates with exit code 1:
```python
if len(translations) == len(new_foods):
    print("  Gemini translation successful! Saving to database...")
    for german_food, english_food in zip(new_foods, translations):
        if english_food and english_food.strip() and english_food != "ERROR":
            known_mappings[german_food] = english_food.strip()
            translations_table.upsert(
                {"german": german_food, "english": english_food.strip()},
                Query().german == german_food
            )
else:
    print("Error: Gemini returned a different number of translations than expected.")
    sys.exit(1)
except Exception as e:
    print(f"Error: Gemini translation failed: {e}")
    sys.exit(1)
```

---

## 5. Configuration Reference

### `config/settings.example.yaml` (Template in Git)
```yaml
# USDA FoodData Central API key
usda_api_key: "YOUR_USDA_API_KEY_HERE"

# Output preferences
output:
  show_amino_acids: true
  show_ok_nutrients: true

# Gemini API configuration for smart food translation (Optional but recommended)
gemini_api_key: "YOUR_GEMINI_API_KEY_HERE"
# gemini_model: "gemini-flash-latest"  # default: gemini-flash-latest

# Storage paths (supports ~ and absolute/relative paths)
database_path: "db/food_db.json"
mappings_path: "config/food_mappings.yaml"
# Example for macOS iCloud sync:
# database_path: "~/Library/Mobile Documents/com~apple~CloudDocs/OmniPilot/db/food_db.json"
# mappings_path: "~/Library/Mobile Documents/com~apple~CloudDocs/OmniPilot/config/food_mappings.yaml"
```

### `config/settings.yaml` (Private macOS Active Settings)
```yaml
usda_api_key: "..."
gemini_api_key: "..."

# Storage paths for macOS iCloud synchronization
database_path: "~/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot/db/food_db.json"
mappings_path: "~/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot/config/food_mappings.yaml"
```

---

## 6. Verification & Automated Test Suite

Full automated test suite with 63 test cases covering:
1. **Path Resolution:** Tilde expansion, environment variable substitution, fallback defaults.
2. **CLI Import & Analyze:** Path loading via `settings.yaml`, TinyDB translation caching, and USDA resolution.
3. **Translation Error Resilience:** `SystemExit(code=1)` on Gemini network failure and count mismatch.
4. **Mapping Integrity:** Verified that empty strings are never written to `food_mappings.yaml`.

```bash
uv run pytest
```
```text
============================== 63 passed in 0.68s ==============================
```
