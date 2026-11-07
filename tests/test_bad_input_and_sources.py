"""Bad input is a clear error, not a crash; and per-source behaviour that once went wrong."""

import json
import sqlite3

import pytest

from datanyx import extract, render
from datanyx.cli import main
from datanyx.privacy import KeyPolicy
from datanyx.sources import SourceError
from datanyx.sources.files import coerce_text
from datanyx.text import Anonymizer, PatternDetector
from datanyx.text.batch import anonymize_folder
from docx_helpers import docx

# --- Crashes --------------------------------------------------------------------------------------------------------------


def test_a_huge_number_in_a_cell_is_just_text():
    digits = "9" * 5000
    assert coerce_text(digits) == digits


def test_one_bad_file_doesnt_cost_a_folder(tmp_path, monkeypatch):
    (tmp_path / "good.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    from datanyx.sources import delimited_source

    original = delimited_source.DelimitedSource.extract

    def failing(self, target, policy, namespace=None):
        if target.endswith("bad.csv"):
            raise ValueError("quoting jane@corp.com")
        return original(self, target, policy, namespace)

    monkeypatch.setattr(delimited_source.DelimitedSource, "extract", failing)
    schema = extract(str(tmp_path))
    notes = " ".join(schema.notes)
    assert "good" in render(schema, "text") and "could not describe" in notes and "jane@corp.com" not in notes


def test_a_folder_survives_a_cell_past_the_digit_limit(tmp_path):
    (tmp_path / "wide.csv").write_text("a\n" + "9" * 5000 + "\n", encoding="utf-8")
    assert "a" in render(extract(str(tmp_path)), "text")


def test_a_date_that_doesnt_exist_in_yaml(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "x.yaml"
    path.write_text("when: 2024-13-45\n", encoding="utf-8")
    with pytest.raises(SourceError, match="invalid YAML"):
        extract(str(path))
    assert "could not describe" in " ".join(extract(str(tmp_path)).notes)  # And a folder goes on.


def test_a_recursive_yaml_alias_is_a_clear_error(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "x.yaml"
    path.write_text("a: &a [*a]\n", encoding="utf-8")
    with pytest.raises(SourceError, match="deeply nested"):
        extract(str(path))
    assert "could not describe" in " ".join(extract(str(tmp_path)).notes)


def test_deep_xml_is_a_clear_error(tmp_path):
    path = tmp_path / "x.xml"
    path.write_text("<a>" * 100_000 + "</a>" * 100_000, encoding="utf-8")
    with pytest.raises(SourceError, match="deeply nested"):
        extract(str(path))


def test_empty_input_to_synth_is_a_clear_error(tmp_path, capsys):
    sqlite3.connect(tmp_path / "e.db").close()
    assert main(["--no-config", "synth", str(tmp_path / "e.db")]) == 1
    assert "no tables to make fake data" in capsys.readouterr().err


def test_schema_wont_write_over_its_input(tmp_path, capsys):
    path = tmp_path / "c.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "-o", str(path)]) == 1
    assert path.read_text(encoding="utf-8") == '{"a": 1}' and "different files" in capsys.readouterr().err


def test_a_bad_schema_output_path_is_a_clear_error(tmp_path, capsys):
    path = tmp_path / "c.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "-o", str(tmp_path / "no" / "such" / "x")]) == 1
    assert "cannot write the output" in capsys.readouterr().err


def test_keep_names_doesnt_collide(tmp_path):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "a.txt").write_text("from a.person@corp.io", encoding="utf-8")
    (source / "a.eml").write_bytes(b"From: b.person@corp.io\n\nhello\n")
    result = anonymize_folder(str(source), str(target), Anonymizer([PatternDetector()]), keep_names=True)
    assert result.failed == [] and len(result.files) == 2 and len(list(target.iterdir())) == 2


def test_a_damaged_file_doesnt_end_a_folder(tmp_path):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "a.docx").write_bytes(docx(b"\xff\x00\xff"))
    (source / "b.txt").write_text("ok", encoding="utf-8")
    result = anonymize_folder(str(source), str(target), Anonymizer([PatternDetector()]))
    assert len(result.failed) == 1 and len(result.files) == 1


def test_an_unexpected_failure_is_reported_by_type_only(tmp_path, monkeypatch):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "a.txt").write_text("ok", encoding="utf-8")

    def failing(path, anonymizer):
        raise ValueError("quoting jane@corp.com")

    monkeypatch.setattr("datanyx.text.batch.anonymize_file", failing)
    result = anonymize_folder(str(source), str(target), Anonymizer([]))
    assert "ValueError" in result.failed[0] and "jane@corp.com" not in result.failed[0]


@pytest.mark.parametrize("native", ["union", "%8.0g", "timestamp[ms]"])
def test_other_sources_type_names_arent_sql(native):
    from datanyx.renderers import render_sql_ddl
    from datanyx.schema import Array, DataSchema, Entity, Field, Object, String

    node = Array(Object({"a": Field(String(), True, native)}))
    ddl = render_sql_ddl(DataSchema("avro", (Entity(None, node),)))
    assert native not in ddl
    sqlite3.connect(":memory:").executescript(ddl)


