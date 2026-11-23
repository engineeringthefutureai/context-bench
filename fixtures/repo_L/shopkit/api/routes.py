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
