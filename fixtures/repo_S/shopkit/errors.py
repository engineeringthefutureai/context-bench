"""Business-rule failures. The API layer maps each one to an HTTP status via `http_status`
and puts `details` into the error body."""


class ShopError(Exception):
    http_status = 400

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class NotFound(ShopError):
    http_status = 404


class Conflict(ShopError):
    http_status = 409


class OrderNotFound(NotFound):
    pass


class DuplicateOrder(Conflict):
    pass
