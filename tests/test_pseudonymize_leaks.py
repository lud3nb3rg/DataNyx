"""Real values that once reached a pseudonymized copy, and the ways a copy could come out wrong."""

import csv
import json
import re
import sqlite3

import pytest

from datanyx.cli import main
from datanyx.pseudonymize import Options, Pseudonymizer, build_plan, classify, pseudonymize_file
from datanyx.pseudonymize.sql_text import clean_table_sql, holds_text, without_comments
from datanyx.text import Anonymizer, PatternDetector

KEY = bytes(range(32))


def pseudo(**options):
    options.setdefault("text", lambda: Anonymizer([PatternDetector()]))
    return Pseudonymizer(KEY, **options)


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return str(path)


def copy(tmp_path, source, kind, fakes=None, **options):
    target = tmp_path / f"out.{kind}"
    report = pseudonymize_file(source, str(target), kind, fakes or pseudo(), Options(**options))
    return target, report


def rows_of(path):
    with open(path, encoding="utf-8", newline="") as stream:
        return list(csv.reader(stream))


# --- Phones -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("number", ["+14155552671", "+442079460958", "+919876543210", " +14155552671"])
def test_a_phone_without_separators_is_replaced(number):
    fake = pseudo().apply("phone", number)
    assert fake != number and len(fake) == len(number) and fake.lstrip().startswith("+")
    assert number.strip()[3:] not in fake


def test_a_phone_with_separators_keeps_its_country_code():
    assert pseudo().apply("phone", "+1 415-555-2671").startswith("+1 ")
    assert pseudo().apply("phone", "+353 1 234 5678").startswith("+353 ")


def test_a_replaced_phone_is_counted():
    fakes = pseudo()
    fakes.apply("phone", "+14155552671")
    assert fakes.counts["phone"] == 1


def test_a_phone_stored_as_a_number_stays_a_number():
    fake = pseudo().apply("phone", 14155552671)
    assert isinstance(fake, int) and fake != 14155552671 and len(str(fake)) == 11
    assert isinstance(pseudo().apply("postal", 90210), int)


# --- Running out of fakes -------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["name", "first_name", "last_name", "city", "company", "address", "category", "email"])
def test_fakes_never_merge_two_real_values(kind):
    fakes = pseudo()
    results = {fakes.apply(kind, f"Value number {i}") for i in range(5000)}
    assert len(results) == 5000


def test_a_small_range_of_ids_never_hangs_or_merges():
    fakes = pseudo()
    letters = "abcdefghijklmnopqrstuvwxyz"
    values = [*letters, *letters.upper(), *[f"{a}" * 2 for a in letters]]
    results = {fakes.apply("id", value) for value in values if not value.isupper()}
    assert len(results) == len({value for value in values if not value.isupper()})
    tiny = {fakes.apply("postal", f"{i}") for i in range(10)} | {fakes.apply("postal", f"x{i}") for i in range(100)}
    assert len(tiny) == 110


def test_exhausting_a_range_adds_a_serial_rather_than_hanging():
    fakes = pseudo()
    results = {fakes._unique("exhausted", f"value {i}", lambda rng: rng.choice("ab")) for i in range(50)}
    assert len(results) == 50 and fakes.redraws > 0


# --- Fakes don't depend on order -----------------------------------------------------------------------


def test_a_fake_doesnt_depend_on_what_else_was_seen():
    values = [f"Person Number {i}" for i in range(3000)]
    first, second = pseudo(), pseudo()
    forward = {value: first.apply("name", value) for value in values}
    backward = {value: second.apply("name", value) for value in reversed(values)}
    assert forward == backward and first.redraws == 0 and second.redraws == 0


def test_a_clash_is_reported_on_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("datanyx.pseudonymize.fakes._word", lambda rng: rng.choice(["Aa", "Bb", "Cc"]))
    source = write(tmp_path, "c.csv", "first_name\nAna\nBea\nCid\nDee\nEve\nFay\n")
    assert main(["pseudonymize", source, "-o", str(tmp_path / "o.csv"), "--key-file", str(tmp_path / "k.key")]) == 0
    assert "clashed" in capsys.readouterr().err


