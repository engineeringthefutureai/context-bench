"""L-AG solver, run inside the l-ag container (docker/l-ag/compose.yaml): one Antigravity session on /workspace.

Standalone on purpose (stdlib + google-antigravity only): the image holds this file and the SDK, nothing of the
project (no tasks, hidden tests, fixtures, results or harness code), so the agent can't find any of it.

Inputs: /out/prompt.txt (the user turn) and the environment (L_AG_API, L_AG_MODEL, L_AG_BASE_URL, L_AG_MAX_MODEL_CALLS,
L_AG_TIMEOUT_S, L_AG_COMMAND_TIMEOUT_S). Output: /out/result.json (the fields of AgRun in
context_bench/levels/l_ag_antigravity.py). The changes are made in /workspace, the run's repository copy.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

WORKSPACE = Path(os.environ.get("L_AG_WORKSPACE", "/workspace"))
OUT = Path(os.environ.get("L_AG_OUT", "/out"))


def config(*, base_url: str, model: str, home: Path, max_model_calls: int, command_timeout_s: float,
           api: str = "openai"):
    """The harness as shipped, minus what would break blindness or hang a headless run: web search and URL
    reading (the project is public), images, asking the user, subagents. All tool calls are allowed.
    api "openai": an OpenAI-compatible server (our vLLM) through LocalOpenAIAgentConfig; "gemini": the Gemini
    API through LocalAgentConfig. Either way base_url is the model proxy, which adds the real key."""
    from google.antigravity import LocalAgentConfig, LocalOpenAIAgentConfig, types
    from google.antigravity.hooks import policy
    from google.antigravity.models import GeminiAPIEndpoint
    off = [types.BuiltinTools.SEARCH_WEB, types.BuiltinTools.READ_URL_CONTENT, types.BuiltinTools.GENERATE_IMAGE,
           types.BuiltinTools.ASK_QUESTION, types.BuiltinTools.INVOKE_SUBAGENT, types.BuiltinTools.RUN_WORKFLOW,
           types.BuiltinTools.SCHEDULE]
    if api == "gemini":
        target = types.ModelTarget(name=model, endpoint=GeminiAPIEndpoint(base_url=base_url.removesuffix("/v1"),
                                                                          api_key="added-by-the-proxy"))
        # the config checks for a key of its own too; the proxy replaces whatever is sent with the real one
        make = lambda **kw: LocalAgentConfig(model=target, api_key="added-by-the-proxy", **kw)
    elif api == "openai":
        make = lambda **kw: LocalOpenAIAgentConfig(model=model, base_url=base_url, **kw)
    else:
        raise ValueError(f"unknown api {api!r}")
    return make(
        capabilities=types.CapabilitiesConfig(
            enable_subagents=False, disabled_tools=off,
            run_command_config=types.RunCommandConfig(timeout_seconds=command_timeout_s)),
        policies=[policy.allow_all()],
        budget_config=types.BudgetConfig(max_model_calls=max_model_calls),
        # rate limits (429) and throttling (503) are waited out with backoff, not failed: the SDK's preset for
        # benchmarks (the run's wall-clock cap still bounds it; the wait counts in latency_s)
        retry_config=types.RetryConfig.benchmark(),
        workspaces=[str(WORKSPACE)],
        app_data_dir=str(home / "app"), save_dir=str(home / "save"),
        env={"HOME": str(home / "home"), "XDG_CONFIG_HOME": str(home / "home" / ".config")},
    )


async def session(prompt: str, *, base_url: str, model: str, home: Path, max_model_calls: int,
                  timeout_s: float, command_timeout_s: float, api: str = "openai") -> dict:
    from google.antigravity import Agent
    run = {"content": "", "finish_reason": "stop", "error": None}
    t0 = time.monotonic()
    try:
        cfg = config(base_url=base_url, model=model, home=home, max_model_calls=max_model_calls,
                     command_timeout_s=command_timeout_s, api=api)
        async with Agent(cfg) as agent:
            results: list[dict] = []          # every tool result, from the response stream (steps don't carry them)
            try:
                response = await asyncio.wait_for(agent.chat(prompt), timeout_s)
                await asyncio.wait_for(_tool_results(response, results), max(1.0, timeout_s - (time.monotonic() - t0)))
                run["content"] = await response.text()
            except asyncio.TimeoutError:
                run["error"], run["finish_reason"] = f"timed out after {timeout_s:.0f} s", "timeout"
                await agent.conversation.cancel()
            run.update(collect(agent.conversation, results))
    except Exception as e:                     # the harness failed to start or died: a result, not a crash
        run["error"], run["finish_reason"] = f"{type(e).__name__}: {e}", "error"
    if run.get("stop_reason") and not run["error"]:
        run["error"], run["finish_reason"] = f"stopped by the harness: {run['stop_reason']}", "harness_stop"
    run["latency_s"] = round(time.monotonic() - t0, 2)
    return run


async def _tool_results(response, results: list[dict]) -> None:
    """Read the response stream to its end, keeping the tool results (the text and thinking deltas are in the
    steps too). Never shortened: the runner checks them for the needed helper (helper_in_context)."""
    from google.antigravity import types
    async for chunk in response.chunks:
        if isinstance(chunk, types.ToolResult):
            results.append({"name": str(chunk.name), "result": _text(chunk.result), "error": chunk.error})


def _text(v) -> str:
    return v if isinstance(v, str) else json.dumps(_jsonable(v))


def _name(v) -> str:
    return str(getattr(v, "value", v))


def collect(conv, results: list[dict] | None = None) -> dict:
    """Steps, tool calls and token usage from the finished conversation. The history holds a snapshot per status
    update (a tool call shows up ACTIVE several times, then DONE), so only each step's last snapshot counts."""
    last: dict = {}
    for i, s in enumerate(conv.history):
        last[getattr(s, "id", "") or f"#{i}"] = s     # dicts keep first-insertion order: the steps stay in order
    steps = list(last.values())
    results = results or []
    counts, thinking, outputs, transcript, tool_errors = Counter(), [], [r["result"] for r in results], [], 0
    for s in steps:
        rec = {"type": _name(s.type), "source": _name(s.source), "status": _name(s.status), "content": s.content}
        if s.thinking:
            rec["thinking"] = s.thinking
            thinking.append(s.thinking)
        if s.tool_calls:
            rec["tool_calls"] = [{"name": str(c.name), "args": _jsonable(c.args)} for c in s.tool_calls]
            counts.update(str(c.name) for c in s.tool_calls)
        if s.error:
            rec["error"] = s.error
            tool_errors += bool(s.tool_calls)
        transcript.append(rec)
    if results:
        transcript.append({"type": "TOOL_RESULTS", "results": results})
    tool_errors = max(tool_errors, sum(1 for r in results if r["error"]))
    # per-step usage (deprecated in the SDK, but the only per-call view): model calls and the largest prompt
    per_call = [s.usage_metadata.prompt_token_count for s in steps
                if s.usage_metadata is not None and s.usage_metadata.prompt_token_count]
    u = conv.total_usage
    stop = conv._last_turn_stop_reason
    stop = _name(stop) if stop is not None else None
    return {
        "transcript": transcript, "tool_counts": dict(counts), "tool_calls": sum(counts.values()),
        "tool_errors": tool_errors, "tool_output": "\n\n".join(outputs), "reasoning": "\n\n".join(thinking) or None,
        # None when the harness reports no per-call usage (only totals), not 0
        "model_calls": len(per_call) or None, "peak_ctx_tokens": max(per_call, default=0),
        "tokens_in": u.prompt_token_count or 0, "tokens_cached": u.cached_content_token_count,
        "tokens_thinking": u.thoughts_token_count,
        "tokens_out": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
        "stop_reason": None if stop in (None, "UNSPECIFIED") else stop,
    }


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return repr(v)


def main() -> int:
    prompt = (OUT / "prompt.txt").read_text()
    env = os.environ
    with tempfile.TemporaryDirectory(prefix="l-ag-") as tmp:
        home = Path(tmp)
        for d in ("home", "app", "save"):
            (home / d).mkdir()
        run = asyncio.run(session(prompt, base_url=env["L_AG_BASE_URL"], model=env["L_AG_MODEL"], home=home,
                                  max_model_calls=int(env.get("L_AG_MAX_MODEL_CALLS", "100")),
                                  timeout_s=float(env.get("L_AG_TIMEOUT_S", "3600")),
                                  command_timeout_s=float(env.get("L_AG_COMMAND_TIMEOUT_S", "300")),
                                  api=env.get("L_AG_API", "openai")))
    from importlib.metadata import version
    run["harness"] = "antigravity-sdk " + version("google-antigravity")
    (OUT / "result.json").write_text(json.dumps(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
