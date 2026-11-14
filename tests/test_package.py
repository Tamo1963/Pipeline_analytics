import importlib

import pipeline_analytics

MODULES = [
    "core",
    "pipespec",
    "codes",
    "mechanical",
    "hydraulics",
    "routing",
    "cost",
    "economics",
    "optimiser",
    "bot",
    "reports",
    "api",
]


def test_version() -> None:
    assert pipeline_analytics.__version__ == "0.1.0"


def test_all_modules_import() -> None:
    for name in MODULES:
        importlib.import_module(f"pipeline_analytics.{name}")
