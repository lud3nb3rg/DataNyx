"""Synthetic data must fit the schema it came from, hold together, and contain nothing real."""

import json
import sqlite3

import pytest

from datanyx import SourceError, extract, render, synthesize
from datanyx.cli import main
from datanyx.synth import render_table

CANARY = "zq-canary-value"

RECORDS = [
    {
        "id": 1,
        "email": "a@x.com",
        "joined": "2024-01-02",
        "score": 1.5,
        "active": True,
        "tags": ["a"],
        "nick": "x",
        "note": "hello",
        "city": "Lisbon",
    },
    {"id": 2, "email": "b@x.com", "joined": "2024-02-03", "score": 2, "active": False, "tags": [], "note": None, "city": "Cork"},
    {"id": 3, "email": "c@x.com", "joined": "2024-03-04", "score": 3.25, "active": True, "tags": ["b", "c"], "nick": "y", "note": "n", "city": "Lyon"},
]


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def text_schema(path):
    return render(extract(str(path)), "text")


# --- Shape: the synthetic data reads back as the same schema ------------------


def test_json_round_trip(tmp_path):
    original = write(tmp_path, "d.json", json.dumps(RECORDS))
    tables = synthesize(extract(str(original)), rows=200)
    fake = write(tmp_path, "fake.json", render_table(tables[0], "json"))
    assert text_schema(fake) == text_schema(original)


def test_jsonl_round_trip(tmp_path):
    original = write(tmp_path, "d.jsonl", "\n".join(json.dumps(r) for r in RECORDS))
    tables = synthesize(extract(str(original)), rows=200)
    fake = write(tmp_path, "fake.jsonl", render_table(tables[0], "jsonl"))
    assert text_schema(fake) == text_schema(original)


def test_csv_round_trip(tmp_path):
    original = write(
        tmp_path,
        "d.csv",
        "id,email,joined,score,active,note,zip\n1,a@x.com,2024-01-02,1.5,true,hi,02139\n2,b@x.com,2024-02-03,2.5,false,,01234\n",
    )
    tables = synthesize(extract(str(original)), rows=200)
    fake = write(tmp_path, "fake.csv", render_table(tables[0], "csv"))
    assert text_schema(fake) == text_schema(original)


def test_document_root_gives_one_document(tmp_path):
    original = write(tmp_path, "d.json", json.dumps({"a": 1, "b": {"c": "x", "d": [1, 2]}}))
    tables = synthesize(extract(str(original)), rows=50)
    assert tables[0].document and len(tables[0].rows) == 1
    fake = write(tmp_path, "fake.json", render_table(tables[0], "json"))
    assert text_schema(fake) == text_schema(original)


# --- Databases: keys, references and types hold together -----------------------


