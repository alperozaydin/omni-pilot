# Mocking with `pytest-mock` & Async Testing

Modern pytest suites use the **`pytest-mock`** plugin (`mocker` fixture) for all mocking and stubbing, alongside **`pytest-asyncio`** for asynchronous code.

---

## 🚫 Why `unittest.mock` is an Anti-Pattern in Pytest

| Pattern | `unittest.mock` (Avoid) ❌ | `pytest-mock` (Preferred) ✅ |
| :--- | :--- | :--- |
| **Patching Syntax** | Nested `@patch` decorators or `with patch(...)` context blocks | Direct `mocker.patch(...)` fixture calls |
| **Parameter Ordering** | Decorators inject arguments in reverse order, making signatures confusing | No extra parameters required; assign return value directly to variable |
| **Cleanup & Teardown** | Manual unpatching needed if not using context managers properly | Guarantees automatic unpatching at test teardown |
| **Spying / Stubbing** | Complex custom mock construction | Built-in `mocker.spy()` and `mocker.stub()` |

---

## 1. Core `mocker` Fixture Methods

### Patching Functions or Methods
Always patch where the object is **looked up / imported**, not where it is defined:

```python
# Assuming myapp/services.py does: `from myapp.client import api_call`

def test_fetch_user_data(mocker):
    # Patch where it is used (myapp.services.api_call)
    mock_api = mocker.patch("myapp.services.api_call", return_value={"name": "Alice"})

    service = UserService()
    result = service.fetch_profile(user_id=123)

    assert result["name"] == "Alice"
    mock_api.assert_called_once_with(123)
```

### Patching Object Attributes (`mocker.patch.object`)
```python
def test_payment_charge(mocker):
    client = PaymentGatewayClient()
    mock_charge = mocker.patch.object(client, "charge", return_value=True)

    success = client.charge(amount=100)

    assert success is True
    mock_charge.assert_called_once_with(amount=100)
```

### Spying on Real Methods (`mocker.spy`)
Wraps an existing method to monitor calls, arguments, and return values without overriding its actual behavior:

```python
def test_spy_calculation(mocker):
    calc = Calculator()
    spy_add = mocker.spy(calc, "add")

    result = calc.add(2, 3)

    assert result == 5
    spy_add.assert_called_once_with(2, 3)
```

### Stubbing (`mocker.stub`)
Creates a standalone callable stub:

```python
def test_callback_invoked(mocker):
    callback = mocker.stub(name="on_complete")

    emitter = EventEmitter()
    emitter.subscribe(callback)
    emitter.trigger()

    callback.assert_called_once()
```

---

## 2. Testing Asynchronous Code with `pytest-asyncio`

### Configuration (`pyproject.toml`)
Enable automatic async test detection in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### Async Test Functions
With `asyncio_mode = "auto"`, write standard `async def test_*` functions:

```python
async def test_async_fetch():
    result = await fetch_async_data(url="https://api.example.com/data")
    assert result["status"] == 200
```

### Mocking Async Coroutines
Use `mocker.AsyncMock` to mock asynchronous functions and coroutines:

```python
async def test_async_service(mocker):
    mock_fetch = mocker.patch(
        "myapp.async_service.fetch_remote_data",
        new_callable=mocker.AsyncMock,
        return_value={"id": 42, "status": "active"}
    )

    result = await process_async_task()

    assert result["status"] == "active"
    mock_fetch.assert_awaited_once()
```

### Mocking Async Context Managers
`mocker.AsyncMock` natively implements the async context manager protocol (`__aenter__` and `__aexit__`), making it straightforward to mock libraries like `aiohttp` or `httpx`:

```python
async def test_async_context_manager(mocker):
    # AsyncMock natively provides __aenter__ returning itself (or configured return value)
    mock_response = mocker.AsyncMock()
    mock_response.status = 200
    mock_response.json = mocker.AsyncMock(return_value={"status": "ok"})

    # Configure client session context manager
    mock_client = mocker.AsyncMock()
    mock_client.get.return_value.__aenter__.return_value = mock_response
    mocker.patch("myapp.client.httpx.AsyncClient", return_value=mock_client)

    result = await perform_async_request()
    assert result == {"status": "ok"}
```
