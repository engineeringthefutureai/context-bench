"""Route table for the HTTP layer. Each route maps (method, path) to a service call via `handle`."""
from shopkit.api import serializers
from shopkit.api.handlers import handle
from shopkit.api.pagination import paginate
from shopkit.catalog.models import Product
from shopkit.catalog.search import search
from shopkit.customers.service import CustomerDirectory
from shopkit.orders.service import OrderBook
from shopkit.utils.collections import index_by


class App:
    def __init__(self, orders: OrderBook, customers: CustomerDirectory, products: list[Product]):
        self.orders = orders
        self.customers = customers
        self.products = products
        self._by_sku = index_by(products, key=lambda p: p.sku)

    def get_order(self, order_id: str) -> tuple[int, dict]:
        return handle(lambda: serializers.order(self.orders.get(order_id)))

    def get_customer(self, customer_id: str) -> tuple[int, dict]:
        return handle(lambda: serializers.customer(self.customers.get(customer_id)))

    def register(self, body: dict) -> tuple[int, dict]:
        return handle(lambda: serializers.customer(
            self.customers.register(body.get("email", ""), body.get("name", ""),
                                    bool(body.get("marketing_opt_in")))))

    def list_products(self, page: int = 1, per_page: int | None = None) -> tuple[int, dict]:
        active = [p for p in self.products if p.active]
        def run():
            result = paginate(active, page, per_page)
            result["items"] = [serializers.product(p) for p in result["items"]]
            return result
        return handle(run)

    def get_product(self, sku: str) -> tuple[int, dict]:
        return handle(lambda: serializers.product(self._by_sku[sku]))

    def search_products(self, q: str) -> tuple[int, dict]:
        return handle(lambda: [serializers.product(p) for p in search(self.products, q)])


class AdminApp(App):
    """Admin endpoints and pages on top of the public App. Every call takes the authenticated key
    (api.auth) and checks its role; order changes go through OrderService."""

    def __init__(self, orders: OrderBook, customers: CustomerDirectory, products: list[Product],
                 service=None, audit=None):
        super().__init__(orders, customers, products)
        from shopkit.orders.service import OrderService
        self.service = service or OrderService(book=orders)
        self.audit = audit

    def _guard(self, key, role: str) -> None:
        from shopkit.api.auth import require_role
        require_role(key, role)

    def list_orders(self, key, status: str | None = None, page: int = 1) -> tuple[int, dict]:
        def run():
            self._guard(key, "viewer")
            found = self.service.search(status=status)
            result = paginate(found, page)
            result["items"] = [serializers.order(o, self.service.board.status(o.id)) for o in result["items"]]
            return result
        return handle(run)

    def cancel_order(self, key, order_id: str, body: dict) -> tuple[int, dict]:
        def run():
            self._guard(key, "staff")
            reason = (body.get("reason") or "").strip()
            if not reason:
                from shopkit.errors import ShopError
                raise ShopError("a reason is required")
            self.service.cancel(order_id, actor=key.key_id, reason=reason)
            if self.audit is not None:
                self.audit.record(key.key_id, "order.cancelled", f"order:{order_id}", reason=reason)
            return serializers.order(self.orders.get(order_id), self.service.board.status(order_id))
        return handle(run)

    def merge_customers(self, key, keep_id: str, duplicate_id: str) -> tuple[int, dict]:
        def run():
            self._guard(key, "admin")
            return serializers.customer(self.customers.merge(keep_id, duplicate_id))
        return handle(run)

    def order_page(self, key, order_id: str) -> tuple[int, str]:
        """HTML admin order page (web.views)."""
        from shopkit.errors import ShopError
        from shopkit.web import views
        try:
            self._guard(key, "viewer")
            order = self.orders.get(order_id)
            status = self.service.board.status(order_id)
            history = [(s, "", "") for s in self.service.board.history(order_id)]
            return 200, views.order_detail(order, status=status, history=history, products=self._by_sku, admin=True)
        except ShopError as e:
            return e.http_status, views.error_page(e.http_status, str(e))

    def product_page(self, sku: str, in_stock: int = 0) -> tuple[int, str]:
        from shopkit.web import views
        product = self._by_sku.get(sku)
        if product is None or not product.active:
            return 404, views.error_page(404, "We couldn't find that product.")
        return 200, views.product_page(product, in_stock=in_stock)
