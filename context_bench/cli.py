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

    run = sub.add_parser("run", help="full sweep from config (resumable)")
    run.add_argument("--level", action="append", help="only these levels (repeatable)")
    run.add_argument("--dry-run", action="store_true", help="list the runs still to do, don't run them")

    idx = sub.add_parser("index", help="build caches, e.g. function summaries")
    idx.add_argument("--summaries", action="store_true")
    idx.add_argument("--model")
    idx.add_argument("--repo-size", default="S")

    val = sub.add_parser("validate-retrieval", help="recall@k of planted helpers per retriever")
    val.add_argument("--retrievers", default="bm25")
    val.add_argument("--repo-size", default="S")

    gr = sub.add_parser("grade", help="blind judge compares all patches per task")
    gr.add_argument("--task", action="append", help="limit to these tasks (repeatable)")
    gr.add_argument("--dry-run", action="store_true", help="write judge dirs, don't call the judge")

    sub.add_parser("report", help="label counts per configuration")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    from . import results
    from .runner import Experiment
    from .types import RunConfig

    if args.cmd == "one":
        exp = Experiment(Path(args.config))
        rc = RunConfig(task_id=args.task, level=args.level, model=args.model or exp.default_model,
                       seed=args.seed, repo_size=args.repo_size, retriever=args.retriever,
                       query_mode=args.query_mode, agent_variant=args.agent_variant,
                       prompt_arm=args.prompt_arm)
        result = exp.run_one(rc)
        results.append(exp.results_path, result)
        print(json.dumps(asdict(result), indent=2))
        return
    if args.cmd == "run":
        Experiment(Path(args.config)).sweep(args.level, dry_run=args.dry_run)
        return
    if args.cmd == "report":
        exp = Experiment(Path(args.config))
        print(results.report(exp.results_path), end="")
        return
    raise NotImplementedError(f"command '{args.cmd}' not implemented yet")


if __name__ == "__main__":
    main()
