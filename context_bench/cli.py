"""cbench one | run | index | validate-retrieval | grade | report"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cbench")
    ap.add_argument("--config", default="config/experiment.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("one", help="single run, prints the result record")
    one.add_argument("--task", required=True)
    one.add_argument("--level", required=True)
    one.add_argument("--model")
    one.add_argument("--seed", type=int, default=1)
    one.add_argument("--repo-size", default="S")
    one.add_argument("--retriever", default="none")
    one.add_argument("--query-mode", default="none")
    one.add_argument("--agent-variant", default="none")
    one.add_argument("--prompt-arm", default="base")
    one.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None,
                     help="override [generation].thinking")
    one.add_argument("--map-docs", action="store_true", help="L6: docstring first lines in the map")

    run = sub.add_parser("run", help="full sweep from config (resumable)")
    run.add_argument("--level", action="append", help="only these levels (repeatable)")
    run.add_argument("--model", action="append", help="run these [models.*] instead of [sweep].models")
    run.add_argument("--repo-size", action="append", help="only these repo sizes (repeatable)")
    run.add_argument("--task", action="append", help="only these tasks (repeatable)")
    run.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None,
                     help="every config with thinking on/off, overriding [generation].thinking")
    run.add_argument("--dry-run", action="store_true", help="list the runs still to do, don't run them")
    run.add_argument("--rerun", action="store_true", help="run recorded configs again too (latest row wins)")
    run.add_argument("--clean", action="store_true",
                     help="serial, restarting the model server before every run (cold cache): needs "
                          "CBENCH_RESTART_CMD; optional CBENCH_READY_TIMEOUT (s)")

    idx = sub.add_parser("index", help="build caches: embedding indexes (--embeddings), function summaries")
    idx.add_argument("--summaries", action="store_true")
    idx.add_argument("--embeddings", action="store_true", help="embed every file and function of the repos now")
    idx.add_argument("--bulk", action="store_true",
                     help="with --embeddings: build on both B60s through the GPU manager ([bulk_index]; vLLM "
                          "is unloaded meanwhile and loaded again after)")
    idx.add_argument("--model")
    idx.add_argument("--repo-size", action="append", help="repos (repeatable; default: all)")

    val = sub.add_parser("validate-retrieval", help="recall@k of planted helpers per retriever")
    val.add_argument("--retrievers", default="bm25")
    val.add_argument("--repo-size", default="S")

    gr = sub.add_parser("grade", help="blind judge compares all patches per task")
    gr.add_argument("--task", action="append", help="limit to these tasks (repeatable)")
    gr.add_argument("--dry-run", action="store_true", help="write judge dirs, don't call the judge")
    gr.add_argument("--repo-size", action="append", help="only these repos (repeatable)")
    gr.add_argument("--mode", choices=["compare", "penalty"], default="compare",
                    help="compare: rank all patches per task (rounds up to v3); penalty: one call per passing patch")
    gr.add_argument("--judge", help="penalty mode: a name from [grading.judges] (agy, claude)")
    gr.add_argument("--export", metavar="DIR", help="penalty mode: write the patches still to grade for the agy sidecar")
    gr.add_argument("--import", dest="import_", metavar="FILE", action="append",
                    help="penalty mode: grades from the agy sidecar's replies (repeatable)")
    gr.add_argument("--sample", type=int, help="penalty mode: grade at most N patches (random, seed 1), for calibration")
    gr.add_argument("--thinking", choices=["on", "off"], help="penalty mode: only runs of this round (default both)")
    gr.add_argument("--jobs", type=int, default=1, help="penalty mode: judge calls run at once (default 1)")

    bd = sub.add_parser("badges", help="exact and intent badges per run (results/badges.jsonl)")
    bd.add_argument("--results-dir", help="another results folder, e.g. results/archive-v4-qwen3emb")
    bd.add_argument("--rebuild-prototypes", action="store_true",
                    help="rebuild and calibrate the WANTS TO EXPLORE prototypes (grading/dodge_prototypes.*) first")
    sub.add_parser("report", help="label counts per configuration")
    rs = sub.add_parser("rescore", help="recompute output-protocol fields from saved responses (no model calls)")
    rs.add_argument("--cacheable", metavar="MODEL", help="also backfill tokens_cacheable for older L7 runs of "
                    "this [models.*] entry (counts tokens on its server, no generation)")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    from . import results
    from .runner import Experiment
    from .prompt import PROMPT_VERSION
    from .types import RunConfig

    if args.cmd == "one":
        exp = Experiment(Path(args.config))
        rc = RunConfig(task_id=args.task, level=args.level, model=args.model or exp.default_model,
                       seed=args.seed, repo_size=args.repo_size, retriever=args.retriever,
                       temperature=exp.config["generation"].get("temperature"),   # as the sweep does
                       query_mode=args.query_mode, agent_variant=args.agent_variant,
                       prompt_arm=args.prompt_arm,
                       thinking=(args.thinking if args.thinking is not None
                                 else exp.config["generation"].get("thinking", True)),
                       map_docs=args.map_docs, prompt_version=PROMPT_VERSION)
        result = exp.run_one(rc)
        results.append(exp.results_path, result)
        print(json.dumps(asdict(result), indent=2))
        return
    if args.cmd == "run":
        Experiment(Path(args.config)).sweep(args.level, dry_run=args.dry_run, models=args.model, repo_sizes=args.repo_size,
                                         thinking=args.thinking, task_ids=args.task, rerun=args.rerun, clean=args.clean)
        return
    if args.cmd == "rescore":
        exp = Experiment(Path(args.config))
        print(f"rescored {exp.rescore()} runs")
        if args.cacheable:
            print(f"backfilled tokens_cacheable for {exp.backfill_cacheable(args.cacheable)} L7 runs")
        return
    if args.cmd == "index":
        exp = Experiment(Path(args.config))
        sizes = args.repo_size or list(exp.config["repos"])
        if args.embeddings:
            if args.bulk:
                from .bulk_index import bulk_build
                print("\n".join(bulk_build(exp, sizes)))
            else:
                print("\n".join(exp.build_indexes(sizes)))
        if args.summaries:
            for size in sizes:
                exp.build_summaries(size, args.model or exp.default_model)
        return
    if args.cmd == "validate-retrieval":
        exp = Experiment(Path(args.config))
        print(exp.validate_retrieval(args.retrievers.split(","), args.repo_size), end="")
        return
    if args.cmd == "grade":
        exp = Experiment(Path(args.config))
        if args.mode == "penalty":
            if not args.judge:
                raise SystemExit("--mode penalty needs --judge (see [grading.judges])")
            filters = dict(task_ids=args.task, repo_sizes=args.repo_size, sample=args.sample, thinking=args.thinking)
            if args.export:
                n = exp.export_penalty(Path(args.export), args.judge, **filters)
                print(f"{n} patches written to {args.export}")
                return
            if args.import_:
                n = exp.import_penalty([Path(f) for f in args.import_], args.judge, **filters)
                print(f"graded {n} runs")
                return
            n = exp.grade_penalty(args.judge, args.task, dry_run=args.dry_run, repo_sizes=args.repo_size,
                                  sample=args.sample, thinking=args.thinking, jobs=args.jobs)
        else:
            n = exp.grade(args.task, dry_run=args.dry_run, repo_sizes=args.repo_size)
        print(f"judge dirs written to {exp.config['grading']['work_dir']}" if args.dry_run else f"graded {n} runs")
        return
    if args.cmd == "badges":
        exp = Experiment(Path(args.config))
        if args.rebuild_prototypes:
            print(json.dumps(exp.build_dodge_prototypes(), indent=1))
        n = exp.badges(Path(args.results_dir) if args.results_dir else None)
        print(f"badges for {n} runs")
        return
    if args.cmd == "report":
        exp = Experiment(Path(args.config))
        print(results.report(exp.results_path, exp._path(exp.config["paths"]["grades"])), end="")
        return
    raise NotImplementedError(f"command '{args.cmd}' not implemented yet")


if __name__ == "__main__":
    main()
