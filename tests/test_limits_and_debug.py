"""A limit on rows read, and a private log of where unexpected failures happened. All data is synthetic."""

import asyncio
import gzip
import json

import pytest

from datanyx import KeyPolicy, extract, render
from datanyx.cli import main
from datanyx.debuglog import describe

CANARY = "zqcanaryvalue"


def csv_text(rows: int) -> str:
    return "id,name\n" + "".join(f"{i},n{i}\n" for i in range(1, rows + 1))


# --- max_rows ------------------------------------------------------------------------------------------------


def test_a_csv_cut_short_says_so_and_claims_nothing_about_size_or_keys(tmp_path):
    path = tmp_path / "big.csv"
    path.write_text(csv_text(100), encoding="utf-8")
    full = extract(str(path), policy=KeyPolicy(scale_hints=True, infer_keys=True))
    cut = extract(str(path), policy=KeyPolicy(scale_hints=True, infer_keys=True, max_rows=30))
    assert full.entities[0].scale == "fewer than 1,000 rows" and "unique" in render(full, "text")
    assert cut.entities[0].scale is None and "unique" not in render(cut, "text")
    assert any("read only the first 30 rows" in note for note in cut.notes)


def test_a_file_with_exactly_the_limit_is_not_cut_short(tmp_path):
    path = tmp_path / "exact.csv"
    path.write_text(csv_text(30), encoding="utf-8")
    schema = extract(str(path), policy=KeyPolicy(scale_hints=True, max_rows=30))
    assert schema.entities[0].scale == "fewer than 1,000 rows"
    assert not any("read only" in note for note in schema.notes)


def test_reading_stops_at_the_limit(tmp_path):
    """Everything after the limit is bad data: a read that went past it would fail."""
    path = tmp_path / "tail.jsonl"
    good = "\n".join(json.dumps({"id": i}) for i in range(10))
    path.write_text(good + "\nthis is not json\n", encoding="utf-8")
    assert extract(str(path), policy=KeyPolicy(max_rows=10)).entities[0].node is not None  # Not reached.
    with pytest.raises(Exception, match="invalid JSON"):
        extract(str(path))
    csv = tmp_path / "tail.csv"
    csv.write_bytes(csv_text(10).encode() + b"".join(b"%d,x\n" % i for i in range(11, 200_000)) + b"\xff\xfe,bad\n")
    assert extract(str(csv), policy=KeyPolicy(max_rows=10)).entities[0].node is not None


def test_json_lines_and_excel_are_limited_too(tmp_path):
    lines = tmp_path / "a.jsonl"
    lines.write_text("\n".join(json.dumps({"id": i}) for i in range(50)), encoding="utf-8")
    schema = extract(str(lines), policy=KeyPolicy(scale_hints=True, max_rows=5))
    assert schema.entities[0].scale is None and any("first 5 rows" in n for n in schema.notes)

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "people"
    sheet.append(["id", "name"])
    for i in range(1, 40):
        sheet.append([i, f"n{i}"])
    small = book.create_sheet("tiny")
    small.append(["id", "name"])
    small.append([1, "x"])
    book.save(tmp_path / "b.xlsx")
    result = extract(str(tmp_path / "b.xlsx"), policy=KeyPolicy(scale_hints=True, max_rows=10))
    scales = {e.name: e.scale for e in result.entities}
    assert scales == {"people": None, "tiny": "fewer than 1,000 rows"}  # Only the sheet that was cut short loses its hint.
    assert sum("first 10 rows" in n for n in result.notes) == 1


def test_a_large_json_array_is_sampled_when_streamed(tmp_path):
    pytest.importorskip("ijson")
    path = tmp_path / "a.json.gz"  # A compressed file is always streamed.
    path.write_bytes(gzip.compress(json.dumps([{"id": i, "tags": ["x"]} for i in range(200)]).encode()))
    schema = extract(str(path), policy=KeyPolicy(scale_hints=True, max_rows=20))
    assert schema.entities[0].scale is None and any("first 20 rows" in n for n in schema.notes)
    assert "tags" in render(schema, "text")
    other = tmp_path / "doc.json.gz"  # Not an array: described whole.
    other.write_bytes(gzip.compress(json.dumps({"a": 1, "b": [1, 2, 3]}).encode()))
    assert not any("first" in n for n in extract(str(other), policy=KeyPolicy(max_rows=1)).notes)


