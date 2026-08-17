# Pytest Parametrization & Marks

Parametrization and markers allow you to execute tests across multiple data sets and categorize test suites for selective execution.

---

## 1. Test Parametrization (`@pytest.mark.parametrize`)

Parametrization runs the same test function across different input and expected output sets:

```python
import pytest
from myapp.math_utils import is_prime

@pytest.mark.parametrize("number, expected", [
    (2, True),
    (3, True),
    (4, False),
    (17, True),
    (1, False),
])
def test_is_prime(number, expected):
    assert is_prime(number) is expected
```

### Custom Test IDs with `pytest.param`
Use `pytest.param` to give readable identifiers or mark specific parameter sets:

```python
@pytest.mark.parametrize("payload, status_code", [
    pytest.param({"user": "alice"}, 200, id="valid_payload"),
    pytest.param({}, 400, id="empty_payload"),
    pytest.param({"user": "admin"}, 403, id="restricted_user", marks=pytest.mark.xfail),
])
def test_api_endpoint(payload, status_code):
    response = client.post("/auth", json=payload)
    assert response.status_code == status_code
```

### Cartesian Product (Multiple `@parametrize`)
Stacking `@parametrize` runs every combination:

```python
@pytest.mark.parametrize("x", [1, 2])
@pytest.mark.parametrize("y", [10, 20])
def test_matrix_combination(x, y):
    # Runs 4 times: (1, 10), (1, 20), (2, 10), (2, 20)
    assert x * y > 0
```

---

## 2. Built-in Markers

### `@pytest.mark.skip` and `skipif`
Skip tests unconditionally or based on environment conditions:

```python
import sys

@pytest.mark.skip(reason="Feature currently under active redesign")
def test_legacy_feature():
    pass

@pytest.mark.skipif(sys.platform == "win32", reason="Feature not supported on Windows")
def test_posix_socket():
    pass
```

### `@pytest.mark.xfail` (Expected Failures)
Marks a test that is known to fail (e.g. pending bugfix). Use `strict=True` to fail the test suite if it unexpectedly passes:

```python
@pytest.mark.xfail(reason="Bug #1042: timeout under high load", strict=True)
def test_high_load_timeout():
    response = slow_service.call()
    assert response.status_code == 200
```

---

## 3. Custom Markers & Registration

Custom markers categorize tests (e.g. `integration`, `slow`, `smoke`).

### Registering Markers (`pyproject.toml`)
Always register custom markers in `pyproject.toml` to prevent pytest from raising typo warnings:

```toml
[tool.pytest.ini_options]
markers = [
    "smoke: Quick sanity check tests",
    "integration: Tests requiring external services or databases",
    "slow: Tests with runtimes > 2 seconds",
]
```

### Applying Custom Markers
```python
@pytest.mark.integration
@pytest.mark.slow
def test_external_payment_gateway():
    result = process_real_stripe_charge()
    assert result.is_success
```

### Running Marked Tests
```bash
# Run only smoke tests
pytest -m smoke

# Run integration tests, excluding slow ones
pytest -m "integration and not slow"
```