@pytest.fixture
def shop(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table users (id integer primary key, email text not null, age integer);
        create table posts (
            id integer primary key,
            author integer references users(id),
            title text not null
        );
        create table post_tags (
            post_id integer not null references posts(id),
            tag_id integer not null,
            primary key (post_id, tag_id)
        );
        """
    )
    connection.close()
    return path


def test_sqlite_output_keeps_references_and_keys(shop, tmp_path):
    out = tmp_path / "fake.db"
    assert main(["synth", str(shop), "--rows", "60", "--format", "sqlite", "-o", str(out)]) == 0
    connection = sqlite3.connect(out)
    assert connection.execute("pragma foreign_key_check").fetchall() == []
    assert connection.execute("select count(*) from users").fetchone() == (60,)
    assert connection.execute("select count(*) from posts where title is null").fetchone() == (0,)
    assert connection.execute("select count(distinct id) from users").fetchone() == (60,)
    # Posts point at users that exist, and some are left without an author.
    assert connection.execute("select count(*) from posts where author is not null").fetchone()[0] > 0
    assert connection.execute("select count(*) from post_tags").fetchone()[0] > 0
    connection.close()


def test_synthetic_database_has_the_same_schema(shop, tmp_path):
    out = tmp_path / "fake.db"
    main(["synth", str(shop), "--rows", "10", "--format", "sqlite", "-o", str(out)])
    assert text_schema(out) == text_schema(shop)


def test_foreign_keys_follow_dependency_order(shop):
    tables = {t.name: t for t in synthesize(extract(str(shop)), rows=30)}
    user_ids = {row["id"] for row in tables["users"].rows}
    assert {row["author"] for row in tables["posts"].rows if row["author"] is not None} <= user_ids
    post_ids = {row["id"] for row in tables["posts"].rows}
    assert {row["post_id"] for row in tables["post_tags"].rows} <= post_ids
    keys = [(row["post_id"], row["tag_id"]) for row in tables["post_tags"].rows]
    assert len(keys) == len(set(keys))


# --- Behaviour ----------------------------------------------------------------


def test_same_seed_same_output_different_seed_different(tmp_path):
    schema = extract(str(write(tmp_path, "d.json", json.dumps(RECORDS))))
    first = render_table(synthesize(schema, 20, seed=1)[0], "jsonl")
    assert first == render_table(synthesize(schema, 20, seed=1)[0], "jsonl")
    assert first != render_table(synthesize(schema, 20, seed=2)[0], "jsonl")


def test_field_names_give_realistic_values(tmp_path):
    schema = extract(str(write(tmp_path, "d.json", json.dumps(RECORDS))))
    cities = {row["city"] for row in synthesize(schema, 100)[0].rows}
    assert cities <= {"Lisbon", "Osaka", "Leeds", "Austin", "Porto", "Lyon", "Turin", "Quito", "Bergen", "Kyoto", "Cork"}


def test_zero_rows_and_bad_row_counts(tmp_path):
    schema = extract(str(write(tmp_path, "d.json", json.dumps(RECORDS))))
    assert synthesize(schema, 0)[0].rows == []
    with pytest.raises(ValueError):
        synthesize(schema, -1)


# --- Nothing real gets through --------------------------------------------------


def test_values_and_hidden_keys_never_appear(tmp_path):
    data = [
        {"name": f"{CANARY} Person", "jane.doe@example.com": f"{CANARY}", "n": 918273645, "tags": [CANARY]}
        for _ in range(8)
    ]
    schema = extract(str(write(tmp_path, "d.json", json.dumps(data))))
    for output_format in ("json", "jsonl", "csv"):
        output = "".join(render_table(table, output_format) for table in synthesize(schema, 50))
        assert CANARY not in output and "jane.doe" not in output and "918273645" not in output


def test_generated_values_use_reserved_example_ranges(tmp_path):
    schema = extract(
        str(write(tmp_path, "d.json", json.dumps([{"e": "a@x.com", "i": "10.0.0.1", "u": "https://x.org/a"}] * 3)))
    )
    rows = synthesize(schema, 50)[0].rows
    assert all(row["e"].endswith("@example.com") and row["i"].startswith("192.0.2.") for row in rows)
    assert all(row["u"].startswith("https://example.com/") for row in rows)


# --- Command line ---------------------------------------------------------------


def test_cli_writes_csv_to_stdout(tmp_path, capsys):
    path = write(tmp_path, "d.csv", "id,email\n1,a@x.com\n")
    assert main(["synth", str(path), "--rows", "3", "--format", "csv"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "id,email" and len(lines) == 4


def test_cli_folder_gets_one_file_per_table(shop, tmp_path):
    out = tmp_path / "fake"
    assert main(["synth", str(shop), "--rows", "5", "-o", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["post_tags.json", "posts.json", "users.json"]
    assert len(json.loads((out / "users.json").read_text(encoding="utf-8"))) == 5


def test_cli_refuses_to_overwrite_and_to_use_the_input(tmp_path, capsys):
    path = write(tmp_path, "d.csv", "id\n1\n")
    target = write(tmp_path, "out.csv", "keep me")
    assert main(["synth", str(path), "--format", "csv", "-o", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "keep me"
    assert main(["synth", str(path), "--format", "csv", "-o", str(path)]) == 1
    assert path.read_text(encoding="utf-8") == "id\n1\n"
    assert "error:" in capsys.readouterr().err


def test_cli_input_errors(shop, tmp_path, capsys):
    assert main(["synth", str(shop)]) == 1  # Several tables, nowhere to put them.
    assert main(["synth", str(shop), "--format", "sqlite"]) == 1
    assert main(["synth", str(write(tmp_path, "d.csv", "a\n1\n")), "--rows", "-5"]) == 1
    assert capsys.readouterr().err.count("error:") == 3


def test_hidden_columns_are_noted_for_csv(tmp_path, capsys):
    path = write(tmp_path, "d.json", json.dumps([{"id": i, "a@x.com": 1} for i in range(8)]))
    main(["synth", str(path), "--format", "csv", "--rows", "2"])
    assert "left out" in capsys.readouterr().err
    main(["synth", str(path), "--format", "json", "--rows", "2"])
    assert "left out" not in capsys.readouterr().err


# --- MCP tool ---------------------------------------------------------------------


def test_mcp_synthetic_sample(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from datanyx.mcp_server import create_server

    path = write(tmp_path, "d.json", json.dumps([{"id": 1, "name": f"{CANARY}"}] * 3))
    server = create_server([tmp_path])

    async def call(arguments):
        return await server.call_tool("synthetic_sample", arguments)

    text = asyncio.run(call({"target": str(path), "rows": 4})).content[0].text
    assert len(text.strip().splitlines()) == 4 and CANARY not in text
    with pytest.raises(Exception, match="rows"):
        asyncio.run(call({"target": str(path), "rows": 100000}))


def test_source_errors_are_still_clean(tmp_path):
    with pytest.raises(SourceError):
        synthesize(extract(str(write(tmp_path, "d.json", "{bad"))))


def test_csv_handles_a_list_that_mixes_records_and_nulls(tmp_path):
    # Found by the fuzz test: rows that aren't objects used to crash the CSV writer.
    schema = extract(str(write(tmp_path, "d.json", json.dumps([None, {"id": 1}, None, {"id": 2}]))))
    text = render_table(synthesize(schema, 10)[0], "csv")
    assert text.splitlines()[0] == "id,value"


# --- Messy rows -------------------------------------------------------------------


def mixed(tmp_path):
    rows = "".join(f"{i},{'n/a' if i % 7 == 0 else i * 1.5},note {CANARY} {i},{'' if i % 5 == 0 else 'x'}\n" for i in range(1, 60))
    return str(write(tmp_path, "m.csv", "id,amount,notes,flag\n" + rows))


def test_messy_is_off_unless_asked_for_and_repeatable(tmp_path):
    schema = extract(mixed(tmp_path))
    plain = render_table(synthesize(schema, 200)[0], "csv")
    assert plain == render_table(synthesize(schema, 200, messy=False)[0], "csv")
    messy = render_table(synthesize(schema, 200, messy=True)[0], "csv")
    assert messy != plain and messy == render_table(synthesize(schema, 200, messy=True)[0], "csv")


def test_messy_rows_have_the_flaws_real_data_has_and_nothing_real(tmp_path):
    table = synthesize(extract(mixed(tmp_path)), 400, messy=True)[0]
    amounts = [row["amount"] for row in table.rows]
    texts = [value for value in amounts if isinstance(value, str)]
    assert any(value in ("n/a", "N/A", "NA", "-", "null", "NULL", "none", "unknown", "?", "") for value in texts)
    assert any(any(char.isdigit() for char in value) for value in texts)  # A number written as text.
    assert any(isinstance(value, float) and value < 0 for value in amounts)
    notes = [row["notes"] for row in table.rows if isinstance(row.get("notes"), str)]
    assert any(note != note.strip() for note in notes) and "" in notes
    assert len({json.dumps(row, sort_keys=True) for row in table.rows}) < len(table.rows)  # Repeated rows.
    assert CANARY not in render_table(table, "csv")


def test_messy_rows_still_read_back_as_the_same_columns(tmp_path):
    path = mixed(tmp_path)
    fake = str(write(tmp_path, "fake.csv", render_table(synthesize(extract(path), 300, messy=True)[0], "csv")))
    assert "amount: string | number" in render(extract(fake), "text") and "notes: string" in render(extract(fake), "text")


def test_messy_keeps_keys_and_references_whole(shop, tmp_path):
    out = tmp_path / "fake.db"
    assert main(["synth", str(shop), "--rows", "80", "--messy", "--format", "sqlite", "-o", str(out)]) == 0
    connection = sqlite3.connect(out)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_mcp_synthetic_sample_can_be_messy(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from datanyx.mcp_server import create_server

    path = mixed(tmp_path)
    server = create_server([tmp_path])

    async def call(arguments):
        return await server.call_tool("synthetic_sample", arguments)

    plain = asyncio.run(call({"target": str(path), "rows": 200})).content[0].text
    messy = asyncio.run(call({"target": str(path), "rows": 200, "messy": True})).content[0].text
    assert messy != plain and CANARY not in messy and '"amount": "' in messy
