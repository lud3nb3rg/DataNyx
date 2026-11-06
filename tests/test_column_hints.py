"""Column hints and composite keys, opt-in and coarse. All data is synthetic."""

import asyncio
import json

import pytest

from datanyx import KeyPolicy, extract, render, synthesize
from datanyx.cli import main
from datanyx.schema import Object

CANARY = "zqcanaryvalue"
HINTS = KeyPolicy(column_hints=True)
KEYS = KeyPolicy(infer_keys=True)


def write(path, header, rows):
    path.write_text(",".join(header) + "\n" + "\n".join(",".join(map(str, row)) for row in rows) + "\n", encoding="utf-8")
    return path


def fields_of(schema, name=None):
    entity = next(e for e in schema.entities if name is None or e.name == name)
    node = getattr(entity.node, "items", entity.node)
    return entity, node.fields if isinstance(node, Object) else {}


@pytest.fixture
def people(tmp_path):
    rows = []
    for i in range(1, 201):
        rows.append(
            [
                i,
                ["new", "active", "closed"][i % 3],  # Three values, never missing.
                f"{CANARY}{i}@example.com",  # All different, and longer than 9 characters.
                "" if i % 3 == 0 else f"n{i}",  # A third of them missing.
                "x" * 150 if i == 1 else "ok",  # A few long ones among short.
            ]
        )
    return write(tmp_path / "people.csv", ["id", "status", "email", "nickname", "bio"], rows)


def test_hints_are_off_unless_asked_for(people):
    schema = extract(str(people))
    assert not any(f.profile for f in fields_of(schema)[1].values())
    assert "x-profile" not in render(schema, "json-schema")


def test_each_column_gets_coarse_ranges_and_never_a_number(people):
    schema = extract(str(people), policy=HINTS)
    _, fields = fields_of(schema)
    assert fields["status"].profile == ("2 to 9 distinct values", "never missing", "text up to 9 characters")
    assert fields["email"].profile[0] == "100 to 999 distinct values" and fields["email"].profile[2] == "text up to 99 characters"
    assert fields["nickname"].profile[1] == "often missing"
    assert fields["bio"].profile == ("2 to 9 distinct values", "never missing", "text up to 999 characters")
    assert fields["id"].profile == ("100 to 999 distinct values", "never missing")  # A number has no text length.
    text = render(schema, "text")
    assert "status: string  -- 2 to 9 distinct values, never missing" in text
    document = json.loads(render(schema, "json-schema"))
    assert document["items"]["properties"]["status"]["x-profile"] == list(fields["status"].profile)
    assert "column hints are ranges" in " ".join(schema.notes)
    for name in ("json-schema", "text", "typescript", "pydantic", "sql", "summary"):
        output = render(schema, name)
        assert CANARY not in output and "200" not in output.replace("2024", "")  # No count and no value.


def test_a_column_with_many_values_stops_counting(tmp_path):
    path = write(tmp_path / "wide.csv", ["id", "token"], [[i, f"t{i}"] for i in range(1, 1501)])
    _, fields = fields_of(extract(str(path), policy=HINTS))
    assert fields["token"].profile[0] == "1,000 or more distinct values"


def test_a_constant_and_an_empty_column(tmp_path):
    path = write(tmp_path / "c.csv", ["id", "same", "gap"], [[i, "a", ""] for i in range(1, 41)])
    _, fields = fields_of(extract(str(path), policy=HINTS))
    assert fields["same"].profile[0] == "one value only"
    assert fields["gap"].profile == ("mostly missing",)  # No values, so no range of them.


def test_too_few_rows_and_partial_reads_say_nothing(people, tmp_path):
    small = write(tmp_path / "small.csv", ["id", "status"], [[i, "a"] for i in range(1, 10)])
    schema = extract(str(small), policy=HINTS)
    assert not any(f.profile for f in fields_of(schema)[1].values()) and any("no column hints" in n for n in schema.notes)
    cut = extract(str(people), policy=KeyPolicy(column_hints=True, max_rows=50))
    assert not any(f.profile for f in fields_of(cut)[1].values())


def test_hidden_columns_get_nothing_and_other_formats_work(tmp_path):
    rows = [{"id": i, "kind": "a" if i % 2 else "b", f"{CANARY} odd": f"v{i}"} for i in range(1, 41)]
    path = tmp_path / "r.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    schema = extract(str(path), policy=HINTS)
    _, fields = fields_of(schema)
    assert set(fields) == {"id", "kind"} and fields["kind"].profile[0] == "2 to 9 distinct values"
    assert CANARY not in render(schema, "text") + "".join(schema.notes)
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.append(["id", "kind"])
    for i in range(1, 41):
        book.active.append([i, "a"])
    book.save(tmp_path / "b.xlsx")
    assert fields_of(extract(str(tmp_path / "b.xlsx"), policy=HINTS))[1]["kind"].profile[0] == "one value only"


def test_other_sources_say_they_cannot(tmp_path):
    import sqlite3

    connection = sqlite3.connect(tmp_path / "d.db")
    connection.execute("create table t (a integer)")
    connection.commit()
    connection.close()
    schema = extract(str(tmp_path / "d.db"), policy=HINTS)
    assert any("no column hints" in n for n in schema.notes)


