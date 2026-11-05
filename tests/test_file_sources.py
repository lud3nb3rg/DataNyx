"""CSV, TSV, Parquet, Excel, YAML and XML: right types, and no values in the output."""

import json

import pytest

from datanyx import SourceError, extract, render
from datanyx.cli import main

CANARY = "zq-canary-value"


def props(path, **options):
    schema = json.loads(render(extract(str(path), **options)))
    return schema["items"]["properties"] if schema.get("type") == "array" else schema["properties"]


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- CSV / TSV ---------------------------------------------------------------


def test_csv_types_and_optional_columns(tmp_path):
    path = write(tmp_path, "d.csv", "id,email,joined,score,active,zip\n1,a@x.com,2024-01-02,1.5,true,007\n2,b@x.com,2024-01-03,,false,008\n")
    p = props(path)
    assert p["id"] == {"type": "integer"}
    assert p["email"] == {"type": "string", "format": "email"}
    assert p["joined"] == {"type": "string", "format": "date"}
    assert p["score"] == {"type": ["number", "null"]}
    assert p["active"] == {"type": "boolean"}
    assert p["zip"] == {"type": "string"}  # Leading zeros: an identifier, not a number.


def test_csv_short_rows_make_columns_optional(tmp_path):
    path = write(tmp_path, "d.csv", "a,b\n1,x\n2\n")
    schema = json.loads(render(extract(str(path))))
    assert schema["items"]["required"] == ["a"]


def test_csv_wide_rows_are_noted_not_leaked(tmp_path):
    path = write(tmp_path, "d.csv", f"a\n1,{CANARY}\n")
    result = extract(str(path))
    assert any("more cells" in note for note in result.notes)
    assert CANARY not in render(extract(str(path)))


def test_csv_blank_and_duplicate_headers(tmp_path):
    path = write(tmp_path, "d.csv", "a,a,\n1,2,3\n")
    assert set(props(path)) == {"a", "a_2", "column_3"}


def test_csv_header_that_is_data_is_hidden(tmp_path):
    path = write(tmp_path, "d.csv", f"id,jane.doe@example.com\n1,{CANARY}\n")
    output = render(extract(str(path)))
    assert "jane.doe" not in output and CANARY not in output


def test_tsv(tmp_path):
    path = write(tmp_path, "d.tsv", "a\tb\n1\tx\n")
    assert props(path) == {"a": {"type": "integer"}, "b": {"type": "string"}}


def test_empty_csv(tmp_path):
    path = write(tmp_path, "d.csv", "")
    assert json.loads(render(extract(str(path)))).get("type") == "array"


# --- YAML --------------------------------------------------------------------


def test_yaml_types_dates_and_non_string_keys(tmp_path):
    pytest.importorskip("yaml")
    path = write(tmp_path, "d.yaml", "n: 1\nwhen: 2024-01-02\nstamp: 2024-01-02 03:04:05\nl: [a]\n3: x\n")
    p = props(path)
    assert p["n"] == {"type": "integer"}
    assert p["when"] == {"type": "string", "format": "date"}
    assert p["stamp"] == {"type": "string", "format": "date-time"}
    assert p["l"] == {"type": "array", "items": {"type": "string"}}


def test_yaml_multiple_documents_are_an_array(tmp_path):
    pytest.importorskip("yaml")
    path = write(tmp_path, "d.yml", "a: 1\n---\na: 2\n")
    assert json.loads(render(extract(str(path))))["type"] == "array"


def test_yaml_does_not_run_code(tmp_path):
    pytest.importorskip("yaml")
    path = write(tmp_path, "d.yaml", "x: !!python/object/apply:os.system ['echo hi']\n")
    with pytest.raises(SourceError) as error:
        extract(str(path))
    assert "os.system" not in str(error.value)


def test_yaml_error_does_not_quote_the_text(tmp_path):
    pytest.importorskip("yaml")
    path = write(tmp_path, "d.yaml", f"a: [{CANARY}\nb: : :\n")
    with pytest.raises(SourceError) as error:
        extract(str(path))
    assert CANARY not in str(error.value)


