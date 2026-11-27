"""Tail checks for runs already recorded (2026-11-25): build the "+checks" arm of L7 and L8 without a full rerun.

The tail checks (RunConfig.tail_checks, l7_agentic.TAIL_SYNTAX / TAIL_TESTS) change nothing before the moment the
agent tries to finish: no prompt, tool or description changes, and the agent is never told in advance. So a
recorded run is a valid sample of the "+checks" arm up to that moment:
- the check would not fire: the run's outcome is the arm's outcome; its row is copied under the new run id
  (tail_checks=true, continued_from=<old id>), with its bundle;
- the check would fire (L7: a file in the final answer doesn't compile; L8: the repo's own tests fail on the
  edited copy): the saved conversation is replayed as the model saw it, the feedback appended, and the agent
  continues from there (run_agent resume), once, with the model under test. Then the run is scored as usual
  (hidden tests, reuse, repo tests, patch coverage). Tokens and time are the original run's plus the
  continuation's; the continuation ran later, on a warm server, so its latency is not comparable.
Old rows are never touched.

    python scripts/tail_continue.py --dry-run        # list what would be copied and what continued
    python scripts/tail_continue.py                   # do it (needs the model server for the continued runs)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from context_bench import bundle, results  # noqa: E402
from context_bench.apply import apply_files, changed_files, make_sandbox, parse_files, unified_patch  # noqa: E402
from context_bench.evaluate.hidden_tests import run_hidden_tests, run_repo_tests  # noqa: E402
from context_bench.evaluate.patch_coverage import patch_coverage, tests_added  # noqa: E402
from context_bench.levels.l7_agentic import (RICH_VARIANTS, SEMANTIC_VARIANTS, TAIL_SYNTAX, TAIL_TESTS,  # noqa: E402
                                             RepoTools, run_agent, syntax_problem_text, test_failures,
                                             turn_prompts)
from context_bench.metrics import dead_modules, legacy_used  # noqa: E402
from context_bench.runner import Experiment  # noqa: E402
from context_bench.workspace import load_workspace  # noqa: E402

UNIMPLEMENTED = ("task_vs_summary", "draft_then_search")


def saved(run_dir: Path):
    messages = json.loads((run_dir / "prompt.json").read_text())
    tr = run_dir / "transcript.jsonl"
    transcript = [json.loads(x) for x in tr.read_text().splitlines() if x.strip()] if tr.is_file() else []
    return messages, transcript


def l7_trigger(run_dir: Path) -> str | None:
    """The TAIL_SYNTAX message the final answer would have got, or None."""
    files = parse_files((run_dir / "response.txt").read_text())
    problems = [f"{rel}: {pr}" for rel, src in sorted(files.items()) if (pr := syntax_problem_text(rel, src))]
    return TAIL_SYNTAX.format(problems="\n".join(problems)) if problems else None


RC_FIELDS = ("task_id", "level", "model", "seed", "repo_size", "retriever", "query_mode", "agent_variant",
             "prompt_arm", "temperature", "thinking", "map_docs", "prompt_version", "failed_call_cost", "tool_names")


def base_configs(exp, rows: dict, model: str) -> list:
    """The L7/L8 runs the "+checks" arm is built from: every configured one (both rounds), plus the recorded
    runs of arms no longer in the config file (the native-names arm, tool_names = "qwen", thinking off only),
    their RunConfig rebuilt from the row and checked against its run id."""
    from context_bench.types import RunConfig
    out, seen = [], set()
    for thinking in (False, True):
        for rc in exp.configs(models=[model], thinking=thinking):
            if rc.level in ("L7", "L8"):
                out.append(rc)
                seen.add(rc.run_id)
    served = exp.config["models"][model]["model"]          # rows store the served name, configs the key
    for r in rows.values():
        if (r["level"] in ("L7", "L8") and r.get("tool_names") == "qwen" and r.get("model") == served
                and r.get("prompt_version") == 4 and not r.get("tail_checks") and r["run_id"] not in seen):
            rc = RunConfig(**{**{k: r[k] for k in RC_FIELDS if k in r and r[k] is not None}, "model": model})
            if rc.run_id == r["run_id"]:
                out.append(rc)
            else:
                print(f"skipped {r['run_id']}: its config can't be rebuilt from the row", file=sys.stderr)
    return out


def history_at_end(messages: list[dict], transcript: list[dict]) -> list[dict]:
    """The conversation exactly as the model saw it at its last reply (selective's drop stubs replayed), plus that
    reply; no reasoning (the model never gets earlier turns' thinking back)."""
    turns = turn_prompts(messages, transcript)
    last = next(m for m in reversed(transcript) if m.get("role") == "assistant")
    return turns[-1][0] + [{k: v for k, v in last.items() if k in ("role", "content", "tool_calls")}]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="qwen3.8-27b-fp8")
    args = ap.parse_args()
    exp = Experiment(ROOT / "config" / "experiment.toml")
    rows = {r["run_id"]: r for r in results.load(exp.results_path)}
    legacy_mods = {size: dead_modules(exp.legacy, list(load_workspace(exp._path(p)).files))
                   for size, p in exp.config["repos"].items()}
    todo_copy, todo_cont, missing = [], [], 0
    for rc in base_configs(exp, rows, args.model):
        if True:
            new = replace(rc, tail_checks=True)
            if new.run_id in rows:
                continue
            old = rows.get(rc.run_id)
            if old is None:
                missing += 1
                continue
            d = exp.runs_dir / rc.run_id
            if old["label"] == "no_fit" or not (d / "prompt.json").is_file():
                todo_copy.append((rc, new, old, None))
                continue
            if rc.level == "L7":
                fb = l7_trigger(d)
            else:
                fb = "tests" if old.get("repo_tests_total") and old["repo_tests_passed"] < old["repo_tests_total"] \
                    and (d / "files.json").is_file() else None
            (todo_cont if fb else todo_copy).append((rc, new, old, fb))
    print(f"copy {len(todo_copy)}, continue {len(todo_cont)}, base runs not recorded yet {missing}")
    for rc, new, old, fb in todo_cont:
        print(f"  continue {old['task_id']}@{old['repo_size']} {old['level']} {rc.agent_variant} "
              f"thinking={'on' if rc.thinking else 'off'} {old['label']} -> {'syntax' if rc.level == 'L7' else 'tests'}")
    if args.dry_run:
        return 0

    for rc, new, old, _ in todo_copy:
        row = {**old, "run_id": new.run_id, "tail_checks": True, "tail_check": None, "continued_from": old["run_id"]}
        src, dst = exp.runs_dir / old["run_id"], exp.runs_dir / new.run_id
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            (dst / "run.json").write_text(json.dumps(row, indent=2))
        if row.get("patch_path"):
            row["patch_path"] = str((dst / "patch.diff").relative_to(ROOT))
        _append(exp.results_path, row)
    print(f"copied {len(todo_copy)}")

    status = Status(exp, [new for _, new, _, _ in todo_cont])
    for rc, new, old, fb in todo_cont:
        print(f"continuing {old['task_id']}@{old['repo_size']} {old['level']} {rc.agent_variant}", flush=True)
        status.set(new.run_id, "running")
        try:
            row = continue_run(exp, rc, new, old, fb, legacy_mods)
            _append(exp.results_path, row)
        finally:
            status.set(new.run_id, None)
        print(f"  -> {row['label']} tests {row['tests_passed']}/{row['tests_total']} (was {old['label']} "
              f"{old['tests_passed']}/{old['tests_total']})", flush=True)
    return 0


class Status:
    """results/sweep_status.json while the continuation runs (the file the in-progress page reads, as cbench run
    writes it): the continued runs still queued and the one running; removed when the last one is done."""
    KEYS = ("task_id", "level", "retriever", "query_mode", "agent_variant", "map_docs", "prompt_arm", "repo_size",
            "thinking", "failed_call_cost", "prompt_version", "tool_names", "tail_checks")

    def __init__(self, exp, configs):
        import datetime, os
        self.now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
        self.path = exp.results_path.parent / "sweep_status.json"
        self.doc = {"pid": os.getpid(), "started_at": self.now(), "clean": False,
                    "runs": {c.run_id: {**{k: v for k, v in asdict(c).items() if k in self.KEYS}, "state": "queued"}
                             for c in configs}}
        self.write()

    def set(self, run_id: str, state: str | None) -> None:
        if state is None:
            self.doc["runs"].pop(run_id, None)
        else:
            self.doc["runs"][run_id]["state"] = state
        self.write()

    def write(self) -> None:
        if not self.doc["runs"]:
            self.path.unlink(missing_ok=True)
            return
        self.doc["updated_at"] = self.now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.doc))
        tmp.replace(self.path)


