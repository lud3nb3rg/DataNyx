"""The audit log records which tools an agent called and on what, and never a value."""

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from datanyx.audit import AuditLog, read_log, summarize, summarize_log
from datanyx.cli import main
from datanyx.sources import SourceError

pytest.importorskip("mcp")
from datanyx.mcp_server import create_server

CANARY = "zq-canary-value"


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "customers.csv"
    path.write_text(f"id,email,lifetime_value\n1,{CANARY}@acme-corp.io,12.5\n2,b@acme-corp.io,3\n", encoding="utf-8")
    return path


def call(server, tool, arguments):
    async def run():
        return await server.call_tool(tool, arguments)

    return asyncio.run(run())


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def server_with_log(tmp_path):
    log = tmp_path / "logs" / "audit.jsonl"
    return create_server([tmp_path], audit_log=log), log


# --- What gets recorded ------------------------------------------------------------------------------


def test_each_call_is_recorded_with_its_tool_target_and_outcome(tmp_path, data):
    server, log = server_with_log(tmp_path)
    call(server, "describe_data", {"target": str(data), "format": "text"})
    call(server, "scan_folder", {"target": str(tmp_path)})
    first, second = records(log)
    assert first["tool"] == "describe_data" and first["outcome"] == "ok"
    assert first["arguments"]["target"] == str(data) and first["output_chars"] > 0
    assert second["tool"] == "scan_folder" and first["session"] == second["session"]
    assert {"time", "seconds"} <= set(first)


def test_failures_and_refusals_are_recorded_with_their_safe_message(tmp_path, data):
    server, log = server_with_log(tmp_path)
    with pytest.raises(Exception, match="outside"):
        call(server, "describe_data", {"target": str(tmp_path.parent / "elsewhere.csv")})
    (entry,) = records(log)
    assert entry["outcome"] == "error" and "outside the folders" in entry["message"] and "output_chars" not in entry


def test_the_log_never_holds_values_outputs_code_terms_or_passwords(tmp_path, data):
    server, log = server_with_log(tmp_path)
    call(server, "describe_data", {"target": str(data)})
    call(server, "check_code", {"code": f"SELECT '{CANARY}-guess' FROM customers", "target": str(data)})
    with pytest.raises(Exception):
        call(server, "anonymize_file", {"target": str(tmp_path / "missing.txt"), "hide": [f"{CANARY}-client", "Acme"]})
    with pytest.raises(Exception):
        call(server, "describe_data", {"target": "postgresql://user:hunter22pw@db.internal/app"})

    text = log.read_text(encoding="utf-8")
    assert CANARY not in text  # Not the value in the data, the SQL, or the terms.
    assert "hunter22pw" not in text and "<CREDENTIALS>" in text
    assert "lifetime_value" not in text  # A column name from the output.
    by_tool = {e["tool"]: e for e in records(log)}
    assert by_tool["check_code"]["arguments"]["code_length"] > 10 and "code" not in by_tool["check_code"]["arguments"]
    assert by_tool["anonymize_file"]["arguments"]["hide_count"] == 2


def test_a_server_without_a_log_writes_nothing(tmp_path, data):
    server = create_server([tmp_path])
    call(server, "describe_data", {"target": str(data)})
    assert list(tmp_path.iterdir()) == [data]


def test_an_unwritable_log_is_a_clear_error(tmp_path, data):
    with pytest.raises(SourceError, match="cannot write the audit log"):
        create_server([tmp_path], audit_log=data / "inside" / "audit.jsonl")


def test_summarize_keeps_only_what_is_safe_to_show():
    assert summarize("check_code", {"code": "SELECT 1", "language": "sql", "target": "x.csv"}) == {
        "code_length": 8,
        "language": "sql",
        "target": "x.csv",
    }
    assert summarize("anonymize_file", {"hide": ["a", "b"], "target": "t.txt"}) == {"hide_count": 2, "target": "t.txt"}
    assert summarize("describe_data", {"target": "x", "map_paths": ["$.a"], "database_schema": None}) == {
        "target": "x",
        "map_paths": ["$.a"],
    }
    assert summarize("new_tool", {"payload": "secret text", "items": [1, 2, 3]}) == {"payload_length": 11, "items_count": 3}


