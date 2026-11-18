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

    def chat(self, messages: list[dict], *, temperature: float, seed: int,
             max_tokens: int, tools: list[dict] | None = None) -> ChatResult:
        """POST /v1/chat/completions; return content, tool calls and token usage."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        """Exact count if the server exposes a tokenize endpoint, else an estimate."""
        raise NotImplementedError
