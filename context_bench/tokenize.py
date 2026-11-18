"""Code-aware tokenizer for lexical retrieval."""
from __future__ import annotations


def code_tokens(text: str) -> list[str]:
    """Lowercased tokens with snake_case and camelCase split ("normalize_sku" -> normalize, sku)."""
    raise NotImplementedError
