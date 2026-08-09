# Food Mapping DB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist user-provided German-to-English food mappings across imports using a TinyDB `translations` table.

**Architecture:** We update `make import` to read known translations from TinyDB and pre-fill the YAML. We update `make analyze` to save any non-empty mappings from the YAML back into TinyDB.

**Tech Stack:** Python, TinyDB, PyYAML, argparse.

## Global Constraints

- No new dependencies.
- Follow existing formatting and style.

---

### Task 1: Update `cmd_import` to read from TinyDB

**Files:**
- Modify: `src/omni_pilot/cli.py`

**Interfaces:**
- Consumes: `db/food_db.json` via TinyDB.

- [ ] **Step 1: Add `--db` argument to import parser**

In `main()`, add `--db` argument to `import_parser` so the DB path can be customized, matching `analyze_parser`.
```python
    import_parser.add_argument("--db", default=None)
```

- [ ] **Step 2: Update `cmd_import` to read translations**

In `cmd_import(args: argparse.Namespace)`:
```python
    db_path = args.db or DEFAULT_DB
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    from tinydb import TinyDB, Query
    db = TinyDB(db_path)
    translations_table = db.table("translations")
    
    # Load db mappings
    db_mappings = {doc["german"]: doc["english"] for doc in translations_table.all()}
```

- [ ] **Step 3: Merge DB mappings with existing YAML mappings**

```python
    existing_mappings = load_food_mappings(mappings_output)
    
    # Merge mappings: DB takes precedence, then existing YAML
    known_mappings = {**existing_mappings, **db_mappings}
    
    # Generate/merge mappings using known_mappings
    generate_food_mappings(foods, known_mappings, mappings_output)
```

- [ ] **Step 4: Commit**
```bash
git add src/omni_pilot/cli.py
git commit -m "feat: read translations from TinyDB during import"
```

---

### Task 2: Update `cmd_analyze` to write to TinyDB

**Files:**
- Modify: `src/omni_pilot/cli.py`

**Interfaces:**
- Produces: Updates `translations` table in TinyDB.

- [ ] **Step 1: Write translations to TinyDB before enrichment**

In `cmd_analyze(args: argparse.Namespace)`, after validating `mappings` and initializing `db = TinyDB(db_path)`:
```python
    from tinydb import Query
    translations_table = db.table("translations")
    TranslationQuery = Query()
    
    # Save non-empty mappings to database
    new_translations_count = 0
    for german, english in mappings.items():
        if english and str(english).strip() and str(english).strip() != "skip":
            # Upsert into translations table
            translations_table.upsert(
                {"german": german, "english": str(english).strip()},
                TranslationQuery.german == german
            )
            new_translations_count += 1
            
    print(f"  Saved {new_translations_count} mappings to translations database.")
```
Wait, if it's "skip", should we save it? Yes, we want to remember "skip" as a valid translation so we don't have to skip it manually again! Let's just save whatever is non-empty.

```python
    from tinydb import Query
    translations_table = db.table("translations")
    TranslationQuery = Query()
    
    # Save all non-empty mappings to database
    saved_count = 0
    for german, english in mappings.items():
        if english and str(english).strip():
            translations_table.upsert(
                {"german": german, "english": str(english).strip()},
                TranslationQuery.german == german
            )
            saved_count += 1
            
    print(f"  Saved/Updated {saved_count} mappings in the database.")
```

- [ ] **Step 2: Commit**
```bash
git add src/omni_pilot/cli.py
git commit -m "feat: save translations to TinyDB during analyze"
```