# --- The mapping ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("name", "Real Person"),
        ("first_name", "Real"),
        ("last_name", "Person"),
        ("email", "real@corp.com"),
        ("phone", "+1 415-555-2671"),
        ("address", "1 Real Street"),
        ("city", "Realtown"),
        ("company", "Real Co"),
        ("postal", "90210"),
        ("token", "hunter22"),
        ("ip", "203.0.113.9"),
        ("url", "https://real.example/x"),
        ("category", "gold"),
        ("id", "1001"),
        ("id", "AB-12"),
        ("date", "1990-04-05"),
        ("text", "Mail a.person@corp.io today"),
    ],
)
def test_the_mapping_holds_every_kind(kind, value):
    fakes = pseudo(record=True)
    fake = fakes.apply(kind, value)
    assert fakes.pairs[kind][str(fake)] == value


def test_nothing_is_recorded_unless_asked():
    fakes = pseudo()
    fakes.apply("phone", "+1 415-555-2671")
    assert fakes.pairs == {}


# --- CSV -----------------------------------------------------------------------------------------------------


def test_blank_lines_before_the_header_dont_turn_off_the_plan(tmp_path):
    target, report = copy(tmp_path, write(tmp_path, "c.csv", "\n\nname,email\nAna Real,a@corp.com\n"), "csv")
    text = target.read_text(encoding="utf-8")
    assert "Ana Real" not in text and "a@corp.com" not in text and "name,email" in text
    assert report.plans["file"]["email"].kind == "email" and report.rows == 1


def test_a_file_of_blank_lines_is_empty(tmp_path):
    from datanyx.sources import SourceError

    with pytest.raises(SourceError, match="empty"):
        copy(tmp_path, write(tmp_path, "c.csv", "\n\n,,\n"), "csv")


def test_cells_past_the_header_are_blanked_and_noted(tmp_path):
    target, report = copy(tmp_path, write(tmp_path, "c.csv", "name,email\nAna Real,a@corp.com,555-123-4567,extra\n"), "csv")
    rows = rows_of(target)
    assert "555-123-4567" not in target.read_text(encoding="utf-8") and rows[1][2:] == ["", ""]
    assert any("past the end of the header" in note for note in report.notes)


def test_duplicate_headers_are_sampled_by_position(tmp_path):
    source = write(tmp_path, "c.csv", "id,id,notes,contact\n1,2,hello there,a@corp.com\n3,4,hi again,b@corp.com\n")
    target, report = copy(tmp_path, source, "csv")
    plan = report.plans["file"]
    assert plan["contact"].kind == "email" and plan["id"].kind == "id" and plan["id_2"].kind == "id"
    text = target.read_text(encoding="utf-8")
    assert "a@corp.com" not in text and text.splitlines()[0] == "id,id,notes,contact"


def test_a_setting_for_a_duplicated_column_covers_both(tmp_path):
    source = write(tmp_path, "c.csv", "x,x,y,y\na,b,c,d\n")
    target, _ = copy(tmp_path, source, "csv", overrides={"x": "drop"}, keep=frozenset({"y"}))
    assert rows_of(target)[1] == ["", "", "c", "d"]


def test_a_file_with_no_header_has_its_first_row_replaced_too(tmp_path):
    target, report = copy(tmp_path, write(tmp_path, "c.csv", "Jane Doe,jane@corp.com\nBob Roe,bob@corp.com\n"), "csv")
    text = target.read_text(encoding="utf-8")
    assert "jane@corp.com" not in text and "bob@corp.com" not in text and report.rows == 2
    assert any("first row" in note for note in report.notes)


# --- What a name says when the values are numbers ----------------------------------------------------


@pytest.mark.parametrize(("name", "kind"), [("phone", "phone"), ("zip", "postal"), ("customer_id", "id"), ("dob", "date")])
def test_a_name_can_speak_for_numbers(name, kind):
    assert classify(name, [14155552671]).kind == kind


@pytest.mark.parametrize("name", ["email", "full_name", "city", "street_address"])
def test_a_name_that_doesnt_fit_numbers_is_kept_but_flagged(name):
    treatment = classify(name, [1, 2])
    assert treatment.kind == "keep" and treatment.review is True


def test_a_column_empty_in_the_sample_goes_by_its_name():
    assert classify("email", [None, ""]).kind == "email"
    assert classify("email", []).kind == "email"


def test_binary_values_that_are_kept_are_flagged():
    assert classify("photo", [b"\x89PNG"]).review is True
    assert build_plan({"photo": [b"x"]}, strict=True)["photo"].kind == "category"


def test_numeric_phones_in_json_are_replaced(tmp_path):
    target, _ = copy(tmp_path, write(tmp_path, "d.json", json.dumps({"phone": 14155552671, "age": 31})), "json")
    out = json.loads(target.read_text(encoding="utf-8"))
    assert out["phone"] != 14155552671 and isinstance(out["phone"], int) and out["age"] == 31


