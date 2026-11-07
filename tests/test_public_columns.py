"""Public columns: the owner declares which columns may list their labels. This is the one place values are shown,
so the safety nets matter as much as the feature: few labels, plain-looking ones, seen often, never counted."""

import asyncio
import json

import pytest

from datanyx import KeyPolicy, extract, render, synthesize
from datanyx.cli import main
from datanyx.keys import MAX_LISTED_LABELS, MIN_LABEL_COUNT
from datanyx.renderers import RENDERERS
from datanyx.synth import render_table

CANARY = "zqcanaryvalue"
COUNTRIES = ["US", "us", "Canada", "FR"]


def public(*names, **more):
    return KeyPolicy(public_columns=frozenset(names), **more)


def write(path, header, rows):
    path.write_text(",".join(header) + "\n" + "\n".join(",".join(map(str, row)) for row in rows) + "\n", encoding="utf-8")
    return path


def fields_of(schema):
    entity = schema.entities[0]
    node = getattr(entity.node, "items", entity.node)
    return node.fields


@pytest.fixture
def orders(tmp_path):
    rows = []
    for i in range(1, 121):
        rows.append(
            [
                i,
                COUNTRIES[i % 4],  # Four labels, 30 times each.
                ["open", "closed"][i % 2],
                f"{CANARY}{i}",  # Not declared: must never appear.
                "x_rare" if i <= MIN_LABEL_COUNT - 1 else "common",  # A label seen too rarely to list.
                f"person{i}@example.com",  # Declared by mistake below: must never be listed.
            ]
        )
    return write(tmp_path / "orders.csv", ["id", "country", "status", "note", "tier", "contact"], rows)


def test_declared_columns_list_their_labels_sorted_and_uncounted(orders):
    schema = extract(str(orders), policy=public("country", "status"))
    fields = fields_of(schema)
    assert fields["country"].values == ("Canada", "FR", "US", "us")
    assert fields["status"].values == ("closed", "open")
    text = render(schema, "text")
    assert 'country: string  -- values: "Canada" | "FR" | "US" | "us"' in text
    assert "30" not in text.split("country:")[1].split("\n")[0], "a count must never be shown"
    document = json.loads(render(schema, "json-schema"))
    assert document["items"]["properties"]["country"]["x-public-values"] == ["Canada", "FR", "US", "us"]
    assert any("declared them public: country, status" in note for note in schema.notes)


def test_nothing_else_is_shown_and_the_default_changes_nothing(orders):
    plain = extract(str(orders))
    assert not any(field.values for field in fields_of(plain).values())
    assert CANARY not in "\n".join(render(plain, name) for name in RENDERERS)
    declared = extract(str(orders), policy=public("country"))
    everything = "\n".join(render(declared, name) for name in RENDERERS) + "\n".join(declared.notes)
    assert CANARY not in everything and "person1@example.com" not in everything and "x_rare" not in everything
    assert not fields_of(declared)["status"].values  # Declared nothing, listed nothing.


def test_a_label_seen_too_rarely_is_left_out_and_the_note_says_so_without_saying_which(orders):
    schema = extract(str(orders), policy=public("tier"))
    assert fields_of(schema)["tier"].values == ("common",)
    notes = "\n".join(schema.notes)
    assert "left out of the list" in notes and "x_rare" not in notes and "x_rare" not in render(schema, "text")


def test_a_column_with_too_many_labels_is_not_listed_at_all(tmp_path):
    rows = [[i, f"label{i % (MAX_LISTED_LABELS + 5)}"] for i in range(400)]
    schema = extract(str(write(tmp_path / "t.csv", ["id", "code"], rows)), policy=public("code"))
    assert not fields_of(schema)["code"].values
    assert any("values not listed" in note and f"more than {MAX_LISTED_LABELS}" in note for note in schema.notes)
    assert "label1" not in render(schema, "text")


