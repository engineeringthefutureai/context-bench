#!/usr/bin/env python3
"""Penalty grading in Antigravity conversations, started through `agy agentapi` (stdlib only).

Run it from agy (ask a conversation to run it) or as an Antigravity sidecar: either way it inherits
ANTIGRAVITY_LS_ADDRESS, ANTIGRAVITY_CSRF_TOKEN and ANTIGRAVITY_PROJECT_ID, which `agy agentapi` needs.
The graders are ordinary conversations in the Antigravity app, so they can be watched and steered there.
agy has to run it outside its terminal sandbox: inside it the local RPC port that agentapi talks to is
unreachable (`agentapi new-conversation` prints {"response": {"newConversation": {"conversationId": ...}}}).

    cbench grade --mode penalty --judge agy --thinking off --export grading/queue/v4-off   # here, then push
    python3 scripts/agy_grader.py grading/queue/v4-off                                     # from agy, after pull
    cbench grade --mode penalty --judge agy --import grading/queue/v4-off/results.jsonl    # here, after pull

For every task@repo group it builds one judge dir outside the checkout (repo/ copied from fixtures/,
task.md, PENALTIES.md) and starts one conversation (`agentapi new-conversation`). Each patch of the group
is one message: patch.diff is rewritten first, so only the current change is ever in the dir, and the
grader writes its JSON reply to verdicts/<id>.json in that dir, which this script waits for. The next
patch goes to the same conversation (`agentapi send-message`), so the repository is read once per
conversation, not once per patch; a fresh conversation starts every --rotate patches.

Recovery: no verdict within --turn-timeout, or one without the JSON, gets a neutral message ("write the
JSON for patch.diff to <file>"); after --max-steers the patch is parked (parked.jsonl, with the
conversation id, which stays open in the app) and the group goes on in a fresh conversation. Messages
never mention a verdict or how a patch was made; each grade records the messages it took. With
--min-quota the script waits for the 5-hour quota to refill. Reruns skip patches already graded;
--retry-parked takes parked ones again. The quote check and the points happen on import, as for every
penalty grade.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
from context_bench.penalty import parse_reply  # noqa: E402  (stdlib-only module)

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LOCK = threading.Lock()


def first_prompt(ws: Path, out: Path) -> str:
    return (f"Your working directory for this job is {ws}; only the files in it belong to the job, and "
            f"only reading them is needed. Follow {ws}/PENALTIES.md: read task.md and patch.diff there, "
            f"look at repo/ as needed, then write the JSON it asks for, nothing else, to the file {out}.")


def next_prompt(ws: Path, out: Path) -> str:
    return (f"{ws}/patch.diff now holds a different change for the same task; task.md and repo/ are "
            f"unchanged. Grade this change on its own against PENALTIES.md, without regard to earlier "
            f"changes, and write the JSON it asks for, nothing else, to the file {out}.")


def steer_prompt(ws: Path, out: Path) -> str:
    return (f"Write the JSON object PENALTIES.md asks for, for the change in {ws}/patch.diff, and nothing "
            f"else, to the file {out} now.")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def say(msg: str) -> None:
    print(f"{now()} {msg}", file=sys.stderr, flush=True)


def append(path: Path, rec: dict) -> None:
    with _LOCK, path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def load_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {json.loads(l)["id"] for l in path.read_text().splitlines() if l.strip()}


AGENTAPI: list[str] = []   # set in main: `agentapi` (on PATH inside Antigravity) or `agy agentapi`


def agentapi(*args: str) -> str:
    p = subprocess.run([*AGENTAPI, *args], capture_output=True, text=True, timeout=180)
    out = (p.stdout + p.stderr).strip()
    if p.returncode != 0 or '"error"' in out[:200]:
        raise RuntimeError(f"agentapi {args[0]} failed (exit {p.returncode}; run outside agy's sandbox?): "
                           f"{out[:300]}")
    return out


def metadata(cid: str) -> dict | str:
    try:
        out = agentapi("get-conversation-metadata", cid)
        try:
            return json.loads(out)
        except ValueError:
            return out[:2000]
    except (RuntimeError, subprocess.SubprocessError) as e:
        return str(e)[:300]


def wait_for_quota(minimum: float, agy: str) -> None:
    """Sleep while the Gemini 5-hour or weekly bucket is below minimum (`agy -p /quota` spends nothing)."""
    while True:
        try:
            r = subprocess.run([agy, "-p", "/quota", "--output-format", "json"], capture_output=True, text=True,
                               timeout=120)
            groups = json.loads(r.stdout)["command"]["data"]["groups"]
        except (ValueError, KeyError, subprocess.SubprocessError) as e:
            say(f"quota check failed ({e}), going on")
            return
        low = [b for g in groups if g["name"].startswith("Gemini") for b in g["buckets"]
               if b["remaining_fraction"] < minimum]
        if not low:
            return
        b = min(low, key=lambda b: b["reset_time"])
        reset = datetime.fromisoformat(b["reset_time"].replace("Z", "+00:00"))
        wait = max(300, (reset - datetime.now(timezone.utc)).total_seconds() + 120)
        say(f"{b['name']} at {b['remaining_fraction']:.0%}, waiting {wait / 60:.0f} min")
        time.sleep(wait)


def make_workspace(work: Path, name: str, size: str, queue: Path, task: str) -> Path:
    ws = (work / name).resolve()
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(ROOT / "fixtures" / f"repo_{size}", ws / "repo",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copyfile(queue / "tasks" / f"{task}.md", ws / "task.md")
    shutil.copyfile(queue / "PENALTIES.md", ws / "PENALTIES.md")
    (ws / "verdicts").mkdir()
    return ws


def wait_verdict(path: Path, timeout: float, poll: float) -> str | None:
    """The verdict file's text once it exists and has stopped growing, or None after timeout."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        if path.is_file():
            text = path.read_text()
            if text and text == last:
                return text
            last = text
        time.sleep(poll)
    return path.read_text() if path.is_file() else None