# --- XML ---------------------------------------------------------------------


def test_xml_attributes_text_and_repeats(tmp_path):
    path = write(tmp_path, "d.xml", '<c xmlns:n="u"><n:item id="1"><n>5</n></n:item><n:item id="2"/></c>')
    schema = json.loads(render(extract(str(path))))
    item = schema["properties"]["c"]["properties"]["item"]
    assert item["type"] == "array"
    assert item["items"]["properties"]["-id"] == {"type": "integer"}
    assert "xmlns" not in render(extract(str(path)))


def test_xml_rejects_entities(tmp_path):
    path = write(tmp_path, "d.xml", '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>')
    with pytest.raises(SourceError, match="DOCTYPE"):
        extract(str(path))


def test_xml_error_does_not_quote_the_text(tmp_path):
    path = write(tmp_path, "d.xml", f"<a>{CANARY}</b>")
    with pytest.raises(SourceError) as error:
        extract(str(path))
    assert CANARY not in str(error.value)


def test_xml_values_never_appear(tmp_path):
    path = write(tmp_path, "d.xml", f'<a k="{CANARY}"><b>{CANARY}</b></a>')
    assert CANARY not in render(extract(str(path)))


# --- Parquet -----------------------------------------------------------------


def test_parquet_types_and_hidden_names(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "id": [1, 2],
            "name": [CANARY, "b"],
            "when": pa.array([1, 2], pa.timestamp("s")),
            "nested": [{"x": 1}, {"x": 2}],
            "tags": [["a"], []],
            "jane.doe@example.com": [1.5, 2.5],
        }
    )
    path = tmp_path / "d.parquet"
    pq.write_table(table, path)
    result = render(extract(str(path)))
    p = props(path)
    assert p["when"]["anyOf"][0] == {"type": "string", "format": "date-time"} or p["when"]["type"]
    assert p["nested"]["anyOf"][0]["properties"]["x"]
    assert "jane.doe" not in result and CANARY not in result
    assert json.loads(result)["items"]["additionalProperties"]


