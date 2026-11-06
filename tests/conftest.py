"""Tests marked `needs_model` run the default spaCy model, which only some CI jobs install."""

import importlib.util

import pytest


def _model_installed() -> bool:
    if importlib.util.find_spec("spacy") is None:
        return False
    import spacy.util

    return bool(spacy.util.is_package("en_core_web_lg"))


def pytest_collection_modifyitems(config, items):
    if _model_installed():
        return
    skip = pytest.mark.skip(reason="needs the en_core_web_lg model (uv sync --group models)")
    for item in items:
        if "needs_model" in item.keywords:
            item.add_marker(skip)
