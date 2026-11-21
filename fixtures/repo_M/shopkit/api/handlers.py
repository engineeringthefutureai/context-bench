from shopkit.errors import ShopError


def handle(fn, *args, **kwargs) -> tuple[int, dict]:
    """Call a service function and turn the outcome into (status, body) for the HTTP layer."""
    try:
        return 200, {"result": fn(*args, **kwargs)}
    except ShopError as e:
        return e.http_status, {"error": type(e).__name__, "message": str(e), **e.details}
