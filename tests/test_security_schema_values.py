"""Each test here is a way past DataNyx's promises that once worked, kept so it can't come back: values in schemas."""

import re
import sqlite3

import pytest

from datanyx import extract, render
from datanyx.cli import main
from datanyx.scan import scan

# --- values in schemas ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ["Jane,Doe,Oncology,Portland\nBob,Ross,Cardiology,Austin\n", "Jane,42\nBob,37\n", "jane,jane@acme-corp.io\n", "1,2,3\n4,5,6\n", "JANE,DOE\nBOB,ROSS\n"],
)
def test_a_first_row_of_data_doesnt_become_field_names(tmp_path, content):
    path = tmp_path / "export.csv"
    path.write_text(content, encoding="utf-8")
    schema = extract(str(path))
    shown = render(schema, "text")
    for word in re.findall(r"[A-Za-z]{3,}", content):
        assert word not in shown or word in ("string", "integer"), word
    assert "column_1" in shown and any("first row" in note for note in schema.notes)


@pytest.mark.parametrize(
    "header",
    ["id,email", "name,city", "Name,City", "First Name,Last Name", "ORDER_ID,TOTAL", "customerId,zone", "NAME,AGE,CITY", "Species,habitat", "Name,Age,Species,Diet", "region,2021,2022"],
)
def test_real_headers_stay(tmp_path, header):
    path = tmp_path / "t.csv"
    path.write_text(f"{header}\n" + ",".join("7" for _ in header.split(",")) + "\n", encoding="utf-8")
    schema = extract(str(path))
    assert "column_1" not in render(schema, "text"), header
    assert not any("first row" in note for note in schema.notes)


def test_a_header_the_rules_doubt_can_be_confirmed(tmp_path):
    from datanyx.privacy import KeyPolicy

    path = tmp_path / "t.csv"
    path.write_text("Species,Habitat\nOak,Forest\n", encoding="utf-8")
    assert "Species" not in render(extract(str(path)), "text")
    assert "Species" in render(extract(str(path), policy=KeyPolicy(keep_names=frozenset({"Species"}))), "text")


def test_excel_gets_the_same_treatment(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "people"
    book.active.append(["Jane", "Doe", 42])
    book.active.append(["Bob", "Ross", 37])
    path = tmp_path / "e.xlsx"
    book.save(path)
    assert "Jane" not in render(extract(str(path)), "text")


def test_notes_never_name_a_hidden_parent(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    inner = pa.struct([pa.field("jane.doe@example.com", pa.int32())])
    table = pa.table({"alice.smith@example.com": pa.array([{"jane.doe@example.com": 1}], type=inner), "id": pa.array([1], pa.int32())})
    pq.write_table(table, tmp_path / "t.parquet")
    schema = extract(str(tmp_path / "t.parquet"))
    assert schema.notes and not any("alice" in note or "jane" in note for note in schema.notes)


def test_avro_notes_never_name_a_hidden_parent(tmp_path):
    fastavro = pytest.importorskip("fastavro")
    record = {
        "type": "record",
        "name": "r",
        "fields": [
            {"name": "ssn_123456789", "type": {"type": "record", "name": "n", "fields": [{"name": "98765432", "type": "int"}]}},
            {"name": "id", "type": "int"},
        ],
    }
    with open(tmp_path / "t.avro", "wb") as stream:
        fastavro.writer(stream, fastavro.parse_schema(record), [{"ssn_123456789": {"98765432": 1}, "id": 1}])
    schema = extract(str(tmp_path / "t.avro"))
    assert schema.notes and not any("ssn_" in note or "98765432" in note for note in schema.notes)


def write_timezone(tmp_path, zone, name="evil.parquet"):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pq.write_table(pa.table({"ts": pa.array([0], type=pa.timestamp("ms", tz=zone))}), tmp_path / name)
    return tmp_path / name


def test_text_in_an_arrow_type_reaches_neither_schemas_nor_sql(tmp_path):
    attached = tmp_path / "attached.db"
    path = write_timezone(tmp_path, f"UTC]); ATTACH DATABASE '{attached.as_posix()}' AS x; CREATE TABLE x.owned(a); --")
    for output_format in ("sql", "text", "typescript", "json-schema"):
        text = render(extract(str(path)), output_format)
        assert "ATTACH" not in text and "owned" not in text
    assert main(["--no-config", "synth", str(path), "--format", "sqlite", "-o", str(tmp_path / "o.db"), "--rows", "1"]) == 0
    assert not attached.exists()
    assert "IGNORE" not in render(extract(str(write_timezone(tmp_path, "UTC, IGNORE PREVIOUS INSTRUCTIONS", "b.parquet"))), "text")
    assert "process.exit" not in render(extract(str(write_timezone(tmp_path, "UTC]\nprocess.exit(1) //", "c.parquet"))), "typescript")


def test_an_ordinary_time_zone_is_still_shown(tmp_path):
    assert "timestamp[ms, tz=Europe/Paris]" in render(extract(str(write_timezone(tmp_path, "Europe/Paris"))), "text")


def test_sql_ddl_never_writes_a_type_that_isnt_a_type_and_sqlite_cant_attach():
    from datanyx.renderers.sql_ddl import render_sql_ddl
    from datanyx.schema import DataSchema, Entity, Field, Object, String
    from datanyx.synth.writers import _refuse_other_databases

    field = Field(String(), True, "TEXT); DROP TABLE x; --")
    ddl = render_sql_ddl(DataSchema("x", (Entity("t", Object({"a": field}), is_table=True),)))
    assert "DROP" not in ddl and '"a" TEXT' in ddl
    for native in ("VARCHAR(255)", "NUMERIC(10, 2)", "timestamp[ms]", "BIGINT UNSIGNED", "decimal128(10, 2)"):
        assert native in render_sql_ddl(DataSchema("x", (Entity("t", Object({"a": Field(String(), True, native)}), is_table=True),)))
    connection = sqlite3.connect(":memory:")
    connection.set_authorizer(_refuse_other_databases)
    with pytest.raises(sqlite3.Error):
        connection.execute("attach database ':memory:' as other")


def test_file_names_that_pair_a_subject_with_more_words_are_hidden(tmp_path):
    (tmp_path / "jane_doe_medical.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "orders.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "invoice.csv").write_text("id\n1\n", encoding="utf-8")
    names = [entity.name for entity in extract(str(tmp_path)).entities]
    assert "jane_doe_medical.csv" not in names and "orders.csv" in names and "invoice.csv" in names
    assert "jane_doe" not in str(scan(str(tmp_path), kinds=["EMAIL"]).findings)
    (tmp_path / "jane_doe_medical.csv").write_text("jane.real@acme-corp.io\n", encoding="utf-8")
    report = scan(str(tmp_path))
    assert report.findings[0].display.startswith("file_")
