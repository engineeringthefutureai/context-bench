"""L-AG (experimental, 2026-11-25): the Antigravity SDK's agent harness as the solver, on the same model.

The other levels build the model's context with this project's code. L-AG hands the whole job to Google's
Antigravity harness (`google-antigravity`, its bundled Go runtime "localharness") pointed at the model under
study through its OpenAI-compatible client (`LocalOpenAIAgentConfig`). The harness brings its own system
prompt, tools (list_directory, search_directory, find_file, view_file, create_file, edit_file, run_command)
and loop; it edits the run's repository copy, and the runner scores the copy's diff the way it scores L8. So
L7/L8 vs L-AG compares agent harnesses with the model held fixed.

A documented exception to hard rule 1 (only <context> varies): the system prompt and tools are the
harness's, as shipped. The user turn is the task text and one line saying where the repository is; nothing
about reuse, tests or dead code. Unlike every other level, L-AG can run commands. Off for blindness: web
search, URL reading (the project's repository is public), image generation, asking the user (no one
answers), subagents.

Confinement: every run happens in a container (docker/l-ag: Dockerfile, compose.yaml, solve.py). The only
host paths it sees are the run's repository copy (/workspace) and a folder with its prompt and result
(/out); the image holds the SDK and solve.py, nothing of the project, so hidden tests, other tasks, results
and this harness are out of reach (rule 6) and the local filesystem is safe. Root filesystem read-only, no
capabilities, internet allowed. The model server is reached through a proxy service in its own container
(docker/l-ag/model_proxy.py), which adds the API key, so the key never enters the agent's container. A fresh
container means no ~/.gemini rules, memories or settings.

Thinking: the harness's OpenAI client has no thinking setting, so on that API the model proxy sets Qwen's
chat_template_kwargs.enable_thinking on every request from the run's thinking flag, the same switch the other
levels use. On the Gemini API the model's own default applies (thinking-off configs are not implemented there).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path

from ..types import ContextBlock, Task
from ..workspace import Workspace

DOCKER_DIR = Path(__file__).resolve().parents[2] / "docker" / "l-ag"
COMPOSE = ["docker", "compose", "-f", str(DOCKER_DIR / "compose.yaml")]
WORKSPACE = "/workspace"                 # where the repository copy is, inside the container

USER_TEMPLATE = """{task}

The repository is your workspace ({workspace}). Make the change in its files."""

MAX_MODEL_CALLS = 100        # a safety cap, not a budget the agent sees; hitting it is recorded
TIMEOUT_S = 3600             # wall clock per run (the container is killed a little after)
COMMAND_TIMEOUT_S = 300      # per run_command call


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    """Nothing is prebuilt: the harness explores the workspace itself."""
    return ContextBlock(text="")


@dataclass
class AgRun:
    content: str = ""                 # the agent's final reply
    reasoning: str | None = None      # thinking of all steps, joined
    tokens_in: int = 0                # prompt tokens over every model call
    tokens_cached: int | None = None
    tokens_out: int = 0               # candidates + thoughts
    tokens_thinking: int | None = None
    latency_s: float = 0.0
    finish_reason: str = "stop"       # stop | timeout | harness_stop | error
    model_calls: int = 0
    tool_calls: int = 0
    tool_counts: dict = field(default_factory=dict)
    tool_errors: int = 0
    stop_reason: str | None = None    # the harness's StopReason (budget caps, quota)
    transcript: list[dict] = field(default_factory=list)
    tool_output: str = ""             # every tool result's text, for helper_in_context
    harness: str | None = None        # "antigravity-sdk <version>"
    error: str | None = None
    no_fit: bool = False
    peak_ctx_tokens: int = 0
    calls: list[dict] = field(default_factory=list)   # per model call, from the proxy: tokens, latency, throttle wait


def user_prompt(task: Task) -> str:
    return USER_TEMPLATE.format(task=task.prompt.strip(), workspace=WORKSPACE)


def upstream_url(base_url: str) -> str:
    """The model server as the proxy container reaches it: the host's localhost is host.docker.internal."""
    url = re.sub(r"//(localhost|127\.0\.0\.1)(?=[:/]|$)", "//host.docker.internal", base_url.rstrip("/"))
    return url.removesuffix("/v1")