# --- JSON with nothing to go by ----------------------------------------------------------------------


def test_values_under_data_like_keys_are_replaced(tmp_path):
    source = write(tmp_path, "d.json", json.dumps({"jane@corp.com": "123-45-6789", "bob@corp.com": "bob's secret", "a@corp.com": 12.5}))
    target, report = copy(tmp_path, source, "json")
    text = target.read_text(encoding="utf-8")
    for real in ("jane@corp.com", "123-45-6789", "bob's secret"):
        assert real not in text
    assert "12.5" in text and any("no key" in note for note in report.notes)


def test_bare_top_level_values_are_replaced(tmp_path):
    target, _ = copy(tmp_path, write(tmp_path, "d.json", json.dumps(["jane@corp.com", "bob@corp.com", "free words"])), "json")
    out = json.loads(target.read_text(encoding="utf-8"))
    assert all(value.endswith("@example.com") for value in out[:2]) and out[2] != "free words"


def test_a_key_first_seen_after_the_sample_is_still_decided(tmp_path, monkeypatch):
    monkeypatch.setattr("datanyx.pseudonymize.tables.SAMPLE_ROWS", 2)
    lines = [json.dumps({"n": i}) for i in range(5)] + [json.dumps({"email": "late@corp.com", "kept": "no"})]
    target, report = copy(tmp_path, write(tmp_path, "d.jsonl", "\n".join(lines) + "\n"), "jsonl", keep=frozenset({"kept"}))
    text = target.read_text(encoding="utf-8")
    assert "late@corp.com" not in text and report.plans["file"]["email"].kind == "email" and '"kept": "no"' in text


# --- SQLite ----------------------------------------------------------------------------------------------------


def database(tmp_path, script, name="s.db"):
    path = tmp_path / name
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.commit()
    connection.close()
    return str(path)


def test_literals_in_the_schema_dont_reach_the_copy(tmp_path):
    source = database(
        tmp_path,
        """
        create table t (
            id integer primary key, -- Jane's table
            owner text not null default 'Jane Doe',
            alias text default "Janey",
            code blob default x'4a616e65',
            email text check (email <> 'blocked@corp.com'),
            n integer default 7,
            constraint no_bob check (owner <> 'Bob Roe'),
            unique (email)
        );
        /* Bob's notes */
        create table u (a text, b text, check (a <> 'Carl'));
        create view v as select * from t where owner = 'Dana Fox';
        create view safe as select id from t where n > 5; -- Eve
        create index i on t(owner) where owner <> 'Fay Gee';
        create index safe_i on t(n);
        insert into t (id, email) values (1, 'a@corp.com');
        insert into u values ('x', 'y');
        """,
    )
    target, report = copy(tmp_path, source, "sqlite")
    connection = sqlite3.connect(target)
    schema = " ".join(row[0] for row in connection.execute("select sql from sqlite_master where sql is not null"))
    for real in ("Jane", "Janey", "4a616e65", "blocked@corp.com", "Bob", "Carl", "Dana", "Fay", "Eve"):
        assert real not in schema, real
    names = {row[0] for row in connection.execute("select name from sqlite_master")}
    assert {"t", "u", "safe", "safe_i"} <= names and "v" not in names and "i" not in names
    assert connection.execute("select count(*) from safe").fetchone() == (1,)  # n defaults to 7.
    assert connection.execute("select n from t").fetchone() == (7,)
    assert connection.execute("select count(*) from u").fetchone() == (1,)
    connection.close()
    notes = " ".join(report.notes)
    assert "CHECK" in notes and "definition" in notes and "view v" in notes and "index i" in notes


def test_a_fake_can_be_inserted_where_a_check_would_have_refused_it(tmp_path):
    source = database(tmp_path, "create table t (email text check (email like '%@corp.com')); insert into t values ('a@corp.com');")
    target, _ = copy(tmp_path, source, "sqlite")
    connection = sqlite3.connect(target)
    assert connection.execute("select email from t").fetchone()[0].endswith("@example.com")
    connection.close()


