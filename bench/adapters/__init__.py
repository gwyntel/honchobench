"""Dataset adapters — transform the Enron corpus into honcho runner input formats.

Each honcho bench runner expects a specific input schema. These adapters bridge
`data/dataset/{conversations,labels}.json` into those schemas. Add a module per
runner as the suite grows.
These helpers are pure local data transformations — no network, no secrets.

Submodules are imported lazily (PEP 562 ``__getattr__``) so that running a single
adapter via ``python -m bench.adapters.<name>`` does not eagerly import its
siblings (avoids a spurious ``runpy`` ``sys.modules`` warning).
"""
import importlib

_LAZY = {
    "run_tests": "enron_to_run_tests",
    "beam": "enron_to_beam",
    "locomo": "enron_to_locomo",
    "longmem": "enron_to_longmem",
    "oolong": "enron_to_oolong_real",
    "trace_capture": "capture_traces",
}


def __getattr__(name: str):
    for mod, func in _LAZY.items():
        if name == func:
            module = importlib.import_module(f"{__name__}.{mod}")
            return getattr(module, func)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY.values()))
