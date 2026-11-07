"""Names that must stay out of notes, headers and catalogs, whatever the format."""

import sqlite3

import pytest

from datanyx import extract, render, schema_of
from datanyx.cli import main
from datanyx.privacy import KeyPolicy

# --- Names in notes -------------------------------------------------------------------------------------


def test_parquet_notes_dont_name_a_hidden_parent(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    inner = pa.struct([("bob@example.com", pa.string())])
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"alice@example.com": pa.array([{"bob@example.com": "x"}], type=inner)}), path)
    schema = extract(str(path))
    assert schema.notes and not any("@example.com" in note for note in schema.notes)
    assert "@example.com" not in render(schema, "text")


def test_avro_notes_dont_name_a_hidden_parent(tmp_path):
    fastavro = pytest.importorskip("fastavro")
    inner = {"type": "record", "name": "inner", "fields": [{"name": "field_a", "type": "string"}]}
    record = {"type": "record", "name": "outer", "fields": [{"name": "Long_Field_Name_" + "1234567", "type": inner}]}
    path = tmp_path / "t.avro"
    with open(path, "wb") as stream:
        fastavro.writer(stream, fastavro.parse_schema(record), [{"Long_Field_Name_1234567": {"field_a": "x"}}])
    schema = extract(str(path), policy=KeyPolicy(map_paths=frozenset({"$[].{*}"})))
    assert not any("Long_Field_Name" in note for note in schema.notes)
    assert any("{*}" in note for note in schema.notes)


def test_a_csv_with_no_header_doesnt_show_its_first_row(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("Jane,Doe,jane@corp.com\nBob,Roe,bob@corp.com\n", encoding="utf-8")
    schema = extract(str(path))
    text = render(schema, "text")
    assert "Jane" not in text and "Doe" not in text and "column_1" in text and any("first row" in note for note in schema.notes)


def test_a_header_with_plain_names_is_still_a_header(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("name,email\nJane,jane@corp.com\n", encoding="utf-8")
    text = schema_of(str(path), "text")
    assert "name" in text and "column_1" not in text


# --- One shared rule for hiding names in catalogs ------------------------------------------------------------------------


def test_sql_and_duckdb_hide_names_the_same_way(tmp_path):
    path = tmp_path / "s.db"
    connection = sqlite3.connect(path)
    connection.execute('create table t (id integer primary key, "alice@example.com" text)')
    connection.close()
    schema = extract(str(path))
    node = schema.entities[0].node
    assert "id" in node.fields and node.extra is not None and node.key_format == "email"
    assert any("hid data-like names at $.t (key shape)" in note for note in schema.notes)
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "s.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute('create table t (id integer primary key, "alice@example.com" varchar)')
    connection.close()
    schema = extract(str(path))
    node = schema.entities[0].node
    assert "id" in node.fields and node.extra is not None and node.key_format == "email"
    assert any("hid data-like names at $.t (key shape)" in note for note in schema.notes)


def test_output_is_utf8_from_one_place(tmp_path, capsysbinary):
    path = tmp_path / "c.csv"
    path.write_text("naïve_id\n1\n", encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "--format", "text"]) == 0
    assert "naïve_id".encode() in capsysbinary.readouterr().out
