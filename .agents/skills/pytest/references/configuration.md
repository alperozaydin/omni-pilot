# Pytest Configuration & Coverage

Configuring pytest at the project level ensures consistent CLI defaults, marker declarations, discovery rules, and test coverage across developer workstations and CI/CD pipelines.

---

## 1. Modern Configuration via `pyproject.toml`

Modern Python projects should configure pytest within `pyproject.toml` under the `[tool.pytest.ini_options]` table:

```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]

# Standard CLI arguments applied to every test run
addopts = [
    "-v",
    "--tb=short",
    "--strict-markers",
    "--strict-config",
]

# Register custom markers to avoid typo warnings
markers = [
    "smoke: Critical sanity tests",
    "integration: Tests requiring external services or databases",
    "slow: Tests with runtimes > 2 seconds",
]

# Warning handling
filterwarnings = [
    "error",                           # Treat unexpected warnings as errors
    "ignore::DeprecationWarning:pkg.*" # Ignore third-party deprecations
]

# Asyncio configuration
asyncio_mode = "auto"
```

---

## 2. Legacy `pytest.ini` Fallback

For older repositories without a `pyproject.toml`, use `pytest.ini` in the project root:

```ini
[pytest]
minversion = 8.0
testpaths = tests
addopts = -v --tb=short --strict-markers
markers =
    smoke: Critical sanity tests
    integration: Integration tests
```

---

## 3. Code Coverage with `pytest-cov`

The `pytest-cov` plugin measures code coverage during test runs and generates detailed line-by-line breakdown reports.

### Common CLI Invocations

```bash
# Run tests with terminal coverage report showing missing lines
pytest --cov=myapp --cov-report=term-missing

# Generate an interactive HTML coverage report in htmlcov/
pytest --cov=myapp --cov-report=html

# Fail CI pipeline if total code coverage drops below a threshold (e.g. 85%)
pytest --cov=myapp --cov-fail-under=85
```

### Coverage Configuration (`pyproject.toml`)
You can configure coverage settings directly in `pyproject.toml` under `[tool.coverage]`:

```toml
[tool.coverage.run]
source = ["myapp"]
branch = true
omit = [
    "tests/*",
    "*/__init__.py",
    "*/migrations/*",
]

[tool.coverage.report]
show_missing = true
skip_covered = false
fail_under = 80
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```