GEMINI_API = "https://generativelanguage.googleapis.com"


def proxy_env(api: str, base_url: str) -> dict[str, str]:
    """The model proxy's upstream, how it sends the key, and the key (from the environment or .env, never
    stored elsewhere): "openai" = our OpenAI-compatible server with CBENCH_API_KEY as a bearer token;
    "gemini" = the Gemini API with GEMINI_API_KEY as x-goog-api-key."""
    from ..llm import api_key_from_env, env_value
    if api == "gemini":
        key = env_value("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("L-AG with the Gemini API needs GEMINI_API_KEY (environment or .env)")
        return {"L_AG_API": "gemini", "L_AG_PROXY_UPSTREAM": base_url.rstrip("/") or GEMINI_API,
                "L_AG_PROXY_AUTH": "goog", "L_AG_PROXY_KEY": key}
    if api == "openai":
        return {"L_AG_API": "openai", "L_AG_PROXY_UPSTREAM": upstream_url(base_url), "L_AG_PROXY_AUTH": "bearer",
                "L_AG_PROXY_KEY": api_key_from_env() or ""}
    raise ValueError(f"unknown api {api!r}")


def run_ag(task: Task, sandbox: Path, *, base_url: str, model: str, api: str = "openai", rpm: float = 0, tpm: float = 0,
           thinking: bool | None = None,
           max_model_calls: int = MAX_MODEL_CALLS, timeout_s: float = TIMEOUT_S) -> AgRun:
    """One Antigravity session in the l-ag container on the repository copy at `sandbox` (changed in place).
    Each run is its own compose project (the agent and its model proxy), taken down afterwards. api: "openai"
    (an OpenAI-compatible server such as our vLLM) or "gemini" (the Gemini API); the model is replaceable
    through the model entry in config/experiment.toml. rpm, tpm: the model entry's throttle (model calls / estimated
    input tokens per minute, 0 = none), enforced by the proxy. thinking: on the "openai" API the proxy sets Qwen's
    chat_template_kwargs.enable_thinking on every request (None: as the harness sends it, i.e. the server's default)."""
    with tempfile.TemporaryDirectory(prefix="l-ag-out-") as out, tempfile.TemporaryDirectory(prefix="l-ag-usage-") as usage:
        out_dir, usage_dir = Path(out), Path(usage)
        (out_dir / "prompt.txt").write_text(user_prompt(task))
        project = f"l-ag-{uuid.uuid4().hex[:12]}"
        compose = [*COMPOSE, "-p", project]
        env = {**os.environ, "L_AG_WORKSPACE": str(sandbox.resolve()), "L_AG_OUT": str(out_dir),
               "L_AG_USAGE": str(usage_dir),
               "L_AG_MODEL": model, **proxy_env(api, base_url), "L_AG_PROXY_RPM": str(rpm or 0),
               "L_AG_PROXY_TPM": str(tpm or 0),
               "L_AG_PROXY_THINKING": "" if thinking is None else ("on" if thinking else "off"),
               "L_AG_MAX_MODEL_CALLS": str(max_model_calls), "L_AG_TIMEOUT_S": str(timeout_s),
               "L_AG_COMMAND_TIMEOUT_S": str(COMMAND_TIMEOUT_S),
               "L_AG_UID": str(os.getuid()), "L_AG_GID": str(os.getgid())}
        try:
            # --build: the image follows docker/l-ag (cached layers, so normally a no-op)
            proc = subprocess.run([*compose, "run", "--rm", "--build", "l-ag"], env=env,
                                  capture_output=True, text=True, timeout=timeout_s + 300)
        except subprocess.TimeoutExpired:
            proc = None
        finally:
            subprocess.run([*compose, "down", "--remove-orphans", "--timeout", "5"], env=env, capture_output=True)
        if proc is None:
            return AgRun(error=f"container killed after {timeout_s + 300:.0f} s", finish_reason="timeout")
        result = out_dir / "result.json"
        if not result.is_file():
            tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
            return AgRun(error=f"no result from the container (exit {proc.returncode}): {tail}", finish_reason="error")
        data = json.loads(result.read_text())
        log = usage_dir / "calls.jsonl"
        calls = [json.loads(x) for x in log.read_text().splitlines() if x.strip()] if log.is_file() else []
    known = {f.name for f in fields(AgRun)}
    run = AgRun(**{k: v for k, v in data.items() if k in known})
    return with_calls(run, calls)


def with_calls(run: AgRun, calls: list[dict]) -> AgRun:
    """Per-call usage from the proxy fills what the harness reports only as totals: model calls, peak context, and
    (when every call reported usage) the token totals themselves, as the API counted them."""
    run.calls = calls
    prompts = [c["prompt"] for c in calls if c.get("prompt") is not None]
    if not calls:
        return run
    run.model_calls = len(calls)
    run.peak_ctx_tokens = max(prompts, default=run.peak_ctx_tokens)
    if prompts and len(prompts) == len(calls):
        run.tokens_in = sum(prompts)
        run.tokens_cached = sum(c.get("cached") or 0 for c in calls)
        run.tokens_thinking = sum(c.get("thinking") or 0 for c in calls)
        run.tokens_out = sum(c.get("output") or 0 for c in calls) + run.tokens_thinking
    return run


def percentiles(values: list[int], ps=(50, 95, 98, 100)) -> dict:
    """Nearest-rank percentiles (p100 = the maximum)."""
    v = sorted(values)
    if not v:
        return {}
    import math
    return {f"p{p}": v[max(0, math.ceil(p / 100 * len(v)) - 1)] for p in ps}


QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "Error 429", "QUOTA_EXHAUSTED", "exceeded your current quota")


