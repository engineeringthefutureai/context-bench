"""Minimal OpenAI-compatible chat client. Plain HTTP, no frameworks."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 600.0):
        raise NotImplementedError

    def chat(self, messages: list[dict], *, temperature: float | None, seed: int,
             max_tokens: int, tools: list[dict] | None = None) -> ChatResult:
        """POST /v1/chat/completions; return content, tool calls and token usage.
        temperature=None omits the field so the server default applies."""
        raise NotImplementedError

    def server_sampling(self) -> dict:
        """Effective default sampling settings (temperature, top_p, top_k, min_p, ...), e.g. from
        llama.cpp GET /props default_generation_settings; {} if the server doesn't expose them."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        """Exact count if the server exposes a tokenize endpoint, else an estimate."""
        raise NotImplementedError