def test_a_database_keeps_its_own_types():
    from datanyx.renderers import render_sql_ddl
    from datanyx.schema import DataSchema, Entity, Field, Object, String

    node = Object({"a": Field(String(), True, "VARCHAR(255)")})
    for kind in ("sql", "duckdb"):
        assert "VARCHAR(255)" in render_sql_ddl(DataSchema(kind, (Entity("t", node),)))


def test_synth_makes_a_sqlite_file_from_avro(tmp_path, capsys):
    fastavro = pytest.importorskip("fastavro")
    record = {"type": "record", "name": "r", "fields": [{"name": "a", "type": ["null", "string"]}]}
    path = tmp_path / "t.avro"
    with open(path, "wb") as stream:
        fastavro.writer(stream, fastavro.parse_schema(record), [{"a": "x"}])
    out = tmp_path / "o.db"
    assert main(["--no-config", "synth", str(path), "--format", "sqlite", "-o", str(out), "--rows", "2"]) == 0
    assert out.exists()


# --- Sources --------------------------------------------------------------------------------------------------------------


def test_foreign_keys_a_server_denies_cost_only_that_note(tmp_path, monkeypatch):
    path = tmp_path / "s.db"
    connection = sqlite3.connect(path)
    connection.executescript("create table a (id integer primary key); create table b (id integer primary key)")
    connection.close()

    def denied(*args):
        raise RuntimeError("permission denied")

    monkeypatch.setattr("datanyx.sources.sql_source._read_foreign_keys", denied)
    schema = extract(str(path))
    assert {entity.name for entity in schema.entities} == {"a", "b"}
    assert sum("foreign keys" in note for note in schema.notes) == 2


@pytest.mark.parametrize(
    ("url", "argument"),
    [
        ("postgresql://h/d", "connect_timeout"),
        ("postgresql+pg8000://h/d", "timeout"),
        ("mysql://h/d", "connect_timeout"),
        ("mssql+pyodbc://h/d", "timeout"),
        ("oracle://h/d", "tcp_connect_timeout"),
    ],
)
def test_the_timeout_argument_is_the_drivers(monkeypatch, url, argument):
    from datanyx.sources import sql_source

    seen = {}
    monkeypatch.setattr(sql_source.sa, "create_engine", lambda url, **options: seen.update(options))
    sql_source._create_engine(url)
    assert argument in seen["connect_args"]


def test_an_unknown_driver_keeps_its_default_timeout(monkeypatch):
    from datanyx.sources import sql_source

    seen = {}
    monkeypatch.setattr(sql_source.sa, "create_engine", lambda url, **options: seen.update(options))
    sql_source._create_engine("oracle+cx_oracle://h/d")
    assert seen["connect_args"] == {}


def test_every_mllp_frame_is_read(tmp_path):
    frame = "\x0bMSH|^~\\&|A|B\rPID|1||123\r\x1c\r"
    other = "\x0bMSH#^~\\&#A#B\rOBX#1#X\r\x1c\r"
    path = tmp_path / "m.hl7"
    path.write_bytes((frame + other).encode())
    schema = extract(str(path))
    assert {entity.name for entity in schema.entities} == {"MSH", "PID", "OBX"}
    assert not any("skipped" in note for note in schema.notes)


def test_hl7_says_when_it_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("datanyx.sources.hl7_source._MAX_SEGMENT_TYPES", 1)
    path = tmp_path / "m.hl7"
    path.write_text("MSH|^~\\&|A\rPID|1\r", encoding="utf-8")
    assert any("left out 1" in note for note in extract(str(path)).notes)


def test_only_real_fhir_types_are_named(tmp_path):
    path = tmp_path / "x.fhir.ndjson"
    path.write_text('{"resourceType":"Patient","a":1}\n{"resourceType":"Whitfield","a":1}\n', encoding="utf-8")
    names = {entity.name for entity in extract(str(path)).entities}
    assert "Patient" in names and "Whitfield" not in names and any(name.startswith("Resource_") for name in names)


def test_fhir_says_when_it_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("datanyx.sources.fhir_source._MAX_TYPES", 1)
    path = tmp_path / "x.fhir.ndjson"
    path.write_text('{"resourceType":"Patient"}\n{"resourceType":"Observation"}\n', encoding="utf-8")
    assert any("left out 1" in note for note in extract(str(path)).notes)


def test_key_evidence_comes_from_a_streamed_file_too(tmp_path, monkeypatch):
    pytest.importorskip("ijson")
    monkeypatch.setattr("datanyx.sources.json_source.STREAM_THRESHOLD_BYTES", 1)
    path = tmp_path / "big.json"
    path.write_text(json.dumps([{"customer_id": i, "n": 1} for i in range(50)]), encoding="utf-8")
    schema = extract(str(path), policy=KeyPolicy(infer_keys=True))
    records = schema.entities[0].node.items
    assert records.fields["customer_id"].unique


def test_init_local_finds_the_repository_from_a_subfolder(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    assert main(["--no-config", "init", "--local"]) == 0
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines() == ["sub/datanyx.local.toml"]
    assert "to .gitignore" in capsys.readouterr().out


def test_init_local_outside_a_repository_writes_no_ignore_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A repository further up would count, so only check it doesn't appear here.
    assert main(["--no-config", "init", "--local"]) == 0
    assert not (tmp_path / ".gitignore").exists()