def test_parquet_pandas_metadata_is_ignored(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    table = pa.table({"a": [1]}).replace_schema_metadata({"pandas": CANARY})
    path = tmp_path / "d.parquet"
    pq.write_table(table, path)
    assert CANARY not in render(extract(str(path)))


def test_parquet_garbage_is_an_error_without_content(tmp_path):
    pytest.importorskip("pyarrow")
    path = write(tmp_path, "d.parquet", CANARY)
    with pytest.raises(SourceError) as error:
        extract(str(path))
    assert CANARY not in str(error.value)


# --- Excel -------------------------------------------------------------------


def test_excel_sheets_types_and_renamed_sheets(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["id", "who", "when"])
    import datetime

    sheet.append([1, CANARY, datetime.datetime(2024, 1, 2, 3, 4)])
    sheet.append([2, None, None])
    workbook.create_sheet("Q1 Jane Doe").append(["a"])
    path = tmp_path / "d.xlsx"
    workbook.save(path)

    result = extract(str(path))
    output = render(result)
    assert [entity.name for entity in result.entities] == ["Sales", "sheet_2"]
    assert "Jane" not in output and CANARY not in output
    sales = json.loads(output)["$defs"]["Sales"]["items"]["properties"]
    assert sales["id"] == {"type": "integer"}
    assert sales["who"] == {"type": ["string", "null"]}


def test_excel_rejects_stdin(tmp_path):
    with pytest.raises(SourceError, match="stdin"):
        extract("-", source="excel")


# --- Detection and CLI -------------------------------------------------------


def test_unknown_extension_asks_for_source(tmp_path):
    path = write(tmp_path, "d.dat", "a,b\n1,2\n")
    with pytest.raises(SourceError, match="--source"):
        extract(str(path))
    assert props(path, source="csv")["a"] == {"type": "integer"}


def test_cli_reads_csv(tmp_path, capsys):
    path = write(tmp_path, "d.csv", "a\n1\n")
    assert main(["schema", str(path), "--format", "text"]) == 0
    assert "a: integer" in capsys.readouterr().out


# --- Streaming JSON ----------------------------------------------------------


def test_streamed_json_matches_the_standard_reader(tmp_path, monkeypatch):
    pytest.importorskip("ijson")
    from datanyx.sources import json_source

    document = {
        "users": [{"id": 1, "mail": "a@x.com", "tags": ["a"], "n": None}, {"id": 2.5, "mail": "b@x.com"}],
        "meta": {"ok": True, "when": "2024-01-02"},
        "empty": [],
    }
    path = write(tmp_path, "d.json", "﻿" + json.dumps(document))
    standard = render(extract(str(path)))
    monkeypatch.setattr(json_source, "STREAM_THRESHOLD_BYTES", 0)
    assert render(extract(str(path))) == standard


def test_streamed_json_errors_do_not_quote_the_text(tmp_path, monkeypatch):
    pytest.importorskip("ijson")
    from datanyx.sources import json_source

    monkeypatch.setattr(json_source, "STREAM_THRESHOLD_BYTES", 0)
    path = write(tmp_path, "d.json", f'{{"a": {CANARY} ]')
    with pytest.raises(SourceError) as error:
        extract(str(path))
    assert CANARY not in str(error.value)


# --- Folders -----------------------------------------------------------------


def make_folder(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "orders.csv").write_text("id,user_id\n1,2\n", encoding="utf-8")
    (folder / "users.json").write_text('[{"user_id": 2, "name": "x"}]', encoding="utf-8")
    (folder / "jane doe medical.csv").write_text(f"a\n{CANARY}\n", encoding="utf-8")
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    (folder / ".hidden.csv").write_text("a\n1\n", encoding="utf-8")
    (folder / "broken.json").write_text(f"{{{CANARY}", encoding="utf-8")
    return folder


def test_folder_describes_each_readable_file(tmp_path):
    result = extract(str(make_folder(tmp_path)))
    names = [entity.name for entity in result.entities]
    assert "orders.csv" in names and "users.json" in names
    assert "file_2" in names  # "jane doe medical.csv": the name looks like data.
    assert not any("jane" in name for name in names)
    joined = "\n".join(result.notes)
    assert "orders.csv and users.json share fields: user_id" in joined
    assert "could not describe broken.json" in joined
    assert "skipped 1 file(s)" in joined
    assert CANARY not in joined and CANARY not in render(result) and "jane" not in joined


def test_folder_recursion_and_source_filter(tmp_path):
    folder = make_folder(tmp_path)
    (folder / "sub").mkdir()
    (folder / "sub" / "more.csv").write_text("z\n1\n", encoding="utf-8")
    assert "sub/more.csv" not in [e.name for e in extract(str(folder)).entities]
    deep = extract(str(folder), recursive=True)
    assert "sub/more.csv" in [e.name for e in deep.entities]
    only_json = extract(str(folder), source="json")
    assert [e.name for e in only_json.entities] == ["users.json"]


def test_folder_rejects_namespace_and_cli_works(tmp_path, capsys):
    folder = make_folder(tmp_path)
    with pytest.raises(SourceError):
        extract(str(folder), namespace="x")
    assert main(["schema", str(folder), "--format", "text"]) == 0
    assert "orders.csv:" in capsys.readouterr().out


def test_folder_with_sqlite_prefixes_table_names(tmp_path):
    import sqlite3

    folder = tmp_path / "d"
    folder.mkdir()
    connection = sqlite3.connect(folder / "shop.db")
    connection.execute("create table items (id integer primary key)")
    connection.execute("create table lines (item_id integer references items(id))")
    connection.close()
    result = extract(str(folder))
    assert sorted(e.name for e in result.entities) == ["shop.db#items", "shop.db#lines"]
    lines = next(e for e in result.entities if e.name.endswith("lines"))
    assert lines.foreign_keys[0].referenced_entity == "shop.db#items"
