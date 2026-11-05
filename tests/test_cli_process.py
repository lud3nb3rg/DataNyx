"""The command line as a user runs it: real processes, exit codes, stdin and stderr."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")
CANARY = "zq-canary-value"


def datanyx(*args, stdin=None):
    environment = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "datanyx", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
    )


def test_schema_from_stdin():
    result = datanyx("schema", "-", "--format", "text", stdin='{"a": 1}')
    assert result.returncode == 0 and "a: integer" in result.stdout


def test_missing_file_fails_cleanly(tmp_path):
    result = datanyx("schema", str(tmp_path / "nope.json"))
    assert result.returncode == 1
    assert result.stdout == "" and result.stderr.startswith("error:") and "Traceback" not in result.stderr


def test_unknown_extension_asks_for_source(tmp_path):
    path = tmp_path / "d.dat"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    result = datanyx("schema", str(path))
    assert result.returncode == 1 and "--source" in result.stderr
    assert datanyx("schema", str(path), "--source", "csv").returncode == 0


def test_invalid_input_never_echoes_the_data(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(f"{{{CANARY}", encoding="utf-8")
    result = datanyx("schema", str(path))
    assert result.returncode == 1 and CANARY not in result.stdout + result.stderr


def test_output_file_and_notes(tmp_path):
    path = tmp_path / "d.json"
    path.write_text('{"jane@example.com": 1, "id": 2}', encoding="utf-8")
    target = tmp_path / "out.json"
    result = datanyx("schema", str(path), "-o", str(target))
    assert result.returncode == 0 and "note:" in result.stderr and result.stdout == ""
    assert "jane@example.com" not in target.read_text(encoding="utf-8") + result.stderr


def test_doctor_runs():
    result = datanyx("doctor")
    assert result.returncode == 0 and "Parquet" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_mapping_file_is_restricted_on_windows(tmp_path):
    from datanyx.cli import _write_mapping

    target = tmp_path / "m.mapping.json"
    _write_mapping(str(target), {"<PERSON_1>": "x"})
    listing = subprocess.run(["icacls", str(target)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    principals = [line for line in listing.stdout.splitlines() if ":(" in line]
    assert len(principals) == 1