def test_hints_and_keys_can_be_asked_for_together(people):
    schema = extract(str(people), policy=KeyPolicy(column_hints=True, infer_keys=True))
    _, fields = fields_of(schema)
    assert fields["id"].unique and fields["id"].profile


# --- composite keys ------------------------------------------------------------------------------------------


@pytest.fixture
def shop(tmp_path):
    write(tmp_path / "orders.csv", ["order_id", "total"], [[o, 10] for o in range(1, 21)])
    lines = [[o, n, "x"] for o in range(1, 21) for n in range(1, 4)]  # 60 rows: unique only as an (order, line) pair.
    write(tmp_path / "order_lines.csv", ["order_id", "line_no", "sku"], lines)
    shipped = [[o, n, "ok"] for o in range(1, 11) for n in range(1, 3)]  # Pairs that exist in order_lines.
    write(tmp_path / "shipments.csv", ["order_id", "line_no", "status"], shipped)
    return tmp_path


def entity_named(schema, name):
    return next(e for e in schema.entities if e.name == name)


def test_two_columns_unique_together_are_a_composite_key(shop):
    schema = extract(str(shop), policy=KEYS)
    lines = entity_named(schema, "order_lines.csv")
    assert lines.inferred_unique_keys == (("line_no", "order_id"),)
    assert entity_named(schema, "orders.csv").inferred_unique_keys == ()
    assert any("2 composite unique key(s)" in note for note in schema.notes)  # Shipments are unique by pair too.


def test_a_composite_foreign_key_follows_the_pair(shop):
    schema = extract(str(shop), policy=KEYS)
    shipments = entity_named(schema, "shipments.csv")
    pair = [k for k in shipments.foreign_keys if len(k.columns) == 2]
    assert [(k.columns, k.referenced_entity, k.referenced_columns, k.inferred) for k in pair] == [
        (("line_no", "order_id"), "order_lines.csv", ("line_no", "order_id"), True)
    ]
    text = render(schema, "text")
    assert "unique (line_no, order_id)  -- inferred from the values" in text
    assert "foreign key (line_no, order_id) -> order_lines.csv (line_no, order_id)  -- inferred from the values" in text
    assert "x-unique-keys-inferred" in render(schema, "json-schema")
    sql = render(schema, "sql")
    assert "inferred unique together (not enforced)" in sql and "FOREIGN KEY (\"line_no\", \"order_id\")" not in sql


def test_a_pair_that_is_not_contained_is_not_a_join(shop):
    write(shop / "shipments.csv", ["order_id", "line_no", "status"], [[o, 9, "ok"] for o in range(1, 31)])
    shipments = entity_named(extract(str(shop), policy=KEYS), "shipments.csv")
    assert not [k for k in shipments.foreign_keys if len(k.columns) == 2]


def test_other_names_or_a_single_unique_column_make_no_composite(shop):
    write(shop / "shipments.csv", ["order_id", "item_no", "status"], [[o, n, "ok"] for o in range(1, 11) for n in range(1, 3)])
    shipments = entity_named(extract(str(shop), policy=KEYS), "shipments.csv")
    assert not [k for k in shipments.foreign_keys if len(k.columns) == 2]
    write(shop / "single.csv", ["part_id", "rev_id"], [[i, i % 3] for i in range(1, 41)])
    assert entity_named(extract(str(shop), policy=KEYS), "single.csv").inferred_unique_keys == ()  # part_id is unique alone.


def test_two_tables_with_the_same_composite_key_are_a_tie(tmp_path):
    for name in ("a", "b"):
        write(tmp_path / f"{name}.csv", ["order_id", "line_no"], [[o, n] for o in range(1, 11) for n in range(1, 4)])
    schema = extract(str(tmp_path), policy=KEYS)
    assert all(not [k for k in e.foreign_keys if len(k.columns) == 2] for e in schema.entities)


def test_synthetic_rows_keep_the_inferred_pairs(shop):
    schema = extract(str(shop), policy=KEYS)
    tables = {t.name: t for t in synthesize(schema, rows=30)}
    parents = {(r["order_id"], r["line_no"]) for r in tables["order_lines.csv"].rows}
    assert {(r["order_id"], r["line_no"]) for r in tables["shipments.csv"].rows} <= parents


def test_a_table_cut_short_claims_no_composite_key(shop):
    schema = extract(str(shop), policy=KeyPolicy(infer_keys=True, max_rows=30))
    assert entity_named(schema, "order_lines.csv").inferred_unique_keys == ()  # 60 rows, cut at 30.
    assert entity_named(schema, "shipments.csv").inferred_unique_keys  # 20 rows, read whole.


# --- command line, config, MCP -------------------------------------------------------------------------------


def test_command_line_config_and_server(people, tmp_path, capsys, monkeypatch):
    assert main(["--no-config", "schema", str(people), "--column-hints", "--format", "text"]) == 0
    assert "2 to 9 distinct values" in capsys.readouterr().out
    (tmp_path / "datanyx.toml").write_text("[schema]\ncolumn_hints = true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["schema", str(people), "--format", "text"]) == 0
    assert "never missing" in capsys.readouterr().out

    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    server = create_server([people.parent], column_hints=True)
    text = asyncio.run(server.call_tool("describe_data", {"target": str(people)})).content[0].text
    assert "2 to 9 distinct values" in text
    plain = asyncio.run(create_server([people.parent]).call_tool("describe_data", {"target": str(people)})).content[0].text
    assert "distinct values" not in plain
