"""Declared uniqueness, views, and choosing which tables to describe. All data is synthetic."""

import asyncio
import json
import sqlite3

import pytest

from datanyx import KeyPolicy, extract, render, synthesize
from datanyx.cli import main
from datanyx.schema import Object


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table users (id integer primary key, email text unique, handle text, tenant integer, code text);
        create unique index ux_handle on users(handle);
        create unique index ux_tenant_code on users(tenant, code);
        create unique index ux_partial on users(code) where tenant = 1;
        create table orders (id integer primary key, user_id integer references users(id), note text);
        create table audit_log (id integer);
        create view recent_orders as select id, user_id from orders;
        """
    )
    for i in range(5):
        connection.execute(f"create table part_{i} (a integer)")
    connection.commit()
    connection.close()
    return str(path)


def entity(schema, name):
    return next(e for e in schema.entities if e.name == name)


def fields(e):
    return e.node.fields if isinstance(e.node, Object) else {}


def test_declared_unique_columns_are_flagged_as_facts(database):
    users = entity(extract(database), "users")
    flagged = {name for name, f in fields(users).items() if f.unique}
    assert flagged == {"email", "handle"}  # The primary key is shown as one already.
    assert all(f.unique_declared for f in fields(users).values() if f.unique)
    assert users.unique_keys == (("tenant", "code"),)  # A partial index makes only some rows unique, so it is left out.


def test_renderers_say_declared_not_inferred(database):
    schema = extract(database)
    text = render(schema, "text")
    assert "TEXT, unique" in text and "inferred" not in text
    assert "unique (tenant, code)" in text
    sql = render(schema, "sql")
    assert '"email" TEXT UNIQUE' in sql and 'UNIQUE ("tenant", "code")' in sql
    document = json.loads(render(schema, "json-schema"))["$defs"]["users"]
    assert document["properties"]["email"]["x-unique-declared"] is True
    assert document["x-unique-keys"] == [["tenant", "code"]]
    assert "unique" in render(schema, "typescript") and "unique" in render(schema, "pydantic")


def test_a_unique_key_with_a_hidden_column_is_left_out(tmp_path):
    path = tmp_path / "k.db"
    connection = sqlite3.connect(path)
    connection.execute('create table t (id integer primary key, a text, "x y" text, unique (a, "x y"))')
    connection.execute('create table u (id integer primary key, "x y" text unique)')
    connection.commit()
    connection.close()
    schema = extract(str(path))
    assert entity(schema, "t").unique_keys == ()
    assert not any(f.unique for f in fields(entity(schema, "u")).values())


def test_synthetic_rows_keep_declared_unique_columns_unique(database):
    schema = extract(database, policy=KeyPolicy(include=("users",)))
    table = synthesize(schema, rows=40)[0]
    emails = [row["email"] for row in table.rows]
    assert len(set(emails)) == len(emails)


def test_views_are_opt_in_and_marked(database):
    assert "recent_orders" not in render(extract(database), "text")
    schema = extract(database, policy=KeyPolicy(views=True))
    view = entity(schema, "recent_orders")
    assert view.view and not view.primary_key
    assert "view" in render(schema, "text") and json.loads(render(schema, "json-schema"))["$defs"]["recent_orders"]["x-view"] is True
    assert "a view in the source" in render(schema, "sql")


def test_include_exclude_and_the_cap_say_what_was_left_out(database):
    schema = extract(database, policy=KeyPolicy(include=("part_*",), exclude=("part_3",)))
    assert [e.name for e in schema.entities] == ["part_0", "part_1", "part_2", "part_4"]
    assert any("left out" in note for note in schema.notes)

    capped = extract(database, policy=KeyPolicy(max_tables=3))
    assert len(capped.entities) == 3
    assert any("showing 3 of" in note for note in capped.notes)

    nothing = extract(database, policy=KeyPolicy(include=("nope_*",)))
    assert nothing.entities == () and any("no table matches" in note for note in nothing.notes)


def test_foreign_keys_to_tables_outside_the_selection_are_dropped(database):
    schema = extract(database, policy=KeyPolicy(include=("orders",)))
    assert entity(schema, "orders").foreign_keys == ()


def test_the_summary_lists_every_table_in_a_line(database):
    schema = extract(database, policy=KeyPolicy(views=True))
    text = render(schema, "summary")
    lines = text.splitlines()
    assert lines[0] == f"{len(schema.entities)} tables"
    assert "orders: 3 columns; primary key (id); references users" in lines
    assert "recent_orders (view): 2 columns" in lines


def test_patterns_do_not_reveal_hidden_names(tmp_path):
    path = tmp_path / "h.db"
    connection = sqlite3.connect(path)
    connection.execute('create table "jane doe" (a integer)')
    connection.execute("create table visible (a integer)")
    connection.commit()
    connection.close()
    probing = extract(str(path), policy=KeyPolicy(include=("jane*",)))
    absent = extract(str(path), policy=KeyPolicy(include=("zzz*",)))
    assert probing.entities == absent.entities == ()
    assert probing.notes == absent.notes  # Nothing tells a hidden name from one that isn't there.


def test_selection_applies_to_files_in_a_folder(tmp_path):
    for name in ("sales_east.csv", "sales_west.csv", "staff.csv"):
        (tmp_path / name).write_text("id,v\n1,x\n", encoding="utf-8")
    schema = extract(str(tmp_path), policy=KeyPolicy(include=("sales_*",), exclude=("*east*",)))
    assert [e.name for e in schema.entities] == ["sales_west.csv"]


def test_command_line_options(database, capsys):
    assert main(["schema", database, "--format", "summary", "--views", "--exclude", "part_*"]) == 0
    captured = capsys.readouterr()
    assert "recent_orders (view)" in captured.out and "part_0" not in captured.out
    assert main(["schema", database, "--format", "text", "--max-tables", "2"]) == 0
    captured = capsys.readouterr()
    assert "showing 2 of" in captured.err


def test_the_mcp_server_caps_and_selects_for_describe_data(database, tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    def call(server, tool, arguments):
        return asyncio.run(server.call_tool(tool, arguments)).content[0].text

    server = create_server([tmp_path], max_tables=3, views=True)
    capped = call(server, "describe_data", {"target": database})
    assert "showing 3 of" in capped
    picked = call(server, "describe_data", {"target": database, "tables": ["part_*"], "exclude": ["part_0"], "limit": 2})
    assert "part_1" in picked and "part_2" in picked and "part_3" not in picked and "showing 2 of 4" in picked
    summary = call(server, "describe_data", {"target": database, "format": "summary"})
    assert "part_4" in summary and "recent_orders (view)" in summary  # Not capped, so every table is listed.
    # Code is checked against every table, however describe_data was limited.
    report = call(server, "check_code", {"code": "select a from part_4", "target": database, "language": "sql"})
    assert "unknown" not in report.lower()
