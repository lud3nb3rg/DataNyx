"""SQLite source. All data is synthetic, with CANARY planted wherever data could hide."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from datanyx import KeyPolicy, SourceError, extract, render
from datanyx.cli import main
from datanyx.renderers import RENDERERS
from datanyx.sources.sql_source import clean_native_type, connect_sqlite_catalog_only

CANARY = "CANARY"

SHOP = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) NOT NULL DEFAULT 'CANARY@example.com',
    status "ENUM('CANARY_ENUM')" CHECK (status <> 'CANARY_CHECK'),
    active BOOLEAN,
    joined DATETIME,
    photo BLOB,
    balance DECIMAL(10, 2),
    anything
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    note TEXT
);
CREATE TABLE order_items (
    order_id INTEGER REFERENCES orders,
    line INTEGER,
    qty INT NOT NULL,
    PRIMARY KEY (order_id, line)
);
CREATE INDEX idx_note ON orders (note) WHERE note <> 'CANARY_INDEX';
CREATE VIEW v AS SELECT 'CANARY_VIEW' AS c;
CREATE TRIGGER t AFTER INSERT ON users WHEN new.email = 'CANARY_TRIGGER' BEGIN SELECT 1; END;
INSERT INTO users (id, email, status) VALUES (918273, 'CANARY.row@example.com', 'CANARY_ROW');
INSERT INTO orders VALUES (1, 918273, 'CANARY note');
"""


def make_db(tmp_path, script=SHOP, name="shop.db"):
    path = tmp_path / name
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(script)
    return str(path)


def json_schema(path, policy=None):
    return json.loads(render(extract(path, policy=policy)))


def all_outputs(path, policy=None):
    schema = extract(path, policy=policy)
    return "\n".join([render(schema, name) for name in RENDERERS] + list(schema.notes))


def test_nothing_but_structure_reaches_the_output(tmp_path):
    output = all_outputs(make_db(tmp_path))
    assert CANARY not in output
    assert "918273" not in output


def test_filename_is_not_revealed(tmp_path):
    assert CANARY not in all_outputs(make_db(tmp_path, name=f"{CANARY}.db"))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT email FROM users",
        "SELECT * FROM users",
        "SELECT count(*) FROM users",
        "SELECT c FROM v",
        "UPDATE users SET email = 'x'",
        "DELETE FROM users",
        "DROP TABLE users",
        "CREATE TABLE stolen AS SELECT * FROM users",
    ],
)
def test_catalog_connection_refuses(tmp_path, statement):
    with closing(connect_sqlite_catalog_only(Path(make_db(tmp_path)))) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement).fetchall()


def test_database_file_is_not_modified(tmp_path):
    path = make_db(tmp_path)
    before = open(path, "rb").read()
    extract(path)
    assert open(path, "rb").read() == before


def test_missing_file_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(SourceError, match="No such file"):
        extract(str(path))
    assert not path.exists()


def test_non_database_file_fails_without_quoting_it(tmp_path, capsys):
    path = tmp_path / "fake.db"
    path.write_text(f"{CANARY} is not a database " * 100, encoding="utf-8")
    assert main(["schema", str(path)]) == 1
    captured = capsys.readouterr()
    assert "could not read the database" in captured.err
    assert CANARY not in captured.out + captured.err


def test_column_types_and_nullability(tmp_path):
    props = json_schema(make_db(tmp_path))["$defs"]["users"]["properties"]
    assert props["id"] == {"type": "integer", "x-native-type": "INTEGER"}
    assert props["email"] == {"type": "string", "x-native-type": "VARCHAR(255)"}
    # SQLAlchemy reports SQLite's affinity for a type it doesn't recognise.
    assert props["status"] == {"type": ["number", "null"], "x-native-type": "NUMERIC"}
    assert props["active"] == {"type": ["boolean", "null"], "x-native-type": "BOOLEAN"}
    assert props["joined"] == {"x-native-type": "DATETIME"}
    assert props["photo"]["anyOf"] == [{"type": "string", "format": "binary"}, {"type": "null"}]
    assert props["balance"] == {"type": ["number", "null"], "x-native-type": "DECIMAL(10, 2)"}
    assert props["anything"] == {}


