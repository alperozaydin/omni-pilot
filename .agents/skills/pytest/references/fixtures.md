# Pytest Fixtures & Dependency Injection

Fixtures are functions that prepare test environments, supply test data, and handle cleanup. Pytest manages fixture dependencies via explicit dependency injection.

---

## 1. Defining and Using Fixtures

```python
import pytest

@pytest.fixture
def sample_user():
    """Provides a fresh user dictionary for each test."""
    return {"id": 1, "username": "alice", "is_active": True}

def test_user_active(sample_user):
    assert sample_user["is_active"] is True
```

---

## 2. Fixture Scopes

The `scope` parameter controls how often a fixture is created and destroyed:

| Scope | Lifetime | Best For |
| :--- | :--- | :--- |
| `function` *(default)* | Created once per test function | Mutable objects, independent test state |
| `class` | Created once per test class | Read-only class-level setups |
| `module` | Created once per test module (`.py` file) | Expensive read-only objects (e.g. database schema check) |
| `package` | Created once per package/subpackage | Shared package resources |
| `session` | Created once for the entire test session | Database engine, Docker test containers, HTTP test servers |

```python
@pytest.fixture(scope="session")
def database_engine():
    engine = create_test_engine()
    yield engine
    engine.dispose()
```

---

## 3. Setup & Teardown with `yield`

Use the `yield` statement to cleanly separate setup logic from teardown cleanup:

```python
@pytest.fixture
def temporary_database():
    # 1. Setup: runs BEFORE the test
    db = create_test_db()
    populate_initial_data(db)

    yield db  # Test executes with this value

    # 2. Teardown: runs AFTER the test (even if test fails)
    db.drop_all_tables()
    db.close()
```

---

## 4. Automatic Execution (`autouse=True`)

Use `autouse=True` when a fixture must run for every test without explicitly declaring it as an argument:

```python
@pytest.fixture(autouse=True)
def reset_database_state():
    """Runs before each test automatically to ensure clean database state."""
    db.truncate_tables()
```

---

## 5. Sharing Fixtures with `conftest.py`

- Fixtures defined in a `conftest.py` file are automatically discovered by any test in that directory and its subdirectories.
- **Never import `conftest`**: Pytest handles injection automatically.
- Subdirectories can define their own `conftest.py` to override or extend root fixtures for specific subsystems.

```text
tests/
├── conftest.py               # Global fixtures (session DB, global app config)
├── unit/
│   └── test_logic.py         # Uses root conftest fixtures
└── integration/
    ├── conftest.py           # Integration-only fixtures (mock external APIs)
    └── test_api.py
```

---

## 6. Parameterized Fixtures

Run every test using this fixture multiple times across a matrix of values:

```python
@pytest.fixture(params=["sqlite", "postgres"])
def db_type(request):
    return request.param

def test_db_connection(db_type):
    conn = connect_to_db(db_type)
    assert conn.is_connected()
```

## 7. Applying Fixtures with `@pytest.mark.usefixtures`

When a test function or test class requires a fixture for its side effects (e.g. populating a database, changing working directory, setting up mocks) but does not need to access the fixture's return value directly, use `@pytest.mark.usefixtures`:

```python
# Apply fixture to an entire test class
@pytest.mark.usefixtures("clean_database", "mock_external_payment")
class TestOrderProcessing:
    def test_checkout_cart(self):
        # clean_database and mock_external_payment ran before this test
        order = process_cart(cart_id=101)
        assert order.status == "processed"

    def test_refund_order(self):
        result = refund(order_id=101)
        assert result is True

# Apply fixture to a single test function without cluttering parameters
@pytest.mark.usefixtures("seed_initial_users")
def test_user_count():
    assert get_total_users() == 5
```

---

## 8. Essential Built-in Fixtures

### `tmp_path` (Pathlib Temporary Directory)
Provides a unique `pathlib.Path` directory for each test function:

```python
def test_file_writer(tmp_path):
    file = tmp_path / "data.txt"
    file.write_text("hello world")
    assert file.read_text() == "hello world"
```

### `monkeypatch` (Safe Environment & Attribute Patching)
Safely modifies objects, dictionaries, or environment variables and automatically restores them after the test:

```python
def test_environment_variable(monkeypatch):
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setattr("myapp.config.DEBUG_MODE", False)
    assert get_current_env() == "staging"
```

### `capsys` (Standard Output & Error Capture)
Captures writes to `sys.stdout` and `sys.stderr`:

```python
def test_cli_output(capsys):
    print("Welcome to the app!")
    captured = capsys.readouterr()
    assert "Welcome to the app!" in captured.out
```

### `caplog` (Logging Capture & Inspection)
Inspects log messages emitted during test execution:

```python
import logging

def test_logging_warning(caplog):
    with caplog.at_level(logging.WARNING):
        process_risky_task()
    assert "Task completed with warnings" in caplog.text
```
