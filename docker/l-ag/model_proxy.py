"""Model proxy for L-AG (the model-proxy service in docker/l-ag/compose.yaml): forwards every request to the model
API and adds its key, so the key never enters the agent's container.

L_AG_PROXY_UPSTREAM: the API (http or https), e.g. our vLLM on the host or https://generativelanguage.googleapis.com.
L_AG_PROXY_AUTH: how the key is sent: "bearer" (Authorization: Bearer, OpenAI-compatible servers) or "goog"
(x-goog-api-key, the Gemini API). Whatever key the client sent is dropped first (headers and a ?key= parameter).
L_AG_PROXY_KEY: the key. Never logged; requests are logged as method, path and status only.
L_AG_PROXY_RPM / L_AG_PROXY_TPM: throttling, at most this many model calls (POSTs) / estimated input tokens (body
size / 4) in any 60 s; the rest wait (0 or unset: none).
Per proxy, so per run: runs in parallel against one quota share it only if each gets a part of it.

L_AG_PROXY_THINKING: "on" or "off" sets chat_template_kwargs.enable_thinking on every OpenAI-compatible chat
request (the harness's OpenAI client has no thinking setting); unset forwards requests as sent. What the client
itself asked for (its top-level keys and any thinking fields) is logged with each call.
L_AG_PROXY_CAPTURE: a file for the first model request's body, written once (to measure the harness's system prompt
and tool definitions); unset by default.
L_AG_PROXY_USAGE: a file for one JSON line per model call: status, request size, throttle wait, latency and the
token usage the API reported (prompt, cached, output, thinking). On the proxy's own mount, not the agent's.

Responses are streamed through as they arrive (server-sent events for streaming calls). Stdlib only.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit

UPSTREAM = urlsplit(os.environ.get("L_AG_PROXY_UPSTREAM", "http://host.docker.internal:8080"))
AUTH = os.environ.get("L_AG_PROXY_AUTH", "bearer")
KEY = os.environ.get("L_AG_PROXY_KEY", "")
RPM = float(os.environ.get("L_AG_PROXY_RPM") or 0)      # requests per minute to the model API; 0 = no limit
TPM = float(os.environ.get("L_AG_PROXY_TPM") or 0)      # input tokens per minute (estimated); 0 = no limit
CHARS_PER_TOKEN = 4.0                                    # request bytes per input token (Gemini, measured: 3.95-4.41)


class Throttle:
    """At most `rpm` requests and `tpm` (estimated) input tokens in any 60-second window (sliding): a request that
    would exceed either waits. A single request larger than tpm goes alone once the window is empty."""

    def __init__(self, rpm: float, tpm: float = 0, clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm, self.clock, self.sleep = rpm, tpm, clock, sleep
        self.sent: deque[tuple[float, float]] = deque()      # (time, tokens)
        self.lock = threading.Lock()

    def wait(self, tokens: float = 0) -> float:
        """Blocks until the request may go; returns the seconds waited."""
        if self.rpm <= 0 and self.tpm <= 0:
            return 0.0
        waited = 0.0
        with self.lock:                      # one waiter at a time keeps the order and the count exact
            while True:
                now = self.clock()
                while self.sent and now - self.sent[0][0] >= 60:
                    self.sent.popleft()
                ok_rpm = self.rpm <= 0 or len(self.sent) < self.rpm
                ok_tpm = self.tpm <= 0 or not self.sent or sum(t for _, t in self.sent) + tokens <= self.tpm
                if ok_rpm and ok_tpm:
                    self.sent.append((now, tokens))
                    return waited
                pause = 60 - (now - self.sent[0][0]) + 0.05
                self.sleep(pause)
                waited += pause


THROTTLE = Throttle(RPM, TPM)
USAGE_LOG = os.environ.get("L_AG_PROXY_USAGE")           # one JSON line per model call (the proxy's own mount)
CAPTURE = os.environ.get("L_AG_PROXY_CAPTURE")          # write the first model request's body here, once (opt-in)
_log_lock = threading.Lock()


def usage_of(body: bytes) -> dict | None:
    """Token usage from a model response, as the API reported it: a JSON body, or the last server-sent event that
    carries usage (streaming). Gemini: usageMetadata; OpenAI-compatible: usage. None when there is none."""
    docs = []
    try:
        docs.append(json.loads(body))
    except ValueError:
        for line in body.splitlines():
            if line.startswith(b"data:"):
                try:
                    docs.append(json.loads(line[5:].strip()))
                except ValueError:
                    pass
    for d in reversed(docs):
        for d2 in (d if isinstance(d, list) else [d]):
            if not isinstance(d2, dict):
                continue
            if u := d2.get("usageMetadata"):
                return {"prompt": u.get("promptTokenCount"), "cached": u.get("cachedContentTokenCount"),
                        "output": u.get("candidatesTokenCount"), "thinking": u.get("thoughtsTokenCount")}
            if u := d2.get("usage"):
                return {"prompt": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
                        "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                        "thinking": (u.get("completion_tokens_details") or {}).get("reasoning_tokens")}
    return None


def log_call(record: dict) -> None:
    if not USAGE_LOG:
        return
    with _log_lock, open(USAGE_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
THINKING = os.environ.get("L_AG_PROXY_THINKING", "")    # "on" / "off": set on OpenAI-compatible chat requests; "" = as sent


def apply_thinking(body: bytes, thinking: str = THINKING) -> tuple[bytes, dict]:
    """OpenAI-compatible chat requests (a JSON body with "messages"): set chat_template_kwargs.enable_thinking to
    the run's setting (Qwen's switch, as the harness's own client sends it), overriding whatever the client sent.
    Returns the body to forward and what the client itself had asked for (logged once per call)."""
    try:
        doc = json.loads(body)
    except ValueError:
        return body, {}
    if not isinstance(doc, dict) or "messages" not in doc:
        return body, {}
    seen = {"keys": sorted(doc)}
    for k in ("chat_template_kwargs", "reasoning_effort", "thinking"):
        if k in doc:
            seen[k] = doc[k]
    if thinking not in ("on", "off"):
        return body, seen
    doc["chat_template_kwargs"] = {**(doc.get("chat_template_kwargs") or {}), "enable_thinking": thinking == "on"}
    return json.dumps(doc).encode(), seen


DROP = {"connection", "keep-alive", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
        "host", "content-length", "authorization", "x-goog-api-key"}


def upstream_path(path: str) -> str:
    """The request path without any ?key= parameter, under the upstream's own path prefix."""
    parts = urlsplit(path)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "key"])
    return UPSTREAM.path.rstrip("/") + parts.path + (f"?{query}" if query else "")


