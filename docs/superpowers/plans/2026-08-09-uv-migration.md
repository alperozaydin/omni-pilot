# UV Package Manager Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the project from `pip`/`requirements.txt` to `uv`/`pyproject.toml` for dependency management.

**Architecture:** Use `uv` native commands (`init`, `add`, `sync`, `run`) to generate `pyproject.toml`, lock dependencies, recreate the virtual environment, and replace the legacy `requirements.txt` setup.

**Tech Stack:** `uv` (Rust-based Python package installer and resolver)

## Global Constraints

- Must retain all existing dependencies at their current version floors (e.g. `openpyxl>=3.1.0`).
- Must separate development dependencies (`pytest`) from runtime dependencies.
- Make `pyproject.toml` the sole source of truth.

---

### Task 1: Initialize Project Configuration

**Files:**
- Create: `pyproject.toml` (generated via uv)
- Create: `hello.py` (default from uv, to be deleted if not needed, but we'll let uv do its thing)

**Interfaces:**
- Consumes: Existing project structure (`src/`, `tests/`)
- Produces: Base `pyproject.toml`

- [ ] **Step 1: Initialize `pyproject.toml`**

Run: `uv init`
Expected: A `pyproject.toml` file is created in the project root.

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: initialize pyproject.toml with uv"
```

### Task 2: Migrate Dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `uv.lock`

**Interfaces:**
- Consumes: The list of dependencies from the spec document.
- Produces: Populated `pyproject.toml` with runtime and dev dependencies.

- [ ] **Step 1: Add runtime dependencies**

Run: `uv add "openpyxl>=3.1.0" "tinydb>=4.8.0" "requests>=2.31.0" "rich>=13.0.0" "pyyaml>=6.0" "jinja2>=3.1.0"`
Expected: `pyproject.toml` is updated with runtime dependencies and `uv.lock` is generated.

- [ ] **Step 2: Add development dependencies**

Run: `uv add --dev "pytest>=7.0.0"`
Expected: `pyproject.toml` is updated with a `dependency-groups.dev` section containing `pytest`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: migrate dependencies to pyproject.toml"
```

### Task 3: Environment Recreation and Cleanup

**Files:**
- Delete: `.venv` (directory)
- Delete: `requirements.txt`
- Modify: `.venv` (recreated by uv)

**Interfaces:**
- Consumes: `uv.lock`
- Produces: A clean, reproducible virtual environment.

- [ ] **Step 1: Remove old environment and requirements file**

Run: `rm -rf .venv && rm requirements.txt`
Expected: The directories and files are removed.

- [ ] **Step 2: Sync new environment**

Run: `uv sync`
Expected: A new `.venv` is created containing the locked dependencies.

- [ ] **Step 3: Commit**

```bash
git rm requirements.txt
git commit -m "build: remove legacy requirements.txt"
```

### Task 4: Verification

**Files:**
- None modified.

**Interfaces:**
- Consumes: The newly created `.venv`.
- Produces: Successful test results.

- [ ] **Step 1: Run the test suite**

Run: `uv run pytest`
Expected: PASS. All tests should pass as they did in the previous environment.