def test_the_log_is_private_to_its_owner(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("describe_data", {"target": "x"}, "ok", None, time.monotonic(), 1)
    if sys.platform == "win32":
        listing = subprocess.run(["icacls", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert len([line for line in listing.stdout.splitlines() if ":(" in line]) == 1
    else:
        assert os.stat(path).st_mode & 0o777 == 0o600


# --- Reading it back --------------------------------------------------------------------------------------------


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    entries = [
        {"time": "2025-01-01T09:00:00+00:00", "session": "s1", "tool": "describe_data", "arguments": {"target": "a.csv"}, "outcome": "ok"},
        {"time": "2025-01-02T09:00:00+00:00", "session": "s1", "tool": "describe_data", "arguments": {"target": "a.csv"}, "outcome": "ok"},
        {
            "time": "2025-01-03T09:00:00+00:00",
            "session": "s2",
            "tool": "scan_folder",
            "arguments": {"target": "../secret"},
            "outcome": "error",
            "message": "that path is outside the folders this server may read",
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\nnot json\n", encoding="utf-8")
    return path


def test_summary_counts_tools_paths_and_failures(log_file):
    found, unreadable = read_log(log_file)
    text = summarize_log(found, unreadable)
    assert "3 calls in 2 sessions" in text
    assert "describe_data 2" in text and "scan_folder 1" in text
    assert "a.csv  ×2" in text and "../secret  ×1" in text
    assert "1 call failed or were refused" in text and "outside the folders" in text
    assert "1 line couldn't be read" in text


def test_since_limits_the_period_and_json_output(log_file):
    found, _ = read_log(log_file, since="2025-01-02")
    assert len(found) == 2
    data = json.loads(summarize_log(found, 0, "json"))
    assert data["calls"] == 2 and data["tools"] == {"describe_data": 1, "scan_folder": 1} and data["sessions"] == 2
    assert "no calls" in summarize_log(read_log(log_file, since="2030-01-01")[0])
    with pytest.raises(SourceError, match="times look like"):
        read_log(log_file, since="yesterday")


def test_audit_command(log_file, capsys):
    assert main(["audit", str(log_file)]) == 0
    assert "3 calls in 2 sessions" in capsys.readouterr().out
    assert main(["audit", str(log_file), "--since", "2025-01-03", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["calls"] == 1
    assert main(["audit", str(log_file.parent / "missing.jsonl")]) == 1
    assert "cannot read the audit log" in capsys.readouterr().err


# --- Configured through datanyx.toml ---------------------------------------------------------------------------------


def test_mcp_command_takes_roots_terms_detectors_and_log_from_the_config(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("datanyx.mcp_server.run", lambda *args, **kwargs: seen.update(args=args, kwargs=kwargs))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)
    (tmp_path / "datanyx.toml").write_text(
        '[mcp]\nroots = ["data"]\naudit_log = "audit.jsonl"\nallow_urls = true\n'
        '[anonymize]\nkeep_entities = ["DATE"]\nhide = ["Acme"]\n[[pattern]]\nkind = "TICKET"\nregex = "[A-Z]{3}-[0-9]{4}"\n',
        encoding="utf-8",
    )
    assert main(["mcp"]) == 0
    roots, allow_urls, _model, keep = seen["args"]
    assert roots == [str((tmp_path / "data").resolve())] and allow_urls is True and keep == ["DATE"]
    assert seen["kwargs"]["hide"] == ("Acme",) and seen["kwargs"]["audit_log"] == str((tmp_path / "audit.jsonl").resolve())
    assert [d.kind for d in seen["kwargs"]["detectors"]] == ["TICKET"]
    assert main(["mcp", "--root", "elsewhere", "--audit-log", "mine.jsonl"]) == 0
    assert seen["args"][0] == ["elsewhere"] and seen["kwargs"]["audit_log"] == "mine.jsonl"


def test_hide_terms_from_the_config_apply_to_anonymize_file(tmp_path):
    spacy_util = pytest.importorskip("spacy.util")
    if not spacy_util.is_package("en_core_web_lg"):
        pytest.skip("needs en_core_web_lg")
    (tmp_path / "t.txt").write_text("Globex called about EMP-123456.\n", encoding="utf-8")
    from datanyx.text import build_detectors

    custom = build_detectors([{"_type": "pattern", "kind": "EMPLOYEE_ID", "regex": "EMP-[0-9]{6}"}])
    server = create_server([tmp_path], hide=["Globex"], custom_detectors=custom)
    text = call(server, "anonymize_file", {"target": str(tmp_path / "t.txt")}).content[0].text
    assert "Globex" not in text and "EMP-123456" not in text and "<EMPLOYEE_ID_1>" in text
