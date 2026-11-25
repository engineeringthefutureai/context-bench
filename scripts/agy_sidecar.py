#!/usr/bin/env python3
"""Penalty grading through one long agy conversation per task, with recovery (stdlib only).

Runs on the machine that hosts Antigravity, from a checkout of this repository:

    cbench grade --mode penalty --judge agy --thinking off --export grading/queue/v4-off   # here, then push
    python3 scripts/agy_sidecar.py grading/queue/v4-off                                    # there, after pull
    cbench grade --mode penalty --judge agy --import grading/queue/v4-off/results.jsonl    # here, after pull

It drives `agy acp` (Agent Client Protocol over stdio). For every task@repo group it builds one judge dir
outside the checkout (repo/ copied from fixtures/, task.md, PENALTIES.md) and opens one conversation;
each patch of the group is a turn: patch.diff is rewritten, so only the current change is ever in the
dir, and the judge reads the repository once per conversation instead of once per patch. Every
`--rotate` patches a fresh conversation starts, so the context stays small.

Recovery, so a derailed call is steered or parked instead of lost:
  - every tool call is checked as it starts: only reading inside the judge dir (read-only commands from
    the allow-list below); anything else cancels the turn and the same conversation gets a neutral steer
  - a reply without the JSON, or a turn over --turn-timeout, gets "reply with the JSON only"
  - after --max-steers steers the patch is parked (parked.jsonl: conversation id, reason) and the group
    goes on in a fresh conversation; parked patches are retried with --retry-parked, and any parked
    conversation can be opened in Antigravity or with `agy -r <session>`
  - steers never mention a verdict or how a patch was made; each grade records its steers
  - with --min-quota, the sidecar waits for the 5-hour quota to refill instead of failing calls

Results (one JSON line per patch: id, patch_hash, penalties as the judge gave them, session, model,
usage, steers) go to <queue>/results.jsonl; reruns skip patches already there. The quote check and the
points happen on import (context_bench/penalty.py), as for every penalty grade.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from context_bench.penalty import JUDGE_PROMPT, parse_reply  # noqa: E402  (stdlib-only module)

NEXT_PROMPT = ("patch.diff in the current directory now holds a different change for the same task; task.md "
               "and repo/ are unchanged. Grade this change on its own against PENALTIES.md, without regard "
               "to earlier changes, and reply with the JSON it asks for, nothing else.")
STEER_SCOPE = ("Only the files in the current directory belong to this job, and only reading them is "
               "needed. Continue grading patch.diff against PENALTIES.md and reply with the JSON it asks for.")
STEER_REPLY = "Reply now with only the JSON object PENALTIES.md asks for, for the change in patch.diff."

# Same allow-list as [grading.judges].agy in config/experiment.toml, plus a few harmless readers.
READ_COMMANDS = {"echo", "ls", "find", "grep", "cat", "head", "tail", "sort", "uniq", "wc", "sed", "diff",
                 "nl", "tree", "awk", "cut", "rg", "pwd", "stat", "file", "less", "true"}
EDIT_KINDS = {"edit", "delete", "move", "switch_mode"}
_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(path: Path, rec: dict) -> None:
    with _LOCK, path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def load_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {json.loads(l)["id"] for l in path.read_text().splitlines() if l.strip()}


# ---- the guard -------------------------------------------------------------------------------------

def _outside(value: str, ws: Path) -> bool:
    """A path-like string that resolves outside the judge dir."""
    if not value or "\n" in value or len(value) > 400:
        return False
    if value.startswith(("/", "~")) or ".." in Path(value).parts:
        p = Path(os.path.expanduser(value))
        p = (p if p.is_absolute() else ws / p).resolve()
        return p != ws and ws not in p.parents
    return False


def check_command(cmd: str, ws: Path) -> str | None:
    if re.search(r"(^|[^2])>|\btee\b|\$\(|`|\bsudo\b", cmd):
        return f"command writes or substitutes: {cmd[:120]}"
    for part in re.split(r"\|\||&&|[|;]", cmd):
        try:
            words = shlex.split(part)
        except ValueError:
            return f"unparsable command: {cmd[:120]}"
        if not words:
            continue
        if words[0] == "cd":
            if len(words) > 1 and _outside(words[1], ws):
                return f"cd outside the judge dir: {cmd[:120]}"
            continue
        if words[0] == "xargs":                       # xargs [flags] CMD ...: check CMD instead
            words = [w for w in words[1:] if not w.startswith("-")] or ["xargs"]
        if words[0] not in READ_COMMANDS:
            return f"command not on the read-only list: {words[0]}"
        if words[0] == "sed" and any(w.startswith("-i") or w == "--in-place" for w in words[1:]):
            return "sed -i"
        if words[0] == "find" and any(w in ("-delete", "-exec", "-execdir", "-fprint") for w in words):
            return f"find with {cmd[:120]}"
        for w in words[1:]:
            if _outside(w, ws):
                return f"path outside the judge dir: {w}"
    return None


def check_tool(update: dict, ws: Path) -> str | None:
    """None if the tool call only reads inside ws, else why not."""
    name = (update.get("name") or "").lower()
    kind = update.get("kind") or ""
    args = update.get("rawInput") or update.get("args") or {}
    if kind in EDIT_KINDS or any(k in name for k in ("write", "edit", "browser", "replace", "create", "delete")):
        return f"tool {name or kind} changes files or leaves the dir"
    if kind == "execute" or "CommandLine" in args:
        cmd = args.get("CommandLine") or args.get("command") or ""
        if (why := check_command(str(cmd), ws)):
            return why
    for k, v in args.items():
        if k in ("CommandLine", "command", "toolSummary"):
            continue
        for s in (v if isinstance(v, list) else [v]):
            if isinstance(s, str) and _outside(s, ws):
                return f"{name or kind} reads outside the judge dir: {s}"
    return None


# ---- ACP --------------------------------------------------------------------------------------------

class Agent:
    """One `agy acp` process."""

    def __init__(self, log_dir: Path, agy: str = "agy"):
        self.p = subprocess.Popen([agy, "acp", "--sandbox"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=(log_dir / "agy-stderr.log").open("a"), text=True, bufsize=1)
        self.next_id = 0
        self.log_dir = log_dir
        self.log = None
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, timeout=60)

    def _read(self) -> None:
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, obj: dict) -> None:
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
        self.p.stdin.flush()

    def request(self, method: str, params: dict) -> int:
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        return self.next_id

    def wait(self, rid: int, timeout: float, on_message=None) -> dict:
        """Pump messages until the response to rid (TimeoutError after timeout seconds)."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise TimeoutError(rid)
            try:
                line = self.lines.get(timeout=min(left, 5))
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("agy acp closed its output")
            msg = json.loads(line)
            if self.log:
                self.log.write(line)
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError(f"request {rid}: {msg['error']}")
                return msg["result"]
            if on_message:
                on_message(msg)
            elif "method" in msg and "id" in msg:      # a request nobody handles: refuse it
                self.send({"id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}})

    def call(self, method: str, params: dict, timeout: float, on_message=None) -> dict:
        return self.wait(self.request(method, params), timeout, on_message)

    def close(self) -> None:
        if self.log:
            self.log.close()
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()


class Session:
    def __init__(self, agent: Agent, ws: Path, model: str | None):
        self.agent, self.ws = agent, ws
        r = agent.call("session/new", {"cwd": str(ws), "mcpServers": []}, timeout=120)
        self.id = r["sessionId"]
        self.model = (r.get("models") or {}).get("currentModelId")
        if model and model != self.model:
            agent.call("session/set_model", {"sessionId": self.id, "modelId": model}, timeout=60)
            self.model = model
        agent.log = (agent.log_dir / f"{self.id}.ndjson").open("a")

    def turn(self, text: str, timeout: float) -> dict:
        """One prompt. Returns {"text", "usage", "violation", "timeout"}."""
        out = {"text": "", "usage": {}, "violation": None, "timeout": False}

        def cancel(why: str):
            print(f"{now()} guard: {why}", file=sys.stderr, flush=True)
            if not out["violation"]:
                out["violation"] = why
                self.agent.send({"method": "session/cancel", "params": {"sessionId": self.id}})

        def on_message(msg: dict):
            method = msg.get("method")
            if method == "session/update":
                u = msg["params"]["update"]
                kind = u.get("sessionUpdate")
                if kind == "agent_message_chunk":
                    out["text"] += (u.get("content") or {}).get("text", "")
                elif kind == "tool_call" and (why := check_tool(u, self.ws)):
                    cancel(why)
            elif method == "session/request_permission":
                p = msg["params"]
                why = check_tool(p.get("toolCall") or {}, self.ws)
                want = "reject_once" if why else "allow_once"
                opt = next((o["optionId"] for o in p.get("options", []) if o.get("kind") == want), None)
                outcome = {"outcome": "selected", "optionId": opt} if opt else {"outcome": "cancelled"}
                self.agent.send({"id": msg["id"], "result": {"outcome": outcome}})
                if why:
                    cancel(why)
            elif "id" in msg and method:               # questions, forms: no one is there to answer
                self.agent.send({"id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}})

        rid = self.agent.request("session/prompt", {"sessionId": self.id, "prompt": [{"type": "text", "text": text}]})
        try:
            r = self.agent.wait(rid, timeout, on_message)
        except TimeoutError:
            out["timeout"] = True
            self.agent.send({"method": "session/cancel", "params": {"sessionId": self.id}})
            try:   # the cancelled turn still answers; wait for it so the next prompt starts clean
                r = self.agent.wait(rid, 120, on_message)
            except TimeoutError:
                raise RuntimeError("conversation stuck after cancel")
        out["usage"] = r.get("usage") or {}
        return out


# ---- quota ------------------------------------------------------------------------------------------

def wait_for_quota(minimum: float, agy: str) -> None:
    """Sleep while the Gemini 5-hour or weekly bucket is below minimum (`agy -p /quota` spends nothing)."""
    while True:
        try:
            r = subprocess.run([agy, "-p", "/quota", "--output-format", "json"], capture_output=True, text=True,
                               timeout=120)
            groups = json.loads(r.stdout)["command"]["data"]["groups"]
        except (ValueError, KeyError, subprocess.SubprocessError) as e:
            print(f"quota check failed ({e}), going on", file=sys.stderr)
            return
        low = [b for g in groups if g["name"].startswith("Gemini") for b in g["buckets"]
               if b["remaining_fraction"] < minimum]
        if not low:
            return
        b = min(low, key=lambda b: b["reset_time"])
        reset = datetime.fromisoformat(b["reset_time"].replace("Z", "+00:00"))
        wait = max(300, (reset - datetime.now(timezone.utc)).total_seconds() + 120)
        print(f"{now()} {b['name']} at {b['remaining_fraction']:.0%}, waiting {wait / 60:.0f} min", file=sys.stderr,
              flush=True)
        time.sleep(wait)


# ---- grading ----------------------------------------------------------------------------------------

def make_workspace(work: Path, name: str, size: str, queue: Path, task: str) -> Path:
    ws = (work / name).resolve()
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(ROOT / "fixtures" / f"repo_{size}", ws / "repo",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copyfile(queue / "tasks" / f"{task}.md", ws / "task.md")
    shutil.copyfile(queue / "PENALTIES.md", ws / "PENALTIES.md")
    return ws


def grade_group(group: str, items: list[dict], args, queue: Path) -> None:
    results, parked = queue / "results.jsonl", queue / "parked.jsonl"
    work = Path(args.work_dir)
    agent = Agent(work / "logs", args.agy)
    session, in_session = None, 0
    try:
        for it in items:
            if args.min_quota:
                wait_for_quota(args.min_quota, args.agy)
            if session is None or in_session >= args.rotate:
                ws = make_workspace(work, group, it["repo_size"], queue, it["task"])
                session, in_session = Session(agent, ws, args.model), 0
            (session.ws / "patch.diff").write_text((queue / "patches" / f"{it['id']}.diff").read_text())
            prompt = JUDGE_PROMPT if in_session == 0 else NEXT_PROMPT
            in_session += 1
            steers, usage, penalties, reason, t0 = [], {}, None, "", time.time()
            for _ in range(args.max_steers + 1):
                res = session.turn(prompt, args.turn_timeout)
                for k, v in res["usage"].items():
                    if isinstance(v, (int, float)):
                        usage[k] = usage.get(k, 0) + v
                if res["violation"] or res["timeout"]:
                    reason = res["violation"] or f"no reply within {args.turn_timeout:.0f} s"
                    steers.append(reason)
                    prompt = STEER_SCOPE if res["violation"] else STEER_REPLY
                    continue
                try:
                    penalties = parse_reply(res["text"])
                    break
                except ValueError:
                    reason = "reply without the JSON"
                    steers.append(reason)
                    prompt = STEER_REPLY
            rec = {"id": it["id"], "patch_hash": it["patch_hash"], "task": it["task"], "repo_size": it["repo_size"],
                   "session": session.id, "model": session.model, "usage": usage, "steers": steers,
                   "seconds": round(time.time() - t0), "graded_at": now()}
            if penalties is None:
                append(parked, {**rec, "reason": reason})
                print(f"{now()} {group} {it['id']}: parked ({reason}); session {session.id}", file=sys.stderr, flush=True)
                session = None                     # the rest of the group starts over in a fresh conversation
                continue
            append(results, {**rec, "penalties": penalties})
            print(f"{now()} {group} {it['id']}: {len(penalties)} penalties, {usage.get('totalTokens', '?')} tokens"
                  + (f", {len(steers)} steers" if steers else ""), file=sys.stderr, flush=True)
    except (RuntimeError, OSError, ValueError) as e:
        print(f"{now()} {group}: stopped ({e}); rerun to continue", file=sys.stderr, flush=True)
    finally:
        agent.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("queue", type=Path, help="a dir written by cbench grade --mode penalty --export")
    ap.add_argument("--work-dir", default=str(Path.home() / ".cache" / "cbench-judge"),
                    help="judge dirs and conversation logs, outside the checkout")
    ap.add_argument("--jobs", type=int, default=1, help="groups graded at once, one agy process each")
    ap.add_argument("--rotate", type=int, default=10, help="patches per conversation before a fresh one")
    ap.add_argument("--max-steers", type=int, default=2)
    ap.add_argument("--turn-timeout", type=float, default=900)
    ap.add_argument("--min-quota", type=float, default=0.1,
                    help="wait while the 5-hour or weekly Gemini quota is below this fraction (0: never)")
    ap.add_argument("--model", help="agy model id (default: agy's default; recorded on every grade)")
    ap.add_argument("--only", action="append", help="only these groups, e.g. reuse-007@M (repeatable)")
    ap.add_argument("--limit", type=int, help="at most this many patches")
    ap.add_argument("--retry-parked", action="store_true")
    ap.add_argument("--agy", default="agy")
    args = ap.parse_args()

    queue = args.queue.resolve()
    work = Path(args.work_dir).resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work-dir must be outside the checkout")
    (work / "logs").mkdir(parents=True, exist_ok=True)
    skip = load_ids(queue / "results.jsonl") | (set() if args.retry_parked else load_ids(queue / "parked.jsonl"))
    items = [json.loads(l) for l in (queue / "manifest.jsonl").read_text().splitlines() if l.strip()]
    items = [it for it in items if it["id"] not in skip
             and (not args.only or f"{it['task']}@{it['repo_size']}" in args.only)][:args.limit]
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(f"{it['task']}@{it['repo_size']}", []).append(it)
    print(f"{len(items)} patches in {len(groups)} groups, {args.jobs} at a time", file=sys.stderr, flush=True)

    pending = list(groups.items())
    threads: list[threading.Thread] = []
    while pending or threads:
        threads = [t for t in threads if t.is_alive()]
        while pending and len(threads) < max(1, args.jobs):
            g, its = pending.pop(0)
            t = threading.Thread(target=grade_group, args=(g, its, args, queue), daemon=True)
            t.start()
            threads.append(t)
        time.sleep(2)


if __name__ == "__main__":
    main()
