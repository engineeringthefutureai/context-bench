#!/usr/bin/env python3
"""Probe `agy agentapi` before building the grader on it (stdlib only).

Must run as a subprocess of agy (ask an Antigravity conversation to run it), because agentapi needs
the ANTIGRAVITY_LS_ADDRESS that agy sets. It opens one tiny conversation, polls its metadata, sends it a
follow-up and polls again, to learn what new-conversation prints, whether the metadata carries the
reply and a done/running status, and whether send-message reaches a conversation. Costs a few thousand
tokens. Everything goes to grading/agentapi-probe.json (commit and push it back):

    python3 scripts/agentapi_probe.py
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "grading" / "agentapi-probe.json"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
log: dict = {"env_names": sorted(k for k in os.environ if "ANTIGRAVITY" in k or k.startswith("AGY")), "steps": []}


def run(*args: str, timeout: int = 120) -> dict:
    p = subprocess.run(["agy", "agentapi", *args], capture_output=True, text=True, timeout=timeout)
    step = {"args": list(args), "exit": p.returncode, "stdout": p.stdout[-6000:], "stderr": p.stderr[-2000:],
            "at": time.strftime("%H:%M:%S")}
    log["steps"].append(step)
    OUT.write_text(json.dumps(log, indent=1))
    return step


def poll(cid: str, seconds: int = 180) -> None:
    last = None
    end = time.time() + seconds
    while time.time() < end:
        s = run("get-conversation-metadata", cid)
        if s["stdout"] == last:              # keep only changes
            log["steps"].pop()
        last = s["stdout"]
        time.sleep(5)


first = run("new-conversation", "--title=cbench agentapi probe",
            'Reply with exactly this JSON and nothing else: {"penalties": []}')
m = UUID.search(first["stdout"] + first["stderr"])
log["conversation_id"] = m.group(0) if m else None
if m:
    poll(m.group(0))
    run("send-message", m.group(0), 'Now reply with exactly this JSON and nothing else: {"penalties": [], "turn": 2}')
    poll(m.group(0), 120)
OUT.write_text(json.dumps(log, indent=1))
print(f"wrote {OUT} ({len(log['steps'])} steps, conversation {log.get('conversation_id')})")
