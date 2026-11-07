"""Smaller anonymization behaviour: kept spans, the Luhn check, Ollama's proxy settings and the command line."""

import pytest

from datanyx.cli import main
from datanyx.sources import SourceError
from datanyx.text import PatternDetector, anonymize
from datanyx.text.detectors import luhn_valid

# --- Ollama --------------------------------------------------------------------------------------------------------


def test_ollama_ignores_proxy_settings(monkeypatch):
    from datanyx.text.backends import OllamaDetector

    seen = []

    class Opener:
        def __init__(self, handlers):
            self.handlers = handlers

        def open(self, request, timeout):
            seen.append(self.handlers)
            raise OSError("stop here")

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener(handlers))
    with pytest.raises(SourceError):
        list(OllamaDetector().find("some text"))
    assert seen[0][0].proxies == {}


# --- Anonymize ---------------------------------------------------------------------------------------------------------


def test_the_kept_span_filter_keeps_what_it_should():
    text = "on 2024-01-02 mail a@corp.io or 2024-02-03"
    out = anonymize(text, [PatternDetector()], keep_kinds=["DATE"])
    assert "2024-01-02" in out and "2024-02-03" in out and "a@corp.io" not in out


def test_the_kept_span_filter_with_overlapping_spans():
    from datanyx.text.anonymizer import _outside
    from datanyx.text.detectors import Span

    kept = [Span(0, 10, "A"), Span(2, 4, "B"), Span(20, 25, "C")]
    spans = [Span(3, 9, "x"), Span(5, 12, "y"), Span(21, 22, "z"), Span(26, 27, "w"), Span(0, 10, "A")]
    assert _outside(spans, kept) == [Span(5, 12, "y"), Span(26, 27, "w")]
    assert _outside(spans, []) == spans


def test_luhn_has_one_home():
    from datanyx.text import packs

    assert packs.luhn_valid is luhn_valid and luhn_valid("4539 1488 0343 6467") and not luhn_valid("1")


def test_the_command_line_hides_credentials(tmp_path, capsys):
    path = tmp_path / "c.txt"
    path.write_text("password = correcthorse\n", encoding="utf-8")
    assert main(["--no-config", "scrub", str(path), "--no-ner"]) == 0
    assert "correcthorse" not in capsys.readouterr().out
