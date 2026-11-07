"""The accuracy numbers in docs/anonymization.md are measured, and they hold.

Runs the default spaCy model over the benchmark corpus (about a minute), so it only runs where the model is installed.
A number that falls more than TOLERANCE points is a regression; one that rises that far means the page is stale.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.needs_model

TOOL = Path(__file__).resolve().parent.parent / "tools" / "ner_benchmark.py"


@pytest.fixture(scope="module")
def benchmark():
    spec = importlib.util.spec_from_file_location("ner_benchmark", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measured(benchmark):
    rows, regions = benchmark.measure()
    return {label: percent for label, percent, _ in rows} | dict(regions)


def test_corpus_is_the_size_the_docs_say(benchmark):
    assert sum(len(names) for names in benchmark.NAMES.values()) == 72
    assert len(benchmark.NAMES) == 12 and len(benchmark.TEMPLATES) == 6 and len(benchmark.CONDITIONS) == 6


def test_documented_numbers_match_a_fresh_measurement(benchmark, measured):
    documented = benchmark.documented()
    assert set(documented) == set(measured), "run `uv run python tools/ner_benchmark.py --write`"
    for label, percent in documented.items():
        assert abs(measured[label] - percent) <= benchmark.TOLERANCE, (
            f"{label}: documented {percent}%, measured {measured[label]:.1f}% "
            "(a drop is a regression; a rise means run `uv run python tools/ner_benchmark.py --write`)"
        )


def test_ordinary_names_are_almost_always_hidden(measured):
    # The cases a user is most likely to have, held to a fixed bar whatever the page says.
    assert measured["First name only"] >= 95
    assert measured["Full name, capitalized normally"] >= 95
