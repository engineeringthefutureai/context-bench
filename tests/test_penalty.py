"""Penalty grading: validation, the judge directory, resume and reuse (no real judge)."""
import json
import sys
from pathlib import Path

from context_bench import penalty

PATCH = """--- a/shopkit/cart.py
+++ b/shopkit/cart.py
@@ -1,3 +1,5 @@
 def total(items):
-    return sum(i.price for i in items)
+    t = sum(i.price for i in items)
+    print("customer email", items[0].email)
+    return t * 1.2
"""


def test_validate_keeps_known_codes_with_real_quotes_and_caps():
    raw = [{"code": "risk", "quote": 'print("customer email", items[0].email)', "why": "PII in logs"},
           {"code": "convention", "quote": "return t * 1.2", "why": "float money"},
           {"code": "convention", "quote": "+    return t * 1.2", "why": "same line again"},
           {"code": "style", "quote": "return t * 1.2", "why": "not in the catalogue"},
           {"code": "scope", "quote": "import os", "why": "not in the diff"},
           {"code": "wrong_untested", "quote": "t = sum(i.price", "why": "part of a line"},
           "garbage"]
    accepted, dropped, points = penalty.validate(raw, PATCH)
    assert [a["code"] for a in accepted] == ["risk", "convention", "wrong_untested"]
    assert sorted(d["reason"] for d in dropped) == ["duplicate", "not an object", "quote not in the diff", "unknown code"]
    assert points == -20 - 5 - 15
    many = [{"code": c, "quote": q} for c, q in [("risk", "return t * 1.2"), ("api", "return t * 1.2"),
                                                 ("scope", "t = sum(i.price for i in items)")]]
    assert penalty.validate(many, PATCH)[2] == penalty.TOTAL_CAP


def test_convention_is_capped_and_empty_list_is_zero():
    lines = ['t = sum(i.price for i in items)', 'return t * 1.2', 'print("customer email", items[0].email)']
    assert penalty.validate([{"code": "convention", "quote": q} for q in lines], PATCH)[2] == -10
    assert penalty.validate([], PATCH) == ([], [], 0)


