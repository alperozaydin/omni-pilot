# Pytest Assertions, Exceptions & Warnings

Pytest uses standard Python `assert` expressions and provides powerful exception and warning assertion helpers.

---

## 1. The Power of Plain `assert`

Unlike traditional frameworks that require specialized methods (`self.assertEqual`, `self.assertIn`), pytest rewrites `assert` statements at import time to display detailed introspection data on failure:

```python
def test_data_structure():
    result = {"name": "Alice", "roles": ["admin", "editor"], "meta": {"score": 95}}
    
    # Simple, readable, and outputs detailed diffs if mismatched
    assert result["name"] == "Alice"
    assert "admin" in result["roles"]
    assert result["meta"]["score"] >= 90
```

---

## 2. Asserting Exceptions (`pytest.raises`)

### Context Manager with Message Matching (`match=r"..."`)
Always use the `match` parameter to ensure the exact expected error message is triggered:

```python
import pytest

def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError, match=r"division by zero"):
        divide(10, 0)
```

### Inspecting Exception Details (`exc_info`)
Capture the exception object to inspect custom attributes or properties:

```python
def test_custom_api_error():
    with pytest.raises(APIException) as exc_info:
        client.get_user(invalid_id=-1)

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "USER_NOT_FOUND"
```

### Asserting Exception Groups (Pytest 8.0+ / Python 3.11+)
For concurrent or grouped exceptions (e.g. `asyncio.TaskGroup`), Pytest 8.0+ provides native inspection helpers:

```python
import asyncio

async def test_task_group_exceptions():
    with pytest.raises(ExceptionGroup) as exc_info:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(failing_task_one())
            tg.create_task(failing_task_two())

    # Verify nested exception types within the group
    assert exc_info.group_contains(ValueError)
    assert exc_info.group_contains(TypeError)
```

---

## 3. Asserting Warnings (`pytest.warns`)

Ensure that deprecated features or configuration issues emit appropriate warnings:

```python
import warnings
import pytest

def test_deprecated_feature_warning():
    with pytest.warns(DeprecationWarning, match=r"legacy_calculate is deprecated"):
        legacy_calculate(10)
```

---

## 4. Comparing Floating Point Numbers (`pytest.approx`)

Avoid floating point precision errors by using `pytest.approx`:

```python
from pytest import approx

def test_floating_point_math():
    # Comparing single values
    assert 0.1 + 0.2 == approx(0.3)

    # Comparing dictionaries or lists with tolerance
    calculated = {"x": 1.000001, "y": 2.000002}
    expected = {"x": 1.0, "y": 2.0}
    assert calculated == approx(expected, rel=1e-5)
```
