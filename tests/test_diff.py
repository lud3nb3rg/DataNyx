"""The schema diff says what changed, and which changes could break code written for the old one."""

import json
import sqlite3

import pytest

from datanyx import extract, render
from datanyx.cli import main
from datanyx.diff import diff_schemas, has_breaking, render_diff, snapshot

CANARY = "zq-canary-value"


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def schema_of(tmp_path, data, name="d.json"):
    return json.loads(render(extract(str(write(tmp_path, name, json.dumps(data))))))


def diff(tmp_path, before, after):
    return diff_schemas(schema_of(tmp_path, before, "a.json"), schema_of(tmp_path, after, "b.json"))


def summary(differences):
    return [(d.severity, d.kind, d.path, d.message) for d in differences]


RECORDS = [{"id": 1, "email": "a@x.com", "nick": "x", "age": 3}, {"id": 2, "email": "b@x.com", "nick": "y", "age": 4}]


def with_changes(**changes):
    rows = [dict(row) for row in RECORDS]
    for row in rows:
        for key, value in changes.items():
            if value is None:
                row.pop(key, None)
            else:
                row[key] = value
    return rows


# --- Fields ---------------------------------------------------------------------------------------


def test_identical_schemas_have_no_differences(tmp_path):
    assert diff(tmp_path, RECORDS, RECORDS) == []
    assert render_diff([]) == "No differences."


def test_removed_fields_break_and_added_fields_dont(tmp_path):
    found = summary(diff(tmp_path, RECORDS, with_changes(nick=None, extra=1.5)))
    assert ("breaking", "removed", "$[].nick", "removed") in found
    assert ("compatible", "added", "$[].extra", "added  (number, required)") in found


def test_a_field_becoming_nullable_breaks_and_becoming_non_null_doesnt(tmp_path):
    nulled = [{**RECORDS[0], "age": None}, RECORDS[1]]
    found = summary(diff(tmp_path, RECORDS, nulled))
    assert found == [("breaking", "changed", "$[].age", "integer -> integer | null  (can now be null)")]
    assert summary(diff(tmp_path, nulled, RECORDS)) == [
        ("compatible", "changed", "$[].age", "integer | null -> integer  (can no longer be null)")
    ]


def test_type_widening_breaks_and_narrowing_doesnt(tmp_path):
    widened = with_changes(age=2.5)
    assert summary(diff(tmp_path, RECORDS, widened))[0][:3] == ("breaking", "changed", "$[].age")
    assert summary(diff(tmp_path, widened, RECORDS))[0][:3] == ("compatible", "changed", "$[].age")
    swapped = summary(diff(tmp_path, RECORDS, with_changes(age="old")))
    assert swapped[0][0] == "breaking" and "integer -> string" in swapped[0][3]


def test_formats(tmp_path):
    lost = summary(diff(tmp_path, RECORDS, with_changes(email="not an email")))
    assert lost[0][0] == "breaking" and "format <email> lost" in lost[0][3]
    gained = summary(diff(tmp_path, with_changes(email="not an email"), RECORDS))
    assert gained[0][0] == "compatible" and "now all <email>" in gained[0][3]
    swapped = summary(diff(tmp_path, with_changes(email="2024-01-02"), with_changes(email="2024-01-02T01:02:03Z")))
    assert swapped[0][3] == "format <date> -> <date-time>" and swapped[0][0] == "breaking"


def test_optional_and_required(tmp_path):
    optional = [RECORDS[0], {k: v for k, v in RECORDS[1].items() if k != "nick"}]
    assert summary(diff(tmp_path, RECORDS, optional)) == [("breaking", "changed", "$[].nick", "was always present, now optional")]
    assert summary(diff(tmp_path, optional, RECORDS)) == [("compatible", "changed", "$[].nick", "optional -> always present")]


def test_nested_objects_and_arrays_have_paths(tmp_path):
    before = {"user": {"tags": ["a"], "address": {"zip": "01234"}}}
    after = {"user": {"tags": [1], "address": {}}}
    paths = {d.path for d in diff(tmp_path, before, after)}
    assert paths == {"$.user.tags[]", "$.user.address.zip"}


def test_hidden_map_keys_are_compared_by_their_values(tmp_path):
    def many(value):
        return {"balances": {f"{i:04d}@x.com": value for i in range(40)}}

    found = summary(diff(tmp_path, many(1.5), many("text")))
    assert found[0][0] == "breaking" and found[0][2] == "$.balances.*"


def test_unknown_to_known_is_only_information(tmp_path):
    found = summary(diff(tmp_path, {"items": []}, {"items": [1]}))
    assert found[0][0] == "info" and found[0][2] == "$.items[]"


# --- Entities and databases --------------------------------------------------------------------------------


def database(tmp_path, name, ddl):
    path = tmp_path / name
    connection = sqlite3.connect(path)
    connection.executescript(ddl)
    connection.close()
    return str(path)


def test_tables_added_removed_and_columns_changed(tmp_path):
    old = database(tmp_path, "old.db", "create table users (id integer primary key, email text, age integer); create table logs (id integer);")
    new = database(
        tmp_path,
        "new.db",
        "create table users (id integer primary key, email text, age bigint, joined text); create table posts (id integer primary key);",
    )
    found = {(d.severity, d.kind, d.path): d.message for d in diff_schemas(snapshot(old), snapshot(new))}
    assert found[("breaking", "removed", "logs")] == "removed"
    assert found[("compatible", "added", "posts")] == "added"
    assert found[("info", "changed", "users.age")].startswith("database type INTEGER -> BIGINT")
    assert ("compatible", "added", "users.joined") in found


