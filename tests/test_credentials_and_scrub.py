"""Scrubbing tracebacks, and finding credentials in URLs and tokens."""

import json

import pytest

from datanyx.audit import read_log, summarize
from datanyx.scrub import ScrubPolicy, scrub
from datanyx.text import SecretDetector
from datanyx.text.secrets import redact_url_credentials

# --- Scrub --------------------------------------------------------------------------------------------------------


TRACEBACK = 'Traceback (most recent call last):\n  File "./clients/Acme/x.py", line 123456, in run\n    client = "Acme Corp"\nKeyError: \'k\'\n'


def test_terms_to_hide_reach_every_line_of_a_traceback():
    out = scrub(TRACEBACK, ScrubPolicy(hide_terms=("Acme",)))
    assert "Acme" not in out and "line 123456" in out


def test_patterns_reach_a_traceback_path_but_not_its_code():
    text = 'Traceback (most recent call last):\n  File "/data/jane@corp.com/x.py", line 3, in f\n    y = "bob@corp.com"\nValueError: bad\n'
    out = scrub(text)
    assert "jane@corp.com" not in out and 'y = "bob@corp.com"' in out and ", line 3, in f" in out


def test_an_indented_line_that_isnt_code_is_a_message():
    text = "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\n    code()\n  jane@corp.com was here\nValueError: bad\n"
    assert "jane@corp.com" not in scrub(text)


def test_carets_stay_in_a_traceback():
    text = 'Traceback (most recent call last):\n  File "x.py", line 1, in f\n    code()\n    ^^^^^^\nValueError: bad\n'
    assert "    ^^^^^^\n" in scrub(text)


@pytest.mark.parametrize("quote", ["'", '"'])
def test_a_long_quoted_value_is_removed(quote):
    assert "x" * 100 not in scrub(f"KeyError: {quote}{'x' * 5000}{quote}")


def test_a_longer_secret_is_replaced_before_a_shorter_one_inside_it(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "hunter22pw")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:hunter22pw@db.internal/app")
    out = scrub("failed: postgres://u:hunter22pw@db.internal/app", ScrubPolicy(detectors=()))
    assert "db.internal" not in out and "hunter22pw" not in out


def test_scrub_hides_credentials_by_default():
    assert "correcthorse" not in scrub("login with password=correcthorse please")


# --- Credentials ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("postgresql://u:p@ss@host/db", "p@ss"),
        ("postgresql://u:pw@host/db", "pw"),
        ("mongodb://tok3n@host/db", "tok3n"),
        ("mongodb+srv://u:pw@a.example,b.example/db", "pw"),
        ("postgresql://host/db?user=u&password=secret", "secret"),
        ("https://host/x?token=abc&y=1", "abc"),
        ("https://host/x?API_KEY=abc", "abc"),
    ],
)
def test_credentials_in_urls_are_found(text, secret):
    assert secret not in redact_url_credentials(text).replace("<CREDENTIALS>", "")


def test_addresses_stay_when_credentials_go():
    assert redact_url_credentials("postgresql://u:p@ss@host/db") == "postgresql://<CREDENTIALS>@host/db"
    assert redact_url_credentials("https://example.com/a@b") == "https://example.com/a@b"
    assert redact_url_credentials("see https://example.com/?page=2") == "see https://example.com/?page=2"


def test_the_audit_log_and_scrub_share_the_rule():
    assert "tok3n" not in json.dumps(summarize("describe_data", {"target": "mongodb://tok3n@host/db"}))
    assert "tok3n" not in scrub("mongodb://tok3n@host/db", ScrubPolicy(detectors=()))


def test_the_secret_detector_finds_a_password_holding_an_at_sign():
    spans = list(SecretDetector().find("postgresql://u:p@ss-word@host/db"))
    assert any("postgresql://u:p@ss-word@host/db"[s.start : s.end] == "p@ss-word" for s in spans)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("token=abcdefghij", True),
        ('csrf_token: "abcdefghij"', True),
        ("max_tokens=1000000000", False),
        ("token_type=bearer-long", False),
    ],
)
def test_the_secret_detector_on_tokens(text, found):
    assert bool(list(SecretDetector().find(text))) is found


def test_one_bad_line_doesnt_hide_the_whole_audit_log(tmp_path):
    log = tmp_path / "a.jsonl"
    log.write_text('{"time":"garbage"}\n{"time":"2025-01-31T09:30:00+00:00","tool":"x"}\n', encoding="utf-8")
    records, unreadable = read_log(log)
    assert len(records) == 1 and unreadable == 1


# --- Owner-only files --------------------------------------------------------------------------------------------