def test_keys_and_table_options_survive_cleaning(tmp_path):
    source = database(
        tmp_path,
        """
        create table parent (id integer primary key autoincrement, name text);
        create table child (a integer, b integer references parent(id) on delete cascade, primary key (a, b)) without rowid;
        insert into parent (name) values ('Jane Doe');
        insert into child values (1, 1);
        """,
    )
    target, _ = copy(tmp_path, source, "sqlite")
    connection = sqlite3.connect(target)
    assert connection.execute("pragma foreign_key_check").fetchall() == []
    assert connection.execute("pragma foreign_key_list(child)").fetchone()[2] == "parent"
    assert connection.execute("select count(*) from child c join parent p on p.id = c.b").fetchone() == (1,)
    connection.close()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("create table t (a text check (a <> 'x'), b int)", "create table t ( a text , b int )"),
        ("create table t (a int, check (a > 1))", "create table t ( a int )"),
        ("create table t (a int, check (a > 1), unique (a))", "create table t ( a int , unique ( a ) )"),
        ("create table t (a int, constraint c check (a > 1))", "create table t ( a int )"),
        ("create table t (a int constraint c check (a > 1) not null)", "create table t ( a int not null )"),
        ("create table t (a text default 'it''s')", "create table t ( a text default '' )"),
        ('create table "check" ("check" text)', 'create table "check" ( "check" text )'),
        ("create table t (a text) -- note", "create table t ( a text )"),
    ],
)
def test_cleaning_a_table_statement(sql, expected):
    assert clean_table_sql(sql).sql == expected
    sqlite3.connect(":memory:").execute(expected)


def test_finding_text_in_a_statement():
    assert holds_text("create view v as select * from t where a = 'x'")
    assert not holds_text("create view v as select * from t where a = 5 -- it's fine")
    assert not holds_text('create view v as select "a b" from t')
    assert without_comments("select 1 -- x\n/* y */ from t") == "select 1 from t"


def test_blobs_follow_the_plan(tmp_path):
    source = database(
        tmp_path,
        "create table t (photo blob, doc blob, name blob, raw blob); insert into t values (x'89504e47', x'6a616e65', x'4a616e6520446f65', x'ff00');",
    )
    target, report = copy(tmp_path, source, "sqlite", overrides={"photo": "drop", "doc": "category", "name": "name"}, keep=frozenset({"raw"}))
    connection = sqlite3.connect(target)
    photo, doc, name, raw = connection.execute("select photo, doc, name, raw from t").fetchone()
    connection.close()
    assert photo is None and doc != b"jane" and isinstance(doc, bytes) and b"Jane" not in name and raw == b"\xff\x00"
    assert report.notes == [] or all("blanked" not in note for note in report.notes)


def test_a_blob_that_cant_be_faked_is_blanked_and_noted(tmp_path):
    source = database(tmp_path, "create table t (doc blob); insert into t values (x'ff00ff');")
    target, report = copy(tmp_path, source, "sqlite", overrides={"doc": "text"})
    connection = sqlite3.connect(target)
    assert connection.execute("select doc from t").fetchone() == (None,)
    connection.close()
    assert any("binary" in note for note in report.notes)


@pytest.mark.parametrize("name", ["exports#1.db", "a?b.db" if __import__("os").name != "nt" else "a&b.db", "a%20b.db", "with space.db"])
def test_database_paths_are_not_read_as_uris(tmp_path, name):
    source = database(tmp_path, "create table t (email text); insert into t values ('a@corp.com');", name=name)
    target, report = copy(tmp_path, source, "sqlite")
    assert report.rows == 1 and "a@corp.com" not in target.read_bytes().decode("latin-1")


def test_tables_with_sqlite_like_names_are_copied(tmp_path):
    source = database(tmp_path, "create table sqlitedata (a text); create table sqlite2_users (b text); insert into sqlitedata values ('x'); insert into sqlite2_users values ('y');")
    target, report = copy(tmp_path, source, "sqlite")
    connection = sqlite3.connect(target)
    assert connection.execute("select count(*) from sqlitedata").fetchone() == (1,)
    assert connection.execute("select count(*) from sqlite2_users").fetchone() == (1,)
    connection.close()
    assert set(report.plans) == {"sqlitedata", "sqlite2_users"}


def test_a_full_text_index_doesnt_carry_its_text_across(tmp_path):
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("create virtual table probe using fts5(a)")
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no FTS5")
    finally:
        connection.close()
    source = database(tmp_path, "create virtual table docs using fts5(body); insert into docs values ('Jane Doe wrote this');")
    target, report = copy(tmp_path, source, "sqlite")
    assert b"Jane Doe" not in target.read_bytes()
    assert any("docs" in note for note in report.notes)


def test_no_real_digits_survive_a_csv_of_unbroken_phones(tmp_path):
    target, report = copy(tmp_path, write(tmp_path, "c.csv", "phone\n+14155552671\n+14155550000\n"), "csv")
    text = target.read_text(encoding="utf-8")
    assert not re.search(r"4155552671|4155550000", text)
    assert report.plans["file"]["phone"].kind == "phone"