def _append(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def continue_run(exp, rc, new, old, fb, legacy_mods) -> dict:
    task = exp.tasks[rc.task_id]
    repo = exp._path(exp.config["repos"][rc.repo_size])
    run_dir_old, run_dir = exp.runs_dir / old["run_id"], exp.runs_dir / new.run_id
    messages, transcript = saved(run_dir_old)
    model_cfg, gen = exp.config["models"][rc.model], exp.config["generation"]
    client = exp._client(rc.model)
    with tempfile.TemporaryDirectory(prefix="cbench-tail-") as tmp:
        sandbox = make_sandbox(repo, Path(tmp) / "repo")
        edit = rc.agent_variant == "edit"
        if edit:                                   # L8: the edited copy as the run left it
            apply_files(sandbox, json.loads((run_dir_old / "files.json").read_text()))
            passed, total, out = run_repo_tests(sandbox, exp.shared_dir)
            fb = TAIL_TESTS.format(failed=total - passed, total=total, output=test_failures(out, sandbox))
        history = history_at_end(messages, transcript) + [{"role": "user", "content": fb}]
        ws = load_workspace(sandbox)
        tools = RepoTools(sandbox, make_retriever=(exp.retrievers()["embed"] if rc.agent_variant in SEMANTIC_VARIANTS
                                                   else None),
                          rich=rc.agent_variant in RICH_VARIANTS, whole_files=edit)
        if edit:
            tools.edited.update(json.loads((run_dir_old / "files.json").read_text()))
        res = run_agent(client, history, tools, agent_variant=rc.agent_variant, temperature=rc.temperature,
                        seed=rc.seed, max_tokens=gen["max_output_tokens"], context_window=model_cfg["context_window"],
                        thinking=rc.thinking, failed_call_cost=rc.failed_call_cost, prompt_version=rc.prompt_version,
                        tool_names=rc.tool_names, tail_checks=True,
                        run_repo_tests=(lambda: run_repo_tests(sandbox, exp.shared_dir)) if edit else None,
                        resume={"spent": old.get("budget_used") or 15.0, "closed": not edit,
                                "tail_check": "tests" if edit else "syntax"})
        error = None
        if edit:
            before, files = changed_files(repo, sandbox)
        else:
            files = parse_files(res.content)
            before = apply_files(sandbox, files) if files else {}
            if not files:
                error = "no <file> blocks in the response"
        patch = unified_patch(before, files) or None if files else None
        passed, total, reuse, tests_out = run_hidden_tests(sandbox, task.hidden_tests, exp.shared_dir)
        extra = {}
        if files:
            repo_fields, r_out = exp._repo_test_fields(old["repo_size"], sandbox)
            extra.update(repo_fields)
            tests_out += "\n--- repo's own tests ---\n" + r_out
            if patch:
                extra.update(patch_coverage(sandbox, task.hidden_tests, exp.shared_dir, before, files),
                             **tests_added(before, files))
    label = "ok" if total > 0 and passed == total else "broken"
    if label == "ok" and extra.get("known_tests_broken"):
        label = "breaks_tests"
    row = {**old, "run_id": new.run_id, "tail_checks": True, "tail_check": "tests" if edit else "syntax",
           "continued_from": old["run_id"], "label": label, "tests_passed": passed, "tests_total": total,
           "reuse_passed": reuse, "error": error or (None if label == "ok" else old.get("error")),
           "tokens_in": old["tokens_in"] + res.tokens_in, "tokens_out": old["tokens_out"] + res.tokens_out,
           "latency_s": round(old["latency_s"] + res.latency_s, 2),
           "tool_calls": old["tool_calls"] + res.tool_calls, "tool_errors": (old.get("tool_errors") or 0) + res.tool_errors,
           "peak_ctx_tokens": max(old.get("peak_ctx_tokens") or 0, res.peak_ctx_tokens),
           "format_status": "ok" if files else ("no_code" if edit else old.get("format_status")),
           "legacy_used": legacy_used(patch, legacy_mods[rc.repo_size]) if patch else None,
           "patch_path": str((run_dir / "patch.diff").relative_to(ROOT)) if patch else None, **extra}
    full = transcript + [{"role": "user", "content": fb}] + res.transcript
    bundle.write_bundle(run_dir, patch=patch, messages=messages, response=res.content,
                        reasoning="\n\n".join(x for x in ((run_dir_old / "reasoning.txt").read_text()
                                                         if (run_dir_old / "reasoning.txt").is_file() else "",
                                                         res.reasoning) if x) or None,
                        transcript=full, tests_output=tests_out, result=row, files=files if edit else None)
    return row


if __name__ == "__main__":
    sys.exit(main())