def test_values_that_look_like_data_are_never_listed_even_when_declared(orders, tmp_path):
    schema = extract(str(orders), policy=public("contact"))
    assert not fields_of(schema)["contact"].values
    assert any("not plain labels" in note for note in schema.notes)
    assert "example.com" not in render(schema, "text")

    mixed = []
    for i in range(1, 91):
        mixed.append([i, ["gold", "silver", "bronze"][i % 3]])
    mixed += [[100 + i, "jane.doe@example.com"] for i in range(5)] + [[200 + i, "+1 415 555 0132"] for i in range(5)]
    mixed += [[300 + i, "4111111111111111"] for i in range(5)] + [[400 + i, "550e8400-e29b-41d4-a716-446655440000"] for i in range(5)]
    schema = extract(str(write(tmp_path / "m.csv", ["id", "tier"], mixed)), policy=public("tier"))
    assert fields_of(schema)["tier"].values == ("bronze", "gold", "silver")
    shown = render(schema, "text") + "\n".join(schema.notes)
    for secret in ("jane.doe", "415 555", "4111", "550e8400"):
        assert secret not in shown, secret


def test_characters_that_could_break_an_output_are_never_listed(tmp_path):
    path = tmp_path / "t.jsonl"
    odd = ['a*/b', 'say "hi"', "back\\slash", "line\nbreak", "<script>", "semi;colon", "x" * 60]
    records = [{"id": i, "kind": value} for i, value in enumerate(odd * 5)] + [{"id": 100 + i, "kind": "plain"} for i in range(40)]
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    schema = extract(str(path), policy=public("kind"))
    assert fields_of(schema)["kind"].values == ("plain",)
    shown = "\n".join(render(schema, name) for name in RENDERERS)
    for fragment in ("a*/b", "say", "slash", "<script>", "semi;colon", "x" * 60):
        assert fragment not in shown, fragment


def test_a_file_read_only_in_part_lists_nothing(orders):
    schema = extract(str(orders), policy=public("country", max_rows=50))
    assert not fields_of(schema)["country"].values
    assert any("only part of the file was read" in note for note in schema.notes)


@pytest.fixture
def two_tables(tmp_path):
    for name in ("customers", "orders"):
        rows = [[i, ["US", "FR", "DE"][i % 3], ["open", "closed"][i % 2]] for i in range(1, 61)]
        write(tmp_path / f"{name}.csv", ["id", "country", "status"], rows)
    return tmp_path


def listed(schema):
    return {e.name: sorted(n for n, f in getattr(e.node, "items", e.node).fields.items() if f.values) for e in schema.entities}


def test_a_plain_name_applies_to_every_table_that_has_the_column(two_tables):
    schema = extract(str(two_tables), policy=public("country"))
    assert listed(schema) == {"customers.csv": ["country"], "orders.csv": ["country"]}


def test_table_dot_column_limits_it_to_one_table(two_tables):
    for entry in ("customers.country", "customers.csv.country"):
        schema = extract(str(two_tables), policy=public(entry))
        assert listed(schema) == {"customers.csv": ["country"], "orders.csv": []}, entry
        note = next(n for n in schema.notes if "declared them public" in n)
        assert "country (in customers.csv)" in note and "orders.csv" not in note


def test_a_qualified_name_works_for_a_single_file_by_its_own_name(two_tables):
    (only,) = extract(str(two_tables / "orders.csv"), policy=public("orders.status")).entities
    assert getattr(only.node, "items", only.node).fields["status"].values == ("closed", "open")
    wrong = extract(str(two_tables / "orders.csv"), policy=public("customers.status"))
    (only,) = wrong.entities
    assert not any(f.values for f in getattr(only.node, "items", only.node).fields.values())
    assert any("not found as a column: customers.status" in n for n in wrong.notes)


