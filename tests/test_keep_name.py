"""--keep-name reveals exactly the names asked for and nothing else. Synthetic data only."""

import json
import sqlite3
from contextlib import closing

from datanyx import KeyPolicy, extract, render
from datanyx.cli import main

CANARY = "CANARY"


def keep(*names, map_paths=()):
    return KeyPolicy(keep_names=frozenset(names), map_paths=frozenset(map_paths))


def write_json(tmp_path, data):
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def make_db(tmp_path, script):
    path = tmp_path / "data.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(script)
    return str(path)


def json_schema(path, policy):
    return json.loads(render(extract(path, policy=policy)))


def test_json_key_with_a_data_shape_is_shown(tmp_path):
    path = write_json(tmp_path, {"id": 1, "report_2024": {"total": 5}})
    assert "report_2024" not in json_schema(path, keep())["properties"]
    properties = json_schema(path, keep("report_2024"))["properties"]
    assert properties["report_2024"]["properties"] == {"total": {"type": "integer"}}


def test_other_json_keys_stay_hidden(tmp_path):
    path = write_json(tmp_path, {"report_2024": 1, f"{CANARY}@example.com": 2})
    schema = extract(path, policy=keep("report_2024"))
    output = render(schema) + render(schema, "text") + "".join(schema.notes)
    assert "report_2024" in output
    assert CANARY not in output


def test_rare_json_key_is_shown(tmp_path):
    records = [{"id": i} for i in range(10)]
    records[0]["legacy_flag"] = True
    path = write_json(tmp_path, records)
    assert "legacy_flag" not in json_schema(path, keep())["items"].get("properties", {})
    assert "legacy_flag" in json_schema(path, keep("legacy_flag"))["items"]["properties"]


def test_one_name_can_be_kept_from_a_large_map(tmp_path):
    data = {f"entry_{chr(97 + i)}": {"age": i} for i in range(10)}
    schema = json_schema(write_json(tmp_path, data), keep("entry_a"))
    assert set(schema["properties"]) == {"entry_a"}
    assert schema["additionalProperties"]["properties"] == {"age": {"type": "integer"}}


def test_nested_keys_are_still_checked_under_a_kept_key(tmp_path):
    path = write_json(tmp_path, {"report_2024": {f"{CANARY}@example.com": 1}})
    output = render(extract(path, policy=keep("report_2024")))
    assert "report_2024" in output
    assert CANARY not in output


def test_map_path_wins_over_keep_name(tmp_path):
    path = write_json(tmp_path, {"users": {"report_2024": 1}})
    schema = json_schema(path, keep("report_2024", map_paths=["$.users"]))
    assert "properties" not in schema["properties"]["users"]


def test_names_match_exactly(tmp_path):
    path = write_json(tmp_path, {"id": "x", "Report_2024": 2})
    assert set(json_schema(path, keep("report_2024"))["properties"]) == {"id"}


def test_sqlite_table_is_shown_and_keys_to_it_return(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE "Order Details" (id INTEGER PRIMARY KEY);
        CREATE TABLE line (ref INT REFERENCES "Order Details" (id));
        """,
    )
    assert set(json_schema(path, keep())["$defs"]) == {"line"}
    defs = json_schema(path, keep("Order Details"))["$defs"]
    assert set(defs) == {"Order Details", "line"}
    assert defs["line"]["x-foreign-keys"][0]["references"]["table"] == "Order Details"
    assert defs["Order Details"]["x-primary-key"] == ["id"]


def test_sqlite_column_is_shown_and_keys_using_it_return(tmp_path):
    path = make_db(tmp_path, 'CREATE TABLE t ("First Name" TEXT PRIMARY KEY, "Last Name" TEXT);')
    assert "x-primary-key" not in json_schema(path, keep())["$defs"]["t"]
    table = json_schema(path, keep("First Name"))["$defs"]["t"]
    assert set(table["properties"]) == {"First Name"}
    assert table["x-primary-key"] == ["First Name"]
    assert table["additionalProperties"] == {"type": ["string", "null"]}


def test_sqlite_map_path_wins_over_keep_name(tmp_path):
    path = make_db(tmp_path, 'CREATE TABLE t ("First Name" TEXT, plain TEXT);')
    table = json_schema(path, keep("First Name", map_paths=["$.t"]))["$defs"]["t"]
    assert "properties" not in table


def test_keeping_a_name_cannot_expose_sqlite_internal_tables(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE a (id INTEGER PRIMARY KEY AUTOINCREMENT);")
    assert set(json_schema(path, keep("sqlite_sequence"))["$defs"]) == {"a"}


def test_cli_flag_is_repeatable(tmp_path, capsys):
    path = write_json(tmp_path, {"report_2023": 1, "report_2024": 2, "report_2025": 3})
    code = main(["schema", path, "--keep-name", "report_2023", "--keep-name", "report_2024"])
    assert code == 0
    schema = json.loads(capsys.readouterr().out)
    assert set(schema["properties"]) == {"report_2023", "report_2024"}