def test_command_line_and_config(tmp_path, capsys, monkeypatch):
    path = tmp_path / "c.csv"
    path.write_text(csv_text(50), encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "--max-rows", "5", "--format", "text"]) == 0
    assert "first 5 rows" in capsys.readouterr().err
    (tmp_path / "datanyx.toml").write_text("[schema]\nmax_rows = 7\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["schema", str(path), "--format", "text"]) == 0
    assert "first 7 rows" in capsys.readouterr().err


def test_the_mcp_owner_can_limit_rows_but_the_agent_cannot_change_it(tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    (tmp_path / "c.csv").write_text(csv_text(50), encoding="utf-8")
    server = create_server([tmp_path], max_rows=5, scale_hints=True)
    text = asyncio.run(server.call_tool("describe_data", {"target": str(tmp_path / "c.csv")})).content[0].text
    assert "first 5 rows" in text and "scale:" not in text


# --- the debug log -------------------------------------------------------------------------------------------


def test_describe_keeps_only_places_and_names():
    try:
        try:
            raise KeyError(CANARY)
        except KeyError as inner:
            raise ValueError(f"bad value {CANARY}") from inner
    except ValueError as error:
        record = describe(error)
    assert record["error"] == "ValueError" and record["causes"] == ["KeyError"]
    assert CANARY not in json.dumps(record)
    frame = record["frames"][-1]
    assert frame["function"] == "test_describe_keeps_only_places_and_names" and isinstance(frame["line"], int)


def test_an_unexpected_failure_is_logged_for_the_owner_and_not_for_the_agent(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from datanyx import mcp_server

    def explode(*_args, **_kwargs):
        raise RuntimeError(f"{CANARY} leaked into an exception message")

    monkeypatch.setattr(mcp_server, "extract", explode)
    (tmp_path / "c.csv").write_text("a\n1\n", encoding="utf-8")
    log = tmp_path / "debug" / "debug.jsonl"
    server = mcp_server.create_server([tmp_path], debug_log=log)
    with pytest.raises(Exception) as raised:
        asyncio.run(server.call_tool("describe_data", {"target": str(tmp_path / "c.csv")}))
    assert "internal failure (RuntimeError)" in str(raised.value) and CANARY not in str(raised.value)
    text = log.read_text(encoding="utf-8")
    assert CANARY not in text
    record = json.loads(text.splitlines()[0])
    assert record["tool"] == "describe_data" and record["error"] == "RuntimeError"
    assert any(frame["function"] == "describe_data" and frame["file"] == "datanyx/mcp_server.py" for frame in record["frames"])


def test_a_refusal_is_not_a_failure_and_without_a_log_nothing_is_written(tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    log = tmp_path / "debug.jsonl"
    server = create_server([tmp_path], debug_log=log)
    with pytest.raises(Exception):
        asyncio.run(server.call_tool("describe_data", {"target": str(tmp_path / "missing.csv")}))
    assert log.read_text(encoding="utf-8") == ""  # A message the agent may see is no bug.
    quiet = create_server([tmp_path])
    with pytest.raises(Exception):
        asyncio.run(quiet.call_tool("describe_data", {"target": str(tmp_path / "missing.csv")}))


def test_the_debug_log_is_private_and_its_setting_is_read_from_the_config(tmp_path, capsys, monkeypatch):
    from datanyx.debuglog import DebugLog

    path = tmp_path / "d.jsonl"
    DebugLog(path)
    assert path.exists()
    (tmp_path / "datanyx.toml").write_text('[mcp]\ndebug_log = "d.jsonl"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["config"]) == 0
    assert "debug_log" in capsys.readouterr().out
