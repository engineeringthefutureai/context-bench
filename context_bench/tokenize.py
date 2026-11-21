"""Code-aware tokenizer for lexical retrieval."""
from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def code_tokens(text: str) -> list[str]:
    """Lowercased tokens with snake_case and camelCase split ("normalize_sku" -> normalize, sku;
    "makeSlug" -> make, slug; "HTTPError" -> http, error). Digits are their own tokens."""
    return [part.lower() for word in _WORD.findall(text) for part in _CAMEL.findall(word)]