def auth_headers() -> dict[str, str]:
    if not KEY:
        return {}
    return {"x-goog-api-key": KEY} if AUTH == "goog" else {"Authorization": f"Bearer {KEY}"}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"     # each response ends when the connection closes: streams pass through as is

    def _forward(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        waited, client_asked = 0.0, {}
        if self.command == "POST":           # model calls; GETs (model lists) are free
            if CAPTURE and not os.path.exists(CAPTURE):
                with open(CAPTURE, "wb") as f:   # as the harness sent it, before the thinking switch
                    f.write(body)
            body, client_asked = apply_thinking(body)
            waited = THROTTLE.wait(len(body) / CHARS_PER_TOKEN)
        t0, status, received = time.monotonic(), None, bytearray()
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP} | auth_headers()
        cls = http.client.HTTPSConnection if UPSTREAM.scheme == "https" else http.client.HTTPConnection
        conn = cls(UPSTREAM.hostname, UPSTREAM.port, timeout=3600)
        try:
            conn.request(self.command, upstream_path(self.path), body=body or None, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in DROP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := resp.read1(65536):
                received += chunk
                self.wfile.write(chunk)
                self.wfile.flush()
        except (ConnectionError, OSError) as e:
            self.send_error(502, f"model API unreachable: {type(e).__name__}")
        finally:
            conn.close()
            if self.command == "POST":
                log_call({"t": round(time.time(), 3), "path": self.path.split("?")[0], "status": status,
                          "request_bytes": len(body), "throttle_wait_s": round(waited, 2),
                          "latency_s": round(time.monotonic() - t0, 2), **(usage_of(bytes(received)) or {}),
                          **({"client_asked": client_asked} if client_asked else {})})

    do_GET = do_POST = _forward

    def log_message(self, fmt, *args):   # method, path and status only; never headers or the query
        sys.stderr.write("%s %s\n" % (self.command, (fmt % args).split("?")[0]))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Proxy).serve_forever()
