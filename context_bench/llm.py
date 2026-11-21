"""Minimal OpenAI-compatible chat client. Plain HTTP, no frameworks.

API key: CBENCH_API_KEY from the environment, else from the project's .env file (gitignored;
see .env.example). Sent as "Authorization: Bearer <key>"; never logged or written to results."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty",
                  "frequency_penalty")


@dataclass
class ChatResult:
    content: str
    reasoning: str = ""            # reasoning_content, if the server separates thinking
    tool_calls: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0
    prefill_s: float | None = None    # prompt processing time, if the server reports it (llama.cpp)
    decode_s: float | None = None     # generation time, if the server reports it (llama.cpp)
    finish_reason: str = ""
    message: dict = field(default_factory=dict)   # raw assistant message, for tool loops


def api_key_from_env(env_file: Path = PROJECT_ROOT / ".env") -> str | None:
    if key := os.environ.get("CBENCH_API_KEY"):
        return key
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "CBENCH_API_KEY" and value.strip():
                return value.strip().strip('"').strip("'")
    return None


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 1800.0,
                 transport: httpx.BaseTransport | None = None):
        self.model = model
        key = api_key or api_key_from_env()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout, transport=transport)

    def chat(self, messages: list[dict], *, temperature: float | None, seed: int,
             max_tokens: int, tools: list[dict] | None = None, thinking: bool = True) -> ChatResult:
        """POST /v1/chat/completions; return content, tool calls and token usage.
        temperature=None omits the field so the server default applies. thinking=False asks the chat
        template to skip reasoning (enable_thinking=false; honoured by vLLM and llama.cpp --jinja)."""
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "seed": seed}
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
        if not thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        start = time.monotonic()
        resp = self._http.post("/v1/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}         # llama.cpp only
        return ChatResult(
            content=msg.get("content") or "",
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",   # llama.cpp | Ollama
            tool_calls=msg.get("tool_calls") or [],
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_s=time.monotonic() - start,
            prefill_s=timings["prompt_ms"] / 1000 if "prompt_ms" in timings else None,
            decode_s=timings["predicted_ms"] / 1000 if "predicted_ms" in timings else None,
            finish_reason=choice.get("finish_reason") or "",
            message=msg,
        )

    def backend(self) -> str:
        """Which server this is, from endpoints only it has: llama.cpp (/props), Ollama
        (/api/version), vLLM (/version); else "unknown"."""
        for path, name in (("/props", "llama.cpp"), ("/api/version", "ollama"), ("/version", "vllm")):
            try:
                if self._http.get(path).status_code == 200:
                    return name
            except httpx.HTTPError:
                pass
        return "unknown"

    def server_sampling(self) -> dict:
        """Effective default sampling settings (temperature, top_p, top_k, min_p, ...), e.g. from
        llama.cpp GET /props default_generation_settings; {} if the server doesn't expose them."""
        try:
            resp = self._http.get("/props")
            resp.raise_for_status()
            settings = resp.json().get("default_generation_settings", {})
        except (httpx.HTTPError, ValueError):
            return {}
        params = settings.get("params", settings)
        return {k: params[k] for k in _SAMPLING_KEYS if k in params}

    def count_tokens(self, text: str) -> int:
        """Exact count if the server exposes a tokenize endpoint, else an estimate."""
        try:
            resp = self._http.post("/tokenize", json={"content": text})
            resp.raise_for_status()
            return len(resp.json()["tokens"])
        except (httpx.HTTPError, ValueError, KeyError):
            return len(text) // 3 + 1   # conservative: errs towards "doesn't fit"

    def count_prompt_tokens(self, messages: list[dict]) -> int:
        """Tokens of the full chat prompt as the server will see it (chat template applied), via
        llama.cpp /apply-template + /tokenize, else vLLM's /tokenize with messages; falls back to
        counting the raw message text."""
        try:
            resp = self._http.post("/apply-template", json={"messages": messages})
            resp.raise_for_status()
            return self.count_tokens(resp.json()["prompt"])
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        try:
            resp = self._http.post("/tokenize", json={"model": self.model, "messages": messages})
            resp.raise_for_status()
            return int(resp.json()["count"])
        except (httpx.HTTPError, ValueError, KeyError):
            return sum(self.count_tokens(m.get("content") or "") + 8 for m in messages)
