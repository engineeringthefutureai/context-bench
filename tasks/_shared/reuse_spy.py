"""Hidden-test helper for reuse checks: did the new code actually call an existing helper?

Import the module under test first, then call spy(); it replaces the helper everywhere it is bound
(its own module and any module that imported it), so both `from x import f` and `x.f` styles are
caught. The runner puts tasks/_shared on PYTHONPATH for hidden tests.
"""
import importlib
import sys


def spy(monkeypatch, qualname: str) -> list:
    """Return a list that collects the args of every call to `qualname` from now on."""
    mod_name, name = qualname.rsplit(".", 1)
    original = getattr(importlib.import_module(mod_name), name)
    root = qualname.split(".")[0] + "."
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith(root):
            for attr, value in list(vars(mod).items()):
                if value is original:
                    monkeypatch.setattr(mod, attr, wrapper)
    return calls