class ModelQuotaError(RuntimeError):
    """The model API refused for quota: not a result. The runner raises it instead of recording the run."""


def quota_detail(run: AgRun) -> str:
    """The quota that was hit, from the API's message (e.g. its metric and limit), for the error."""
    import re as _re
    text = run.error or run.stop_reason or ""
    found = _re.findall(r"Quota exceeded for metric: [^\n]*?(?:limit: \d+, model: [\w.-]+)", text)
    return "; ".join(dict.fromkeys(found)) or text[:300]


def quota_error(run: AgRun) -> bool:
    text = " ".join(x for x in (run.error, run.stop_reason) if x)
    return any(m in text for m in QUOTA_MARKERS)


OUTPUT_ARGS = ("combined_output", "output", "results", "matches")   # tool output the harness puts in a call's args


def seen_text(run: AgRun, repo: Path) -> str:
    """What the agent saw, for helper_in_context and the other visibility metrics. The harness runs its built-in
    tools in its Go runtime, so their results never reach the SDK as ToolResults; the finished steps' arguments
    say what was shown instead: view_file's file and line range (read here from the original repository, 0-based,
    end inclusive) and run_command's combined_output. Plus any tool results the stream did carry."""
    parts = [run.tool_output] if run.tool_output else []
    for step in run.transcript:
        for call in step.get("tool_calls", ()):
            args = call.get("args") or {}
            if not isinstance(args, dict):
                continue
            if call.get("name") == "view_file" and args.get("file_path"):
                rel = str(args["file_path"]).removeprefix(WORKSPACE).lstrip("/")
                path = (repo / rel).resolve()
                if path.is_file() and path.is_relative_to(repo.resolve()):
                    lines = path.read_text(errors="replace").splitlines()
                    start, end = int(args.get("start_line") or 0), args.get("end_line")
                    parts.append("\n".join(lines[start:None if end is None else int(end) + 1]))
            parts.extend(str(args[k]) for k in OUTPUT_ARGS if args.get(k))
    return "\n\n".join(parts)