def grade_group(group: str, items: list[dict], args, queue: Path) -> None:
    results, parked = queue / "results.jsonl", queue / "parked.jsonl"
    work = Path(args.work_dir)
    cid, ws, in_conv = None, None, 0
    try:
        for it in items:
            if args.min_quota:
                wait_for_quota(args.min_quota, args.agy)
            fresh = cid is None or in_conv >= args.rotate
            if fresh:
                ws = make_workspace(work, group, it["repo_size"], queue, it["task"])
            (ws / "patch.diff").write_text((queue / "patches" / f"{it['id']}.diff").read_text())
            out = ws / "verdicts" / f"{it['id']}.json"
            t0, messages, penalties, reason = time.time(), [], None, ""
            if fresh:
                started = agentapi("new-conversation", f"--title=cbench judge {group}",
                                   *( [f"--model={args.model}"] if args.model else []), first_prompt(ws, out))
                m = UUID.search(started)
                if not m:
                    raise RuntimeError(f"no conversation id in: {started[:300]}")
                cid, in_conv = m.group(0), 0
                messages.append("start")
            else:
                agentapi("send-message", cid, next_prompt(ws, out))
                messages.append("next")
            in_conv += 1
            for attempt in range(args.max_steers + 1):
                text = wait_verdict(out, args.turn_timeout, args.poll)
                if text is not None:
                    try:
                        penalties = parse_reply(text)
                        break
                    except ValueError:
                        reason = "verdict file without the JSON"
                        out.unlink(missing_ok=True)
                else:
                    reason = f"no verdict within {args.turn_timeout:.0f} s"
                if attempt < args.max_steers:
                    agentapi("send-message", cid, steer_prompt(ws, out))
                    messages.append(f"steer: {reason}")
            rec = {"id": it["id"], "patch_hash": it["patch_hash"], "task": it["task"], "repo_size": it["repo_size"],
                   "session": cid, "model": args.model, "steers": [m for m in messages if m.startswith("steer")],
                   "seconds": round(time.time() - t0), "graded_at": now(),
                   "metadata": metadata(cid) if args.metadata else None}
            if penalties is None:
                append(parked, {**rec, "reason": reason})
                say(f"{group} {it['id']}: parked ({reason}); conversation {cid} stays open in the app")
                cid = None                          # the rest of the group starts over in a fresh conversation
                continue
            append(results, {**rec, "penalties": penalties})
            say(f"{group} {it['id']}: {len(penalties)} penalties in {rec['seconds']} s"
                + (f", {len(rec['steers'])} steers" if rec["steers"] else ""))
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        say(f"{group}: stopped ({e}); rerun to continue")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("queue", type=Path, help="a dir written by cbench grade --mode penalty --export")
    ap.add_argument("--work-dir", default=os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR")
                    or str(Path.home() / ".cache" / "cbench-judge"), help="judge dirs, outside the checkout")
    ap.add_argument("--jobs", type=int, default=1, help="groups graded at once, one conversation each")
    ap.add_argument("--rotate", type=int, default=10, help="patches per conversation before a fresh one")
    ap.add_argument("--max-steers", type=int, default=2)
    ap.add_argument("--turn-timeout", type=float, default=900, help="seconds to wait for a verdict file")
    ap.add_argument("--poll", type=float, default=5)
    ap.add_argument("--min-quota", type=float, default=0.1,
                    help="wait while the 5-hour or weekly Gemini quota is below this fraction (0: never)")
    ap.add_argument("--model", help="agentapi --model (flash_lite|flash|pro|model id; default: agy's own)")
    ap.add_argument("--metadata", action="store_true", help="record get-conversation-metadata on each grade")
    ap.add_argument("--only", action="append", help="only these groups, e.g. reuse-007@M (repeatable)")
    ap.add_argument("--limit", type=int, help="at most this many patches")
    ap.add_argument("--retry-parked", action="store_true")
    ap.add_argument("--agentapi", help="the agentapi command (default: `agentapi` if on PATH, else `agy agentapi`)")
    ap.add_argument("--agy", default="agy", help="agy, for the quota check and the agentapi fallback")
    args = ap.parse_args()

    if not os.environ.get("ANTIGRAVITY_LS_ADDRESS"):
        raise SystemExit("ANTIGRAVITY_LS_ADDRESS is not set: run this from agy (or as an Antigravity sidecar)")
    AGENTAPI[:] = (shlex.split(args.agentapi) if args.agentapi
                   else [shutil.which("agentapi")] if shutil.which("agentapi") else [args.agy, "agentapi"])
    queue = args.queue.resolve()
    work = Path(args.work_dir).resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work-dir must be outside the checkout")
    work.mkdir(parents=True, exist_ok=True)
    skip = load_ids(queue / "results.jsonl") | (set() if args.retry_parked else load_ids(queue / "parked.jsonl"))
    items = [json.loads(l) for l in (queue / "manifest.jsonl").read_text().splitlines() if l.strip()]
    items = [it for it in items if it["id"] not in skip
             and (not args.only or f"{it['task']}@{it['repo_size']}" in args.only)][:args.limit]
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(f"{it['task']}@{it['repo_size']}", []).append(it)
    say(f"{len(items)} patches in {len(groups)} groups, {args.jobs} at a time; judge dirs in {work}")

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
