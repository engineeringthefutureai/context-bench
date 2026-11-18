"""cbench one | run | index | validate-retrieval | report"""
from __future__ import annotations

import argparse


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

    sub.add_parser("run", help="full sweep from config (resumable)")

    idx = sub.add_parser("index", help="build caches, e.g. function summaries")
    idx.add_argument("--summaries", action="store_true")
    idx.add_argument("--model")
    idx.add_argument("--repo-size", default="S")

    val = sub.add_parser("validate-retrieval", help="recall@k of planted helpers per retriever")
    val.add_argument("--retrievers", default="bm25")
    val.add_argument("--repo-size", default="S")

    sub.add_parser("report", help="label counts per configuration")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(f"command '{args.cmd}' not implemented yet")


if __name__ == "__main__":
    main()
