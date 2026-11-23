"""Form handling for the storefront and admin pages: declare fields, validate submitted data, get
typed values or per-field errors to show next to the inputs.

    class AddressForm(Form):
        name = Field(required=True, max_length=80)
        postcode = Field(required=True, clean=normalize_postcode)
    form = AddressForm(request_data)
    if form.is_valid(): use(form.cleaned)
    else: render(errors=form.errors)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from shopkit.money import parse_money
from shopkit.utils.validation import is_valid_email


@dataclass
class Field:
    required: bool = False
    max_length: int | None = None
    choices: tuple[str, ...] | None = None
    clean: Callable[[str], object] | None = None      # may raise ValueError with the message to show
    default: object = None
    label: str = ""

    def run(self, raw: str | None) -> object:
        value = (raw or "").strip()
        if not value:
            if self.required:
                raise ValueError("This field is required.")
            return self.default
        if self.max_length is not None and len(value) > self.max_length:
            raise ValueError(f"At most {self.max_length} characters.")
        if self.choices is not None and value not in self.choices:
            raise ValueError("Choose one of the listed options.")
        return self.clean(value) if self.clean else value


def email_field(required: bool = True) -> Field:
    def clean(value: str) -> str:
        if not is_valid_email(value):
            raise ValueError("Enter a valid email address.")
        return value.lower()
    return Field(required=required, max_length=254, clean=clean, label="Email")


def money_field(required: bool = True, minimum_cents: int = 0) -> Field:
    def clean(value: str) -> int:
        try:
            cents = parse_money(value)
        except ValueError:
            raise ValueError("Enter an amount like 12.50.") from None
        if cents < minimum_cents:
            raise ValueError("Amount is too small.")
        return cents
    return Field(required=required, clean=clean, label="Amount")


def int_field(required: bool = True, minimum: int | None = None, maximum: int | None = None) -> Field:
    def clean(value: str) -> int:
        try:
            n = int(value)
        except ValueError:
            raise ValueError("Enter a whole number.") from None
        if minimum is not None and n < minimum:
            raise ValueError(f"Must be at least {minimum}.")
        if maximum is not None and n > maximum:
            raise ValueError(f"Must be at most {maximum}.")
        return n
    return Field(required=required, clean=clean)


class Form:
    def __init__(self, data: dict[str, str] | None = None):
        self.data = data or {}
        self.cleaned: dict[str, object] = {}
        self.errors: dict[str, str] = {}
        self._validated = False

    @classmethod
    def fields(cls) -> dict[str, Field]:
        out = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, Field):
                    out[name] = value
        return out

    def is_valid(self) -> bool:
        if not self._validated:
            for name, f in self.fields().items():
                try:
                    self.cleaned[name] = f.run(self.data.get(name))
                except ValueError as e:
                    self.errors[name] = str(e)
            if not self.errors:
                try:
                    self.validate()
                except ValueError as e:
                    self.errors["__all__"] = str(e)
            self._validated = True
        return not self.errors

    def validate(self) -> None:
        """Cross-field checks; raise ValueError with a form-level message."""


class AddressForm(Form):
    name = Field(required=True, max_length=80, label="Full name")
    line1 = Field(required=True, max_length=120, label="Address")
    line2 = Field(max_length=120, default="")
    city = Field(required=True, max_length=80)
    postcode = Field(required=True, max_length=16)
    country = Field(required=True, choices=("US", "GB", "DE", "FR", "NL", "IE", "CA"))


class ContactForm(Form):
    email = email_field()
    subject = Field(required=True, max_length=120)
    order_id = Field(max_length=40, default=None)
    message = Field(required=True, max_length=4000)


class RefundForm(Form):
    """Admin: refund part or all of a payment."""
    amount = money_field(minimum_cents=1)
    reason = Field(required=True, choices=("damaged", "not_received", "changed_mind", "other"))
    note = Field(max_length=500, default="")

    def __init__(self, data: dict[str, str] | None = None, *, max_refundable_cents: int = 0):
        super().__init__(data)
        self.max_refundable_cents = max_refundable_cents

    def validate(self) -> None:
        if self.cleaned["amount"] > self.max_refundable_cents:
            raise ValueError("The amount is more than what is left to refund.")


@dataclass
class FieldView:
    """What a template needs for one input: name, value to show, error."""
    name: str
    value: str = ""
    error: str = ""
    label: str = ""
    choices: tuple[str, ...] = field(default_factory=tuple)


def field_views(form: Form) -> list[FieldView]:
    out = []
    for name, f in form.fields().items():
        out.append(FieldView(name=name, value=form.data.get(name, "") or "", error=form.errors.get(name, ""),
                             label=f.label or name.replace("_", " ").capitalize(), choices=f.choices or ()))
    return out
