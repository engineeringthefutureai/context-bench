"""pytest plugin: record which lines of files under CBENCH_TRACE_ROOT ran, as JSON in CBENCH_TRACE_OUT.

Loaded with `-p context_bench.evaluate._linetrace` by run_hidden_traced(). Standard library only
(sys.settrace), and it traces only frames whose file is under the root, so the tests run at nearly
full speed. A function's def line counts as run when the function is called."""
from __future__ import annotations

import json
import os
import sys
import threading

_ROOT = os.environ.get("CBENCH_TRACE_ROOT", "")
_OUT = os.environ.get("CBENCH_TRACE_OUT", "")
_hits: dict[str, set[int]] = {}


def _local(frame, event, arg):
    if event == "line":
        _hits.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
    return _local


def _global(frame, event, arg):
    if _ROOT and frame.f_code.co_filename.startswith(_ROOT):
        _hits.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
        return _local
    return None


def pytest_configure(config):
    if _ROOT and _OUT:
        sys.settrace(_global)
        threading.settrace(_global)


def pytest_unconfigure(config):
    sys.settrace(None)
    threading.settrace(None)
    if _OUT:
        with open(_OUT, "w") as f:
            json.dump({os.path.realpath(k): sorted(v) for k, v in _hits.items()}, f)