def test_key_changes_are_information(tmp_path):
    old = database(tmp_path, "o.db", "create table a (id integer primary key); create table b (id integer primary key, a_id integer references a(id));")
    new = database(tmp_path, "n.db", "create table a (id integer); create table b (id integer primary key, a_id integer);")
    found = {d.message for d in diff_schemas(snapshot(old), snapshot(new)) if d.severity == "info" and "key" in d.message}
    assert any("primary key changed: (id) -> none" in m for m in found)
    assert any("foreign keys changed" in m for m in found)


# --- Saved schemas and live inputs ----------------------------------------------------------------------------


def test_a_saved_schema_is_read_as_it_is_and_data_files_are_not_mistaken_for_one(tmp_path):
    data = write(tmp_path, "feed.json", json.dumps(RECORDS))
    saved = write(tmp_path, "schema.json", render(extract(str(data))))
    assert snapshot(str(saved)) == json.loads(saved.read_text(encoding="utf-8"))
    live = snapshot(str(data))
    assert live == snapshot(str(saved))  # A data file is described, not returned as it is.
    assert "type" in live and "id" not in live


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("a.csv", "id,email,when\n1,a@x.com,2024-01-02\n2,,2024-01-03\n"),
        ("a.json", json.dumps({"users": [{"id": 1, "tags": ["a"], "meta": {"k": 1.5}}], "total": 3})),
        ("a.yaml", "a: 1\nb: [x, y]\nc:\n  d: 2024-01-02\n"),
        ("a.xml", '<r><item id="1"><n>5</n></item><item id="2"/></r>'),
    ],
)
def test_a_snapshot_of_an_input_compared_with_that_input_has_no_differences(tmp_path, name, text):
    path = write(tmp_path, name, text)
    saved = write(tmp_path, "snap.json", render(extract(str(path))))
    assert diff_schemas(snapshot(str(saved)), snapshot(str(path))) == []


def test_database_snapshot_round_trip(tmp_path):
    db = database(tmp_path, "s.db", "create table t (id integer primary key, n text not null, f integer references t(id));")
    saved = write(tmp_path, "snap.json", render(extract(db)))
    assert diff_schemas(snapshot(str(saved)), snapshot(db)) == []


def test_output_has_names_and_types_but_no_values(tmp_path):
    rows = [{"name": f"{CANARY} Person", "n": 1}, {"name": "x", "n": 2}]
    out = render_diff(diff(tmp_path, rows, [{"name": 5, "n": 1}, {"name": 6, "n": 2}]))
    assert CANARY not in out and "$[].name" in out


# --- Command line -------------------------------------------------------------------------------------------------


def test_cli_exit_codes_and_formats(tmp_path, capsys):
    old = write(tmp_path, "old.csv", "id,email,nick\n1,a@x.com,x\n2,b@x.com,y\n")
    same = write(tmp_path, "same.csv", old.read_text(encoding="utf-8"))
    new = write(tmp_path, "new.csv", "id,email,score\n1,a@x.com,1.5\n2,b@x.com,2.5\n")
    assert main(["diff", str(old), str(same)]) == 0 and "No differences." in capsys.readouterr().out
    assert main(["diff", str(old), str(new)]) == 1
    out = capsys.readouterr().out
    assert "1 breaking, 1 compatible" in out and "- $[].nick: removed" in out and "+ $[].score: added" in out
    assert main(["diff", str(old), str(new), "--fail-on", "never"]) == 0
    capsys.readouterr()
    assert main(["diff", str(same), str(write(tmp_path, "extra.csv", "id,email,nick,more\n1,a@x.com,x,1\n")), "--fail-on", "any"]) == 1
    capsys.readouterr()
    assert main(["diff", str(old), str(new), "--format", "json", "--fail-on", "never"]) == 0
    assert {d["severity"] for d in json.loads(capsys.readouterr().out)} == {"breaking", "compatible"}


def test_cli_compares_a_saved_schema_with_the_feed_as_it_is_now(tmp_path, capsys):
    feed = write(tmp_path, "feed.csv", "id,email\n1,a@x.com\n")
    snapshot_file = tmp_path / "snapshot.json"
    assert main(["schema", str(feed), "-o", str(snapshot_file)]) == 0
    capsys.readouterr()
    assert main(["diff", str(snapshot_file), str(feed)]) == 0
    feed.write_text("id\n1\n", encoding="utf-8")
    assert main(["diff", str(snapshot_file), str(feed)]) == 1
    assert "removed" in capsys.readouterr().out


def test_has_breaking(tmp_path):
    assert has_breaking(diff(tmp_path, RECORDS, with_changes(nick=None)))
    assert not has_breaking(diff(tmp_path, RECORDS, with_changes(extra=1)))


def test_mcp_schema_diff_tool(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from datanyx.mcp_server import create_server

    old = write(tmp_path, "old.csv", "id,nick\n1,x\n")
    new = write(tmp_path, "new.csv", "id\n1\n")
    server = create_server([tmp_path])

    async def call(arguments):
        return await server.call_tool("schema_diff", arguments)

    text = asyncio.run(call({"old": str(old), "new": str(new)})).content[0].text
    assert "1 breaking" in text and "$[].nick: removed" in text
    with pytest.raises(Exception, match="outside"):
        asyncio.run(call({"old": str(old), "new": str(tmp_path.parent / "x.csv")}))
