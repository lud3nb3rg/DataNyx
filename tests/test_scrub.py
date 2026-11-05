"""Scrubbing keeps the diagnosis of an error and drops the data in it."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from datanyx import extract
from datanyx.scrub import ScrubPolicy, scrub, shape, visible_names

SRC = str(Path(__file__).resolve().parents[1] / "src")
CANARY = "zqcanaryvalue"

TRACEBACK = """Traceback (most recent call last):
  File "report.py", line 3, in <module>
    total = rows[0]["jane.doe@example.com"]
            ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
KeyError: 'jane.doe@example.com'
"""


def test_traceback_keeps_its_skeleton_and_loses_the_value():
    cleaned = scrub(TRACEBACK)
    assert 'File "report.py", line 3, in <module>' in cleaned
    assert 'total = rows[0]["jane.doe@example.com"]' in cleaned  # Code is the agent's own.
    assert "KeyError: <VALUE>" in cleaned


def test_exception_message_values_are_removed():
    cleaned = scrub("ValueError: could not convert string to float: 'abc123'")
    assert cleaned == "ValueError: could not convert string to float: <VALUE>"
    assert scrub('TypeError: unsupported operand for "Jessica"') == "TypeError: unsupported operand for <VALUE>"
    assert scrub("ValueError: bad bytes b'zz9'") == "ValueError: bad bytes <VALUE>"


def test_names_the_schema_shows_are_kept():
    policy = ScrubPolicy(allowed_names=frozenset({"plan", "email"}))
    assert scrub("KeyError: 'plan'", policy) == "KeyError: 'plan'"
    assert scrub("KeyError: 'Plan'", policy) == "KeyError: <VALUE>"
    assert scrub("dtype='object'", policy) == "dtype='object'"  # About storage, not content.


def test_apostrophes_in_words_are_not_quotes():
    assert scrub("ValueError: can't parse it") == "ValueError: can't parse it"


def test_long_numbers_go_and_short_ones_stay():
    cleaned = scrub("ParserError: Expected 3 fields in line 4, saw 5; total 918273 and 12.75")
    assert "line 4, saw 5" in cleaned and "Expected 3 fields" in cleaned
    assert "918273" not in cleaned and "12.75" not in cleaned
    assert "12.75" in scrub("ParserError: ratio 12.75", ScrubPolicy(redact_numbers=False))


def test_detectors_replace_unquoted_identifiers_consistently():
    cleaned = scrub("Failed for a@example.com\nRetrying a@example.com and b@example.com")
    assert cleaned == "Failed for <EMAIL_1>\nRetrying <EMAIL_1> and <EMAIL_2>"


def test_hide_terms():
    cleaned = scrub("Error while reading Northwind export", ScrubPolicy(hide_terms=("northwind",)))
    assert "Northwind" not in cleaned


def test_home_folder_working_folder_and_username_are_shortened(monkeypatch, tmp_path):
    home = tmp_path / "zqhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("getpass.getuser", lambda: "zqjane")
    text = f'Traceback (most recent call last):\n  File "{home}/proj/a.py", line 1\nlogged in as zqjane'
    cleaned = scrub(text)
    assert "zqhome" not in cleaned and "zqjane" not in cleaned
    assert '"~/proj/a.py"' in cleaned.replace("\\", "/") and "<USER>" in cleaned


def test_credentials_in_the_environment_and_in_urls_are_hidden(monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "tok_live_1234567890")
    monkeypatch.setenv("SHORT_KEY", "abc")
    cleaned = scrub("auth failed with tok_live_1234567890 for postgresql://admin:hunter22@db.internal/prod; abc")
    assert "tok_live" not in cleaned and "hunter22" not in cleaned and "admin" not in cleaned
    assert "<SECRET>" in cleaned and "<CREDENTIALS>" in cleaned


def test_output_without_a_traceback_is_scrubbed_line_by_line():
    cleaned = scrub("row 1: 'Ana Silva', 91000.50\nrow 2: 'Li Chen', 78000.25")
    assert "Ana" not in cleaned and "91000" not in cleaned and "row 1:" in cleaned


def test_chained_tracebacks_keep_their_structure():
    text = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in <module>\n'
        "KeyError: 'x'\n\n"
        "During handling of the above exception, another exception occurred:\n\n"
        "Traceback (most recent call last):\n"
        '  File "a.py", line 3, in <module>\n'
        "RuntimeError: failed on 'jane'\n"
    )
    cleaned = scrub(text)
    assert cleaned.count("Traceback (most recent call last):") == 2
    assert "During handling of the above exception" in cleaned and "jane" not in cleaned


def test_shape_says_how_much_without_showing_it():
    assert shape("") == "no output"
    assert shape("a\n") == "1 line, 2 characters"
    assert shape("a\nbb") == "2 lines, 4 characters"


def test_visible_names_come_from_the_schema(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text("id,plan\n1,pro\n", encoding="utf-8")
    names = visible_names(extract(str(path)))
    assert {"id", "plan"} <= names and "pro" not in names


def test_ner_catches_names_in_unquoted_text():
    spacy_util = pytest.importorskip("spacy.util")
    if not spacy_util.is_package("en_core_web_lg"):
        pytest.skip("needs en_core_web_lg")
    from datanyx.text import SpacyDetector

    policy = ScrubPolicy(detectors=(*ScrubPolicy().detectors, SpacyDetector("en_core_web_lg")))
    cleaned = scrub("RuntimeError: no invoice found for Jessica Whitfield", policy)
    assert "Jessica" not in cleaned and cleaned.startswith("RuntimeError: ")


# --- Fuzz --------------------------------------------------------------------


def test_quoted_values_and_numbers_never_survive():
    hypothesis = pytest.importorskip("hypothesis")
    from hypothesis import strategies as st

    @hypothesis.settings(max_examples=100, deadline=None)
    @hypothesis.given(
        st.text(alphabet="abc XYZ019-_.@:/", max_size=12),
        st.sampled_from(["'{}'", '"{}"', "b'{}'", "[{!r}]", "{{'k': '{}'}}"]),
        st.integers(min_value=10**4, max_value=10**12),
    )
    def check(tail, shape_of_message, number):
        value = CANARY + tail
        message = shape_of_message.replace("{!r}", "'{}'").format(value) + f" and {number}"
        cleaned = scrub(f"ValueError: bad input {message}")
        assert CANARY not in cleaned and str(number) not in cleaned

    check()


# --- Command line ------------------------------------------------------------


def datanyx(*args, stdin=None):
    environment = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "datanyx", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
    )


def py(code):
    return ["--", sys.executable, "-c", code]


def test_scrub_command_filters_stdin():
    result = datanyx("scrub", "--no-ner", "-", stdin=TRACEBACK)
    assert result.returncode == 0 and result.stdout.rstrip().endswith("KeyError: <VALUE>")


def test_scrub_command_keeps_allowed_names(tmp_path):
    data = tmp_path / "d.csv"
    data.write_text("plan\npro\n", encoding="utf-8")
    result = datanyx("scrub", "--no-ner", "--names-from", str(data), "-", stdin="KeyError: 'plan'\nKeyError: 'pro'\n")
    assert result.stdout == "KeyError: 'plan'\nKeyError: <VALUE>\n"


def test_run_hides_stdout_scrubs_stderr_and_passes_the_exit_code():
    code = "import sys; print('secret row jane@example.com'); sys.exit(int('x'))"
    result = datanyx("run", "--no-ner", *py(code))
    assert result.returncode == 1
    assert result.stdout == "" and "jane@example.com" not in result.stderr
    assert "ValueError: invalid literal for int() with base 10: <VALUE>" in result.stderr
    assert "datanyx: exit code 1; stdout hidden (1 line, 29 characters)" in result.stderr


def test_run_can_show_scrubbed_stdout_or_none_of_it():
    code = "print('contact jane@example.com')"
    shown = datanyx("run", "--no-ner", "--stdout", "scrubbed", *py(code))
    assert shown.returncode == 0 and shown.stdout.strip() == "contact <EMAIL_1>"
    hidden = datanyx("run", "--no-ner", "--stdout", "none", *py(code))
    assert hidden.stdout == "" and "stdout hidden" in hidden.stderr


def test_run_saves_the_real_output_for_the_human(tmp_path):
    target = tmp_path / "real.txt"
    datanyx("run", "--no-ner", "--save-stdout", str(target), *py("print('jane@example.com')"))
    assert "jane@example.com" in target.read_text(encoding="utf-8")
    again = datanyx("run", "--no-ner", "--save-stdout", str(target), *py("print(1)"))
    assert again.returncode == 1 and "already exists" in again.stderr


def test_run_timeout_and_missing_command():
    slow = datanyx("run", "--no-ner", "--timeout", "1", *py("import time; time.sleep(30)"))
    assert slow.returncode == 124 and "timed out" in slow.stderr
    missing = datanyx("run", "--no-ner", "--", "definitely-not-a-real-command")
    assert missing.returncode == 1 and "not found" in missing.stderr
    assert datanyx("run", "--no-ner").returncode == 1


def test_run_does_not_read_our_stdin():
    code = "import sys; print(repr(sys.stdin.read()))"
    result = datanyx("run", "--no-ner", "--stdout", "scrubbed", *py(code), stdin="typed input")
    assert result.returncode == 0 and result.stdout.strip() == "''"


def test_run_refuses_to_start_without_the_model_unless_told_to(tmp_path):
    result = datanyx("run", "--model", "no_such_model", *py("print(1)"))
    assert result.returncode == 1 and "model" in result.stderr.lower()
