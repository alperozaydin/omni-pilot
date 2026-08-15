# Omni Pilot — Supplement Intake Feature

## Purpose
Include daily supplement intake (e.g. multivitamin, omega-3, vitamin D) in the daily averages so the micronutrient analysis accurately reflects total nutrient intake, not just food intake.

## Architecture
- Supplements are defined in a new configuration file `config/supplements.yaml`.
- The configuration structure maps system nutrient keys directly to daily amounts.
- The `analyzer.py` module explicitly adds these fixed amounts to the food-derived daily average before assessing nutrient status against WHO/NIH reference ranges.
- The reporter implicitly includes these amounts as part of the "Daily Avg" output (no separate breakdown).

## Key Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config Location | `config/supplements.yaml` | Separates personal nutritional data from application settings. |
| Units | Matches system units (mcg, mg, g) | Keeps the implementation simple without complex unit conversions like IU. |
| Report Display | Implicit addition | Focuses on the user's primary goal: "Am I hitting my targets overall?" |

## Module Changes
1. **`config/supplements.yaml`** (New File):
   ```yaml
   # Daily supplement intake matching system units (mcg, mg, g)
   # e.g., vitamin_d_mcg: 25.0
   ```

2. **`src/omni_pilot/config.py`**: 
   - Add `load_supplements(path: str) -> dict`. 
   - Return `{}` if the file does not exist to prevent errors for users who do not use supplements.

3. **`src/omni_pilot/analyzer.py`**: 
   - Update `analyze` function signature to accept `supplements: dict`. 
   - During the calculation, add `supplements.get(nutrient_key, 0.0)` to the final `daily_avg` for each nutrient.

4. **`src/omni_pilot/cli.py`**: 
   - Call `load_supplements("config/supplements.yaml")`.
   - Pass the loaded supplements to the analyzer.
