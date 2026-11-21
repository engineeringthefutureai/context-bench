"""Customer registry used by checkout and the admin API."""
from shopkit.customers.models import Address, Customer
from shopkit.errors import Conflict, NotFound, ShopError
from shopkit.utils.ids import new_id
from shopkit.utils.validation import is_valid_email


class CustomerNotFound(NotFound):
    pass


class EmailTaken(Conflict):
    pass


class InvalidEmail(ShopError):
    http_status = 422


class CustomerDirectory:
    def __init__(self):
        self._by_id: dict[str, Customer] = {}

    def register(self, email: str, name: str, marketing_opt_in: bool = False) -> Customer:
        email = email.strip().lower()
        if not is_valid_email(email):
            raise InvalidEmail(f"invalid email {email!r}", email=email)
        if self.find_by_email(email):
            raise EmailTaken(f"{email} is already registered", email=email)
        customer = Customer(id=new_id("cus"), email=email, name=name.strip(),
                            marketing_opt_in=marketing_opt_in)
        self._by_id[customer.id] = customer
        return customer

    def get(self, customer_id: str) -> Customer:
        try:
            return self._by_id[customer_id]
        except KeyError:
            raise CustomerNotFound(f"no customer {customer_id}", customer_id=customer_id) from None

    def find_by_email(self, email: str) -> Customer | None:
        email = email.strip().lower()
        return next((c for c in self._by_id.values() if c.email == email), None)

    def add_address(self, customer_id: str, address: Address, default: bool = False) -> None:
        customer = self.get(customer_id)
        if default:
            customer.addresses.insert(0, address)
        else:
            customer.addresses.append(address)

    def opted_in(self) -> list[Customer]:
        return [c for c in self._by_id.values() if c.marketing_opt_in]
