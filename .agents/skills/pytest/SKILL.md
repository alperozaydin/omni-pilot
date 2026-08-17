---
name: pytest
description: >-
  Authoring, running, debugging, and configuring Python test suites using pytest.
  Covers fixtures, assertions, parametrization, async testing, mocking with pytest-mock,
  and CLI execution. Use when writing tests, refactoring test suites, debugging failures,
  or configuring pytest projects.
---

# Pytest Skill

A comprehensive guide for authoring, executing, and maintaining clean, idiomatic Python test suites using **pytest** and **pytest-mock**.

---

## 🚫 Critical Rules & Anti-Patterns

- ❌ **NEVER import or use `unittest`**:
  - Do not subclass `unittest.TestCase`.
  - Do not use `self.assertEqual`, `self.assertTrue`, `self.assertRaises`, or any `self.assert*` methods.
  - Do not use `@unittest.mock.patch` decorators or `with mock.patch(...)` context managers.
- ✅ **ALWAYS use native pytest patterns**:
  - Write plain standalone functions (`test_*`) or unprefixed test classes (`Test*`) with no base class.
  - Use simple Python `assert` statements with standard operators (`==`, `in`, `isinstance`).
  - Use the `mocker` fixture from `pytest-mock` for all mocking, spying, and patching.
  - Use `pytest.raises(Exception, match="...")` for expected exceptions.
  - Use fixtures (and `conftest.py`) for reusable setup and teardown.

---

## ⚡ When to Activate

- Creating new unit, integration, or functional tests in Python.
- Refactoring legacy tests away from `unittest` into clean pytest idioms.
- Running tests, filtering specific test cases, and analyzing failure tracebacks.
- Mocking external dependencies, APIs, time, or environment variables using `pytest-mock`.
- Setting up or tuning test configurations (`pyproject.toml`, coverage, markers).

---

## 🛠️ CLI Quick Reference

```bash
# Basic test execution with concise tracebacks
pytest -v --tb=short

# Run a specific test file or specific test function
pytest tests/test_auth.py -v
pytest tests/test_auth.py::test_login_success -v

# Run tests matching a keyword pattern or marker
pytest -k "login or register" -v
pytest -m "integration and not slow" -v

# Fast debugging: stop on first failure, rerun last failed
pytest -x --tb=short
pytest --lf -v             # Last failed only
pytest --ff -v             # Run failed first, then the rest

# Interactive debugging and stdout visibility
pytest -s                  # Disable stdout/stderr capturing (show print output)
pytest --pdb               # Drop into PDB debugger on first error/failure
```

---

## 🧪 Standard Test Anatomy

```python
"""tests/test_user_service.py"""
import pytest
from myapp.services import UserService
from myapp.models import User

def test_create_user_success(mocker, tmp_path):
    """Test user creation with mocked email notification."""
    # 1. Arrange (Mocks & Data)
    mock_send_email = mocker.patch("myapp.services.send_welcome_email")
    service = UserService(storage_dir=tmp_path)

    # 2. Act
    user = service.create_user(email="alice@example.com", name="Alice")

    # 3. Assert (Plain assert & mocker verifications)
    assert isinstance(user, User)
    assert user.email == "alice@example.com"
    mock_send_email.assert_called_once_with("alice@example.com")

def test_create_duplicate_user_raises(mocker):
    """Test that attempting to register an existing user raises ValueError."""
    mocker.patch("myapp.services.user_exists", return_value=True)
    service = UserService()

    with pytest.raises(ValueError, match=r"User already exists"):
        service.create_user(email="duplicate@example.com", name="Duplicate")
```

---

## 📚 Deep-Dive References (Progressive Disclosure)

For detailed guides, syntax rules, and official best practices, refer to:

- [**Fixtures & Scopes**](./references/fixtures.md): Fixture dependency injection, scopes (`function`, `session`), yield fixtures, `conftest.py`, and built-in fixtures (`tmp_path`, `monkeypatch`, `capsys`).
- [**Mocking & Async**](./references/mocking_async.md): Complete guide to `pytest-mock` (`mocker` fixture, spying, async mocking) and `pytest-asyncio`.
- [**Parametrization & Marks**](./references/parametrization_marks.md): `@pytest.mark.parametrize`, custom markers, `skip`, `skipif`, and `xfail`.
- [**Assertions & Exceptions**](./references/assertions_exceptions.md): Assertion rewriting, `pytest.raises`, `pytest.warns`, and `pytest.approx`.
- [**CLI & Debugging**](./references/cli_debugging.md): In-depth execution flags, traceback formatting, filtering, and debugging strategies.
- [**Configuration & Coverage**](./references/configuration.md): `pyproject.toml` (`[tool.pytest.ini_options]`), discovery rules, and `pytest-cov` integration.
