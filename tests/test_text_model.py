"""Real NER models on labelled synthetic documents. Skipped unless the model is installed."""

import pytest

from datanyx.text import PatternDetector, SpacyDetector, anonymize
from text_corpus import DOCUMENTS, FRENCH_DOCUMENTS, misses

spacy = pytest.importorskip("spacy")


def detectors_for(model):
    if not spacy.util.is_package(model):
        pytest.skip(f"{model} isn't installed")
    return [PatternDetector(), SpacyDetector(model)]


@pytest.fixture(scope="module")
def english():
    return detectors_for("en_core_web_lg")


@pytest.fixture(scope="module")
def french():
    return detectors_for("fr_core_news_md")


def test_english_corpus_has_no_misses(english):
    _, missed = misses(DOCUMENTS, lambda text: anonymize(text, english))
    assert missed == []


def test_french_corpus_has_no_misses(french):
    _, missed = misses(FRENCH_DOCUMENTS, lambda text: anonymize(text, french))
    assert missed == []


def test_placeholders_track_people_through_a_document(english):
    output = anonymize(
        "Mark Bell left in May. Rosa Delgado took over. Bell later called Rosa.", english
    )
    assert "Bell" not in output and "Rosa" not in output
    first = output.index("<PERSON_1>")
    assert output.count("<PERSON_1>") == 2 and output.count("<PERSON_2>") == 2
    assert first == 0


@pytest.mark.parametrize(
    "text, name",
    [
        ("PATIENT: OLIVER HARGREAVES\nSTATUS: ACTIVE\n", "HARGREAVES"),
        ("Contact JESSICA WHITFIELD about the claim.", "WHITFIELD"),
        ("hey did you hear from jessica whitfield yesterday?\n", "whitfield"),
    ],
)
def test_names_outside_normal_capitalization_are_found(english, text, name):
    assert name not in anonymize(text, english)


def test_long_input_keeps_correct_positions(english):
    # Longer than one chunk, so spans from later chunks must be offset correctly.
    filler = "The team reviewed ordinary operational topics.\n" * 3000
    output = anonymize(f"{filler}Jessica Whitfield approved it.\n", english)
    assert "Whitfield" not in output
    assert output.startswith("The team reviewed")
    assert output.endswith(" approved it.\n")