def test_floating_point_columns_are_numbers(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE t (a REAL NOT NULL, b DOUBLE NOT NULL, c FLOAT NOT NULL);")
    props = json_schema(path)["$defs"]["t"]["properties"]
    assert {name: prop["type"] for name, prop in props.items()} == {
        "a": "number",
        "b": "number",
        "c": "number",
    }


def test_primary_and_foreign_keys(tmp_path):
    defs = json_schema(make_db(tmp_path))["$defs"]
    assert defs["users"]["x-primary-key"] == ["id"]
    assert defs["orders"]["x-foreign-keys"] == [
        {"columns": ["user_id"], "references": {"table": "users", "columns": ["id"]}}
    ]
    assert defs["order_items"]["x-primary-key"] == ["order_id", "line"]
    # REFERENCES with no column resolves to the target's primary key.
    assert defs["order_items"]["x-foreign-keys"] == [
        {"columns": ["order_id"], "references": {"table": "orders", "columns": ["id"]}}
    ]


def test_composite_foreign_key(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE parent (a INT, b INT, PRIMARY KEY (a, b));
        CREATE TABLE child (x INT, y INT, FOREIGN KEY (x, y) REFERENCES parent (a, b));
        """,
    )
    assert json_schema(path)["$defs"]["child"]["x-foreign-keys"] == [
        {"columns": ["x", "y"], "references": {"table": "parent", "columns": ["a", "b"]}}
    ]


def test_foreign_key_names_match_case_insensitively(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE Users (ID INTEGER PRIMARY KEY);
        CREATE TABLE posts (author INT REFERENCES users (id));
        """,
    )
    assert json_schema(path)["$defs"]["posts"]["x-foreign-keys"] == [
        {"columns": ["author"], "references": {"table": "Users", "columns": ["ID"]}}
    ]


def test_foreign_key_to_a_missing_table_is_dropped(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE a (x INT REFERENCES nowhere (id));")
    assert "x-foreign-keys" not in json_schema(path)["$defs"]["a"]


def test_data_like_table_is_hidden_along_with_references_to_it(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE "CANARY@example.com" (id INTEGER PRIMARY KEY);
        CREATE TABLE ok (ref INT REFERENCES "CANARY@example.com" (id));
        """,
    )
    assert CANARY not in all_outputs(path)
    defs = json_schema(path)["$defs"]
    assert set(defs) == {"ok"}
    assert "x-foreign-keys" not in defs["ok"]
    assert any("table names" in note for note in extract(path).notes)


def test_data_like_columns_are_hidden_along_with_keys_using_them(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE p ("CANARY@example.com" INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE q ("CANARY Person" INT REFERENCES p ("CANARY@example.com"), kept INT);
        CREATE TABLE r (ref INT REFERENCES p ("CANARY@example.com"));
        """,
    )
    assert CANARY not in all_outputs(path)
    defs = json_schema(path)["$defs"]
    assert set(defs["p"]["properties"]) == {"name"}
    assert defs["p"]["additionalProperties"] == {"type": "integer"}
    assert "x-primary-key" not in defs["p"]
    assert "x-foreign-keys" not in defs["q"]
    assert "x-foreign-keys" not in defs["r"]


def test_map_path_hides_a_table_with_ordinary_column_names(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE t (CANARYalice INT, CANARYbob TEXT);")
    assert CANARY in all_outputs(path)
    policy = KeyPolicy(map_paths=frozenset({"$.t"}))
    assert CANARY not in all_outputs(path, policy)
    assert json_schema(path, policy)["$defs"]["t"]["additionalProperties"] == {
        "type": ["integer", "null", "string"]
    }


def test_single_table_keeps_its_name(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE only_one (a INT);")
    assert set(json_schema(path)["$defs"]) == {"only_one"}


def test_internal_tables_are_left_out(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE a (id INTEGER PRIMARY KEY AUTOINCREMENT, b INT);
        CREATE INDEX i ON a (b);
        INSERT INTO a (b) VALUES (1);
        ANALYZE;
        """,
    )
    assert set(json_schema(path)["$defs"]) == {"a"}


@pytest.mark.parametrize(
    "virtual_table, name",
    [
        ("CREATE VIRTUAL TABLE docs USING fts5(body);", "docs"),
        ("CREATE VIRTUAL TABLE docs USING rtree(id, x0, x1);", "docs"),
    ],
)
def test_virtual_table_is_skipped_with_a_note_and_the_rest_still_described(
    tmp_path, virtual_table, name
):
    with closing(sqlite3.connect(":memory:")) as probe:
        try:
            probe.execute(virtual_table)
        except sqlite3.OperationalError:
            pytest.skip("this SQLite build lacks the module")
    script = f"""
        {virtual_table}
        CREATE TABLE kept (id INTEGER PRIMARY KEY, ref INT REFERENCES {name} (id));
    """
    path = make_db(tmp_path, script)
    schema = extract(path)
    entity_names = {entity.name for entity in schema.entities}
    assert "kept" in entity_names and name not in entity_names
    assert any(f"$.{name}" in note for note in schema.notes)
    kept = next(entity for entity in schema.entities if entity.name == "kept")
    assert kept.foreign_keys == ()


def test_full_text_search_contents_do_not_leak(tmp_path):
    script = """
        CREATE VIRTUAL TABLE docs USING fts5(body);
        INSERT INTO docs VALUES ('CANARY body text');
    """
    try:
        path = make_db(tmp_path, script)
    except sqlite3.OperationalError:
        pytest.skip("this SQLite build lacks FTS5")
    assert CANARY not in all_outputs(path)


def test_ordinary_database_features_are_described(tmp_path):
    path = make_db(
        tmp_path,
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE s (a INT, b ANY) STRICT;
        CREATE TABLE g (a INT, b INT GENERATED ALWAYS AS (a + 1));
        CREATE TABLE w (a INT PRIMARY KEY) WITHOUT ROWID;
        """,
    )
    defs = json_schema(path)["$defs"]
    assert set(defs["s"]["properties"]) == {"a", "b"}
    assert set(defs["g"]["properties"]) == {"a", "b"}
    assert defs["w"]["x-primary-key"] == ["a"]


def test_empty_database_says_so(tmp_path):
    path = tmp_path / "empty.db"
    path.touch()
    schema = extract(str(path))
    assert schema.entities == ()
    assert schema.notes == ("no tables found",)


def test_text_format(tmp_path):
    path = make_db(
        tmp_path,
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, age INT);
        CREATE TABLE posts (id INTEGER PRIMARY KEY, author INT REFERENCES users (id));
        """,
    )
    assert render(extract(path), "text") == (
        "posts: {\n"
        "  id: integer  -- INTEGER\n"
        "  author: integer | null  -- INTEGER\n"
        "}\n"
        "  primary key (id)\n"
        "  foreign key (author) -> users (id)\n"
        "\n"
        "users: {\n"
        "  id: integer  -- INTEGER\n"
        "  email: string  -- TEXT\n"
        "  age: integer | null  -- INTEGER\n"
        "}\n"
        "  primary key (id)"
    )


@pytest.mark.parametrize("extension", [".db", ".sqlite", ".sqlite3", ".SQLITE"])
def test_extensions_are_detected(tmp_path, extension):
    path = make_db(tmp_path, "CREATE TABLE a (b INT);", name=f"x{extension}")
    assert extract(path).source_kind == "sql"


def test_explicit_source_overrides_extension(tmp_path):
    path = make_db(tmp_path, "CREATE TABLE a (b INT);", name="x.bin")
    assert extract(path, source="sql").source_kind == "sql"


@pytest.mark.parametrize(
    "declared, expected",
    [
        ("INTEGER", "INTEGER"),
        ("VARCHAR(255)", "VARCHAR(255)"),
        ("DECIMAL(10, 2)", "DECIMAL(10, 2)"),
        ("UNSIGNED BIG INT", "UNSIGNED BIG INT"),
        ("TIMESTAMP(6) WITH TIME ZONE", "TIMESTAMP(6) WITH TIME ZONE"),
        ("INTEGER[]", "INTEGER[]"),
        ("VARCHAR(max)", "VARCHAR(max)"),
        ("VARCHAR2(255 CHAR)", "VARCHAR2(255 CHAR)"),
        ("NUMBER(10, 2)", "NUMBER(10, 2)"),
        ('VARCHAR(20) COLLATE "en_US"', "VARCHAR(20)"),
        ("VARCHAR(20) CHARACTER SET latin1", "VARCHAR(20)"),
        ("public.order_status", "public.order_status"),
        ("ENUM('CANARY','b')", "ENUM"),
        ("SET('CANARY')", "SET"),
        ('"CANARY Type"', None),
        ("'CANARY'", None),
        ("", None),
        ("x" * 65, None),
    ],
)
def test_native_type_cleaning(declared, expected):
    assert clean_native_type(declared) == expected


def test_json_documents_are_unaffected(tmp_path):
    path = tmp_path / "a.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert json.loads(render(extract(str(path))))["properties"] == {"a": {"type": "integer"}}
    assert render(extract(str(path)), "text").startswith("root: ")