def test_parse_reply_accepts_fenced_json_and_rejects_other_shapes():
    assert penalty.parse_reply('```json\n{"penalties": []}\n```') == []
    for bad in ('{"ranking": []}', "no json", '{"penalties": {}}'):
        try:
            penalty.parse_reply(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_judge_dir_holds_only_task_patch_repo_and_catalogue(tmp_path):
    repo = tmp_path / "repo_src"
    (repo / "shopkit").mkdir(parents=True)
    (repo / "shopkit" / "cart.py").write_text("x = 1\n")
    cat = tmp_path / "penalties.md"
    cat.write_text("catalogue")
    dst = penalty.write_judge_dir("Add tax.", repo, PATCH, cat, tmp_path / "judge")
    assert sorted(p.name for p in dst.iterdir()) == ["PENALTIES.md", "patch.diff", "repo", "task.md"]
    text = " ".join(p.read_text() for p in dst.rglob("*") if p.is_file())
    for leak in ("L7", "level", "run_id", "qwen", "thinking", "agent"):
        assert leak not in text


def _rows(tmp_path):
    runs = tmp_path / "runs"
    rows = []
    for rid, label, patch in [("a", "ok", PATCH), ("b", "ok", PATCH), ("c", "broken", PATCH), ("d", "ok", PATCH + "\n")]:
        (runs / rid).mkdir(parents=True)
        (runs / rid / "patch.diff").write_text(patch)
        rows.append({"run_id": rid, "label": label, "task_id": "t", "repo_size": "M"})
    return rows, runs


def test_pending_groups_identical_patches_skips_failing_and_graded(tmp_path):
    rows, runs = _rows(tmp_path)
    todo = penalty.pending(rows, runs, {}, "agy", "1")
    assert sorted(len(v["rows"]) for v in todo.values()) == [1, 2]          # a+b share a patch; c fails
    h = penalty.patch_hash(PATCH)
    done = {"a": {"run_id": "a", "patch_hash": h, "judge": "agy", "catalogue_version": "1",
                  "penalties": [], "dropped": [], "points": 0}}
    todo = penalty.pending(rows, runs, done, "agy", "1")
    assert [r["run_id"] for r in todo[h]["rows"]] == ["b"] and todo[h]["reuse"]["points"] == 0
    assert all(not v.get("reuse") for v in penalty.pending(rows, runs, done, "claude", "1").values())
    assert len(penalty.pending(rows, runs, {}, "agy", "1", sample=1)) == 1


def test_grade_patch_with_a_fake_judge(tmp_path):
    rows, runs = _rows(tmp_path)
    fake = tmp_path / "judge.py"
    fake.write_text("import json\nprint(json.dumps({'result': json.dumps({'penalties': "
                    "[{'code': 'convention', 'quote': 'return t * 1.2', 'why': 'float'}]})}))\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    cat = tmp_path / "p.md"
    cat.write_text("c")
    task = type("T", (), {"id": "t", "prompt": "Add tax."})()
    out = tmp_path / "penalties.jsonl"
    item = penalty.pending(rows, runs, {}, "fake", "1")[penalty.patch_hash(PATCH)]
    n = penalty.grade_patch(task, "M", item, repo=repo, catalogue=cat, version="1", judge="fake",
                            judge_command=[sys.executable, str(fake)], work_dir=tmp_path / "work", out_path=out)
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert n == 2 and {l["run_id"] for l in lines} == {"a", "b"} and lines[0]["points"] == -5
    assert penalty.load(out)["a"]["penalties"][0]["code"] == "convention"


def test_export_queue_and_a_sidecar_reply(tmp_path):
    rows, runs = _rows(tmp_path)
    cat = tmp_path / "p.md"
    cat.write_text("catalogue")
    task = type("T", (), {"id": "t", "prompt": "Add tax."})()
    todo = penalty.pending(rows, runs, {}, "agy", "1")
    q = tmp_path / "queue"
    n = penalty.export_queue(todo, {r["task_id"]: task for r in rows}, {"M": "abc", "S": "def", "L": "ghi"},
                             cat, "1", q)
    manifest = [json.loads(l) for l in (q / "manifest.jsonl").read_text().splitlines()]
    assert n == len(manifest) == len(todo)
    assert all(set(m) == {"id", "task", "repo_size", "repo_hash", "patch_hash", "catalogue_version"} for m in manifest)
    h = penalty.patch_hash(PATCH)
    assert (q / "patches" / f"{h[:16]}.diff").read_text() == PATCH and (q / "tasks" / "t.md").exists()
    out = tmp_path / "penalties.jsonl"
    reply = {"patch_hash": h, "session": "s1", "steers": [], "penalties": [
        {"code": "risk", "quote": 'print("customer email", items[0].email)', "why": "email in logs"},
        {"code": "scope", "quote": "not in the patch", "why": "x"}]}
    n = penalty.grade_patch(task, "M", todo[h], repo=Path(), catalogue=cat, version="1", judge="agy",
                            judge_command=None, work_dir=Path(), out_path=out, reply=reply)
    g = penalty.load(out)["a"]
    assert n == 2 and g["points"] == -20 and len(g["dropped"]) == 1 and g["session"] == "s1"


FAKE_AGY = r"""#!/usr/bin/env python3
import json, re, subprocess, sys, uuid
a = sys.argv[1:]
if "/quota" in a:
    print('{"command": {"data": {"groups": []}}}'); sys.exit()
if a[1] == "get-conversation-metadata":
    print("{}"); sys.exit()
prompt = a[-1]
out = re.search(r"to the file (/\S+?\.json)", prompt).group(1)
patch = open(out.rsplit("/verdicts/", 1)[0] + "/patch.diff").read()
if not ("silent" in patch and not prompt.startswith("Write the JSON")):
    body = "no json" if "bad" in patch else '{"penalties": []}'
    subprocess.Popen(["sh", "-c", f"sleep 0.2; printf '%s' '{body}' > {out}"])
print(json.dumps({"conversationId": str(uuid.uuid4())}) if a[1] == "new-conversation" else "{}")
"""


def test_agy_grader_grades_steers_and_parks_with_a_fake_agentapi(tmp_path):
    import os, subprocess
    root = Path(__file__).resolve().parent.parent
    agy = tmp_path / "agy"
    agy.write_text(FAKE_AGY)
    agy.chmod(0o755)
    q = tmp_path / "q"
    (q / "patches").mkdir(parents=True)
    (q / "tasks").mkdir()
    (q / "PENALTIES.md").write_text("catalogue")
    (q / "tasks" / "reuse-001.md").write_text("task")
    lines = []
    for pid, body in [("a1", "+ok"), ("a2", "+silent"), ("a3", "+bad"), ("a4", "+ok 2")]:
        (q / "patches" / f"{pid}.diff").write_text(body + "\n")
        lines.append(json.dumps({"id": pid, "task": "reuse-001", "repo_size": "S", "patch_hash": "h" + pid}))
    (q / "manifest.jsonl").write_text("\n".join(lines) + "\n")
    subprocess.run([sys.executable, str(root / "scripts" / "agy_grader.py"), str(q), "--agy", str(agy), "--agentapi", f"{agy} agentapi",
                    "--work-dir", str(tmp_path / "work"), "--turn-timeout", "2", "--poll", "0.1", "--min-quota", "0"],
                   env={**os.environ, "ANTIGRAVITY_LS_ADDRESS": "x"}, check=True, timeout=120)
    res = {json.loads(l)["id"]: json.loads(l) for l in (q / "results.jsonl").read_text().splitlines()}
    parked = [json.loads(l) for l in (q / "parked.jsonl").read_text().splitlines()]
    assert set(res) == {"a1", "a2", "a4"} and [p["id"] for p in parked] == ["a3"]
    assert res["a2"]["steers"] and res["a1"]["session"] == res["a2"]["session"] != res["a4"]["session"]