def test_a_qualified_name_can_name_a_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    first = book.active
    first.title = "people"
    second = book.create_sheet("orders")
    for sheet in (first, second):
        sheet.append(["id", "country"])
        for i in range(1, 61):
            sheet.append([i, ["US", "FR", "DE"][i % 3]])
    path = tmp_path / "book.xlsx"
    book.save(path)
    schema = extract(str(path), policy=public("orders.country"))
    assert [e.name for e in schema.entities if any(f.values for f in getattr(e.node, "items", e.node).fields.values())] == [
        next(e.name for e in schema.entities if e.name and e.name.endswith("orders"))
    ]


def test_a_declared_name_that_matches_nothing_is_reported(orders):
    schema = extract(str(orders), policy=public("country", "contry"))
    assert any("not found as a column: contry" in note for note in schema.notes)


def test_a_source_that_cant_list_values_says_so(tmp_path):
    import sqlite3

    path = tmp_path / "t.db"
    connection = sqlite3.connect(path)
    connection.execute("create table t (country text)")
    connection.executemany("insert into t values (?)", [("US",)] * 30)
    connection.commit()
    connection.close()
    schema = extract(str(path), policy=public("country"))
    assert any("can't give them without reading rows" in note for note in schema.notes)
    assert "US" not in render(schema, "text").replace("country", "")


def test_synthetic_rows_use_the_listed_labels_so_code_meets_the_real_spellings(orders):
    schema = extract(str(orders), policy=public("country", "status"))
    table = synthesize(schema, rows=80)[0]
    seen = {row["country"] for row in table.rows}
    assert seen <= {"Canada", "FR", "US", "us"} and len(seen) >= 3
    assert {row["status"] for row in table.rows} <= {"closed", "open"}
    assert CANARY not in render_table(table, "csv")


def test_every_renderer_still_works_and_the_comment_formats_carry_the_labels(orders):
    schema = extract(str(orders), policy=public("country"))
    for name in RENDERERS:
        output = render(schema, name)
        assert output
    for name in ("text", "typescript", "pydantic", "sql"):
        if name in RENDERERS:
            assert '"Canada" | "FR" | "US" | "us"' in render(schema, name), name


def test_the_command_line_flag_and_the_config_setting(orders, tmp_path, capsys, monkeypatch):
    assert main(["schema", str(orders), "--format", "text", "--public-column", "country"]) == 0
    out = capsys.readouterr().out
    assert '"Canada" | "FR"' in out and "closed" not in out

    (tmp_path / "datanyx.toml").write_text('[schema]\npublic_columns = ["status"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["schema", str(orders), "--format", "text"]) == 0
    out = capsys.readouterr().out
    assert '"closed" | "open"' in out and "Canada" not in out


def test_only_the_owner_of_the_mcp_server_can_make_a_column_public(orders):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    def described(server):
        result = asyncio.run(server.call_tool("describe_data", {"target": str(orders)}))
        return "\n".join(getattr(block, "text", "") for block in result.content) if hasattr(result, "content") else str(result)

    assert "Canada" not in described(create_server([orders.parent]))
    shown = described(create_server([orders.parent], public_columns=["country"]))
    assert "Canada" in shown and CANARY not in shown
    tools = asyncio.run(create_server([orders.parent], public_columns=["country"]).list_tools())
    for tool in tools:  # No tool takes a list of columns to reveal: the agent can't widen it.
        parameters = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
        assert parameters, "the tool should describe its arguments"
        assert "public" not in json.dumps(parameters).lower(), tool.name


def test_a_declared_column_that_holds_a_distinctive_word_shows_it_because_the_owner_chose_that(tmp_path):
    rows = [[i, CANARY] for i in range(1, 31)]
    schema = extract(str(write(tmp_path / "t.csv", ["id", "label"], rows)), policy=public("label"))
    assert CANARY in render(schema, "text")  # The control: the owner declared it, so it is shown, and only because of that.
    assert CANARY not in render(extract(str(tmp_path / "t.csv")), "text")
