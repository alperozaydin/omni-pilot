# uv Package Manager Migration Design

## Objective
Introduce `uv` as the primary package manager for the `omni-pilot` project, replacing the existing `requirements.txt` and standard `pip`/`venv` workflow. This will improve dependency resolution speed and streamline environment management.

## Proposed Changes

### 1. Initialize `pyproject.toml`
- Create a `pyproject.toml` configuration file to manage project metadata and dependencies natively.
- The project's existing structure (`src/` and `tests/`) will be preserved and mapped correctly within the build system.

### 2. Dependency Migration
Dependencies currently residing in `requirements.txt` will be mapped to the new configuration:
- **Runtime Dependencies**:
  - `openpyxl>=3.1.0`
  - `tinydb>=4.8.0`
  - `requests>=2.31.0`
  - `rich>=13.0.0`
  - `pyyaml>=6.0`
  - `jinja2>=3.1.0`
- **Development Dependencies**:
  - `pytest>=7.0.0` will be moved into a dedicated dev dependency group.

### 3. Environment Recreation
- The existing `.venv` folder will be deleted to avoid conflict and state issues.
- `uv sync` will be executed to generate a new, optimized `.venv` and a reproducible `uv.lock` file.

### 4. Cleanup
- `requirements.txt` will be permanently deleted, making `pyproject.toml` the sole source of truth.

## Verification
- Run the test suite via `uv run pytest` to ensure all imports and packages are functioning correctly in the new environment.
