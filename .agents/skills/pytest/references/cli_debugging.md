# Pytest CLI Flags & Debugging Workflows

Mastering pytest command-line options accelerates test discovery, fast-tracks debugging, and prevents log noise in large test suites.

---

## 1. Test Selection & Filtering

### By Path and Node ID
Target specific files, test classes, or individual functions:

```bash
# Run an entire test file
pytest tests/unit/test_auth.py

# Run a specific test function in a file
pytest tests/unit/test_auth.py::test_login_success

# Run a specific test method within a test class
pytest tests/unit/test_auth.py::TestAuthService::test_token_refresh
```

### By Expression Matching (`-k`)
Filter tests by substring matches across file names, class names, function names, and parameter IDs:

```bash
# Run tests matching 'payment' or 'checkout'
pytest -k "payment or checkout"

# Run tests with 'auth' in the name but NOT 'oauth'
pytest -k "auth and not oauth"
```

### By Markers (`-m`)
```bash
# Run only integration tests
pytest -m integration

# Run non-slow unit tests
pytest -m "not slow"
```

---

## 2. Traceback & Output Formatting

| Flag | Purpose | Best When |
| :--- | :--- | :--- |
| `--tb=short` | Prints only the failing code line and assert diff | Quick review of failures |
| `--tb=line` | One-line summary per failure | Large suites with many failures |
| `--tb=auto` *(default)* | Full traceback for first/last frame, abbreviated middle | Standard test runs |
| `--tb=native` | Standard Python traceback formatting | Comparing against vanilla Python exceptions |
| `-v` / `-vv` | Verbose / Extra verbose test names & full parameter diffs | Investigating parameterized failures |
| `-s` | Disables output capture; displays `print()` directly | Inspecting live prints during development |

---

## 3. Fast Iteration & Failure Reruns

### Stopping Early
Avoid running hundreds of tests after a breaking change:

```bash
# Stop immediately on the first test failure or error
pytest -x

# Stop after N failures
pytest --maxfail=3
```

### Re-running Failures
Pytest caches previous execution results in `.pytest_cache`:

```bash
# Run ONLY the tests that failed during the previous test run
pytest --lf

# Run failed tests FIRST, then proceed to run all remaining passing tests
pytest --ff

# Run newly added tests first, then remaining tests
pytest --nf
```

---

## 4. Interactive Debugging with PDB

### Drop to Debugger on Failure (`--pdb`)
Automatically open the Python debugger at the exact point where an assertion failed or an unhandled exception occurred:

```bash
pytest --pdb
```

### Break at Test Start (`--trace`)
Pause execution at the very first line of each selected test:

```bash
pytest --trace tests/test_payment.py::test_checkout
```

---

## 5. Summary & Reporting Flags

```bash
# Show comprehensive summary report at the end (passed, skipped, failed, xfailed)
pytest -rA

# Show duration of slowest N test cases (great for performance tuning)
pytest --durations=10
```
