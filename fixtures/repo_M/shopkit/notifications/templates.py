"""Tiny template engine for notification texts: {{ name }} placeholders, nothing else."""
import re

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_.]*)\s*\}\}")


class MissingValue(KeyError):
    pass


def render_template(template: str, values: dict) -> str:
    """Replace {{ key }} (dotted keys walk nested dicts: {{ customer.name }}) with str(value).
    A placeholder without a value raises MissingValue, so no email goes out with a hole in it."""
    def lookup(match: re.Match) -> str:
        current: object = values
        for part in match.group(1).split("."):
            if not isinstance(current, dict) or part not in current:
                raise MissingValue(match.group(1))
            current = current[part]
        return str(current)
    return _PLACEHOLDER.sub(lookup, template)


TEMPLATES = {
    "order_confirmation": ("Your order {{ order_id }}",
                           "Hi {{ first_name }},\n\nThanks for your order {{ order_id }} "
                           "({{ total }}). We'll email you when it ships."),
    "shipped": ("Order {{ order_id }} is on its way",
                "Hi {{ first_name }},\n\nYour order shipped with {{ carrier }}. "
                "Track it here: {{ tracking_url }}"),
    "low_stock": ("Low stock: {{ sku }}", "{{ sku }} is down to {{ available }} units."),
}
