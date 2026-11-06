"""Pseudonymized copies are consistent and joinable, keep their shape, and hold no real values."""

import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest

from datanyx import extract, render
from datanyx.cli import main
from datanyx.pseudonymize import (
    Options,
    Pseudonymizer,
    build_plan,
    classify,
    load_key,
    parse_overrides,
    pseudonymize_file,
    render_plan,
)
from datanyx.sources import SourceError
from datanyx.text import Anonymizer, DictionaryDetector, PatternDetector

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
REAL_NAMES = ["Jessica Whitfield", "Tomasz Kowalczyk", "Amara Okafor"]


def pseudo(key=KEY, **options):
    options.setdefault("text", lambda: Anonymizer([PatternDetector()]))
    return Pseudonymizer(key, **options)


# --- Fake values -----------------------------------------------------------------------------------


def test_the_same_value_always_gets_the_same_fake_and_the_key_decides_which():
    a, b, c = pseudo(), pseudo(), pseudo(OTHER_KEY)
    assert a.apply("name", "Jessica Whitfield") == b.apply("name", "Jessica Whitfield")
    assert a.apply("name", "Jessica Whitfield") != c.apply("name", "Jessica Whitfield")
    assert a.apply("name", "  JESSICA   whitfield ") == a.apply("name", "Jessica Whitfield")  # Case and spacing don't matter.
    assert a.apply("name", "Jessica Whitfield") != "Jessica Whitfield"


def test_different_real_names_never_share_a_fake():
    fakes = pseudo()
    names = {fakes.apply("name", f"Person Number {i}") for i in range(2000)}
    assert len(names) == 2000
    emails = {fakes.apply("email", f"user{i}@acme-corp.io") for i in range(2000)}
    assert len(emails) == 2000 and all(e.endswith("@example.com") for e in emails)


def test_whole_number_ids_keep_their_length_and_never_collide():
    fakes = pseudo()
    mapped = {i: fakes.apply("id", i) for i in range(1000, 10000)}
    assert len(set(mapped.values())) == 9000  # One to one, so a join can't merge two rows.
    assert all(1000 <= v <= 9999 for v in mapped.values())
    assert sorted(fakes.apply("id", i) for i in range(10)) == list(range(10))
    assert fakes.apply("id", -4821) < 0 and len(str(abs(fakes.apply("id", -4821)))) == 4
    assert all(isinstance(fakes.apply("id", i), int) for i in (7, 70, 700))
    assert fakes.apply("id", 1001) == pseudo().apply("id", 1001) != pseudo(OTHER_KEY).apply("id", 1001)


def test_ids_written_as_text_stay_text_with_the_same_shape():
    fakes = pseudo()
    assert isinstance(fakes.apply("id", "4821"), str) and len(fakes.apply("id", "4821")) == 4
    padded = fakes.apply("id", "00042")
    assert isinstance(padded, str) and len(padded) == 5
    assert fakes.apply("id", "4821") == str(fakes.apply("id", 4821))  # A number and its text agree.
    code = fakes.apply("id", "AB-123x")
    assert re.fullmatch(r"[A-Z]{2}-\d{3}[a-z]", code) and code != "AB-123x"
    assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", fakes.apply("id", "d290f1ee-6c54-4b01-90e6-d701748f0851"))
    huge = fakes.apply("id", "9" * 25)
    assert len(huge) == 25 and huge.isdigit()
    assert fakes.apply("id", 3.5) == 3.5


def test_phone_numbers_postal_codes_and_ips_keep_their_shape():
    fakes = pseudo()
    phone = fakes.apply("phone", "+44 20 7946 0958")
    assert re.fullmatch(r"\+44 \d{2} \d{4} \d{4}", phone) and phone != "+44 20 7946 0958"
    first, second = fakes.apply("phone", "(415) 555-2671"), fakes.apply("phone", "415.555.2671")
    assert re.sub(r"\D", "", first) == re.sub(r"\D", "", second)  # The same number, however it's written.
    assert re.fullmatch(r"[A-Z]\d [A-Z]{3}", fakes.apply("postal", "K1 ABC")) or re.fullmatch(r"[A-Z]\d \d[A-Z]{2}", fakes.apply("postal", "K1 2AB"))
    assert re.fullmatch(r"10\.\d+\.\d+\.\d+", fakes.apply("ip", "203.0.113.9"))
    assert fakes.apply("ip", "2001:db8::1").startswith("2001:db8::")
    assert fakes.apply("url", "http://acme.example/x?id=1").startswith("http://example.com/")


def test_dates_move_together_and_keep_their_form():
    fakes = pseudo()
    a, b = fakes.apply("date", "2024-01-02"), fakes.apply("date", "2024-02-11")
    from datetime import date

    assert (date.fromisoformat(b) - date.fromisoformat(a)).days == 40  # The gap survives.
    assert a != "2024-01-02"
    stamped = fakes.apply("date", "2024-01-02T10:30:00Z")
    assert stamped.endswith("T10:30:00Z") and stamped[:10] == a
    assert fakes.apply("date", "yesterday") == "yesterday" and fakes.unparsed["date"] == 1


def test_other_kinds_nulls_and_counts():
    fakes = pseudo(record=True)
    assert fakes.apply("token", "hunter22") != "hunter22" and len(fakes.apply("token", "hunter22")) >= 8
    assert fakes.apply("category", "gold") == fakes.apply("category", "gold") != fakes.apply("category", "silver")
    assert fakes.apply("drop", "anything") is None and fakes.apply("keep", "x") == "x"
    for value in (None, "", True):
        assert fakes.apply("name", value) == value
    assert re.fullmatch(r"[A-Z][a-z]+", fakes.apply("first_name", "Ana"))
    assert fakes.counts["token"] == 2  # Applied twice above.
    fake = fakes.apply("name", "Real Person")
    assert fakes.pairs["name"][fake] == "Real Person"


def test_free_text_uses_the_text_anonymizer_and_only_loads_it_when_needed():
    built = []

    def factory():
        built.append(1)
        return Anonymizer([DictionaryDetector("PERSON", ["Jessica Whitfield"])])

    fakes = pseudo(text=factory)
    assert fakes.apply("name", "Someone") and built == []
    assert fakes.apply("text", "Jessica Whitfield called") == "<PERSON_1> called"
    fakes.apply("text", "Jessica Whitfield again")
    assert built == [1]
    with pytest.raises(ValueError):
        pseudo(text=None).apply("text", "no anonymizer")


def test_a_short_key_is_refused():
    with pytest.raises(ValueError):
        Pseudonymizer(b"short")


def test_key_files_are_created_reused_and_private(tmp_path):
    path = tmp_path / "k.key"
    key = load_key(str(path))
    assert len(key) == 32 and load_key(str(path)) == key
    assert load_key(None) != load_key(None)
    if sys.platform == "win32":
        listing = subprocess.run(["icacls", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert len([line for line in listing.stdout.splitlines() if ":(" in line]) == 1
    else:
        assert os.stat(path).st_mode & 0o777 == 0o600
    for text, message in (("not hex!", "can't be read"), ("abcd", "too short")):
        (tmp_path / "bad.key").write_text(text, encoding="utf-8")
        with pytest.raises(SourceError, match=message):
            load_key(str(tmp_path / "bad.key"))


# --- Deciding what a column holds --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "values", "kind"),
    [
        ("customer_id", ["1", "2"], "id"),
        ("customerId", [1, 2], "id"),
        ("email", ["a@b.co"], "email"),
        ("Contact_Email", ["a@b.co"], "email"),
        ("first_name", ["Ana"], "first_name"),
        ("surname", ["Silva"], "last_name"),
        ("full_name", ["Ana Silva"], "name"),
        ("customer", ["Ana Silva"], "name"),
        ("phone_number", ["+44 20 7946 0958"], "phone"),
        ("zip", ["01234"], "postal"),
        ("street_address", ["1 Main St"], "address"),
        ("city", ["Leeds"], "city"),
        ("employer", ["Acme"], "company"),
        ("ip_address", ["10.0.0.1"], "ip"),
        ("homepage", ["https://x.example"], "url"),
        ("date_of_birth", ["1990-04-05"], "date"),
        ("dob", ["1990-04-05"], "date"),
        ("ssn", ["123-45-6789"], "id"),
        ("api_key", ["abcd"], "token"),
        ("username", ["jwhit"], "id"),
        ("notes", ["said hello"], "text"),
        ("comment", ["fine"], "text"),
    ],
)
def test_names_decide_what_a_column_holds(name, values, kind):
    assert classify(name, values).kind == kind


@pytest.mark.parametrize(
    ("name", "values", "kind"),
    [
        ("contact", ["a@b.co", "c@d.org"], "email"),
        ("ref", ["d290f1ee-6c54-4b01-90e6-d701748f0851"], "id"),
        ("src", ["10.1.1.1", "192.168.0.4"], "ip"),
        ("link", ["https://a.example/x", "https://b.example/y"], "url"),
        ("tel", ["+1 415 555 2671", "(415) 555-0100"], "phone"),
        ("blob", ["GB82 WEST 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00"], "id"),
        ("x", ["A long sentence about a customer who wrote in to complain about a late delivery."] * 5, "text"),
    ],
)
def test_values_decide_when_the_name_doesnt(name, values, kind):
    assert classify(name, values).kind == kind


def test_things_that_are_not_personal_are_kept_and_only_text_is_flagged():
    assert classify("salary", ["52000", "48000.50"]).review is False  # Numbers written as text.
    assert classify("joined", ["2024-01-02", "2024-02-11"]) == classify("created", ["2024-03-04"]).__class__("keep", "dates are kept, except birth dates")
    assert classify("quantity", [1, 2, 3]).kind == "keep"
    assert classify("plan", ["pro", "free"]).review is True
    assert classify("last_login", ["yesterday"]).kind == "keep"  # "last" without "name" isn't a surname.
    assert classify("product_name", ["Widget"]).kind == "keep"
    assert classify("digits", ["1234567890", "9876543210"]).kind == "keep"  # A bare run of digits isn't a phone.
    assert classify("email", [1, 2]).kind == "keep"  # A hint that doesn't fit the data.


def test_keys_stay_joinable_unless_the_values_say_otherwise():
    assert classify("ref", ["a", "b"], is_key=True).kind == "id"
    assert classify("owner_email", ["a@b.co"], is_key=True).kind == "email"
    assert classify("code", ["x1", "y2"], is_key=True).reason.startswith("it is a key")


def test_overrides_keeps_and_strict():
    columns = {"plan": ["pro", "free"], "notes": ["hello there"], "region": ["north"], "age": [31, 42]}
    plan = build_plan(columns, overrides={"Region": "city"}, keep=frozenset({"NOTES"}))
    assert (plan["region"].kind, plan["notes"].kind, plan["plan"].kind) == ("city", "keep", "keep")
    strict = build_plan(columns, strict=True)
    assert strict["plan"].kind == "category" and strict["age"].kind == "keep"  # Numbers aren't text.
    assert build_plan(columns, strict=True, keep=frozenset({"plan"}))["plan"].kind == "keep"


def test_override_syntax_is_checked_and_the_plan_reads_clearly():
    assert parse_overrides(["notes=text", " a.b = id".replace(" ", "")]) == {"notes": "text", "a.b": "id"}
    for bad in ("nokind", "=text", "x=nonsense"):
        with pytest.raises(SourceError):
            parse_overrides([bad])
    text = render_plan("Plan", build_plan({"email": ["a@b.co"], "plan": ["pro"]}))
    assert "email" in text and "the name says email" in text and "kept: check it" in text


# --- Tables --------------------------------------------------------------------------------------------


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return str(path)


def run_file(tmp_path, source, kind, output="out", **options):
    target = str(tmp_path / output)
    report = pseudonymize_file(source, target, kind, pseudo(), Options(**options))
    return target, report


CUSTOMERS = (
    "customer_id,name,email,phone,plan,joined\n"
    "1001,Jessica Whitfield,jessica@acme-corp.io,+44 20 7946 0958,pro,2024-01-02\n"
    "1002,Tomasz Kowalczyk,tk@acme-corp.io,+44 20 7946 0111,free,2024-02-11\n"
)
ORDERS = "order_id,customer_id,total\n1,1001,10.5\n2,1002,7.25\n3,1001,3.0\n"


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as stream:
        return list(csv.reader(stream))


def test_csv_values_are_replaced_by_the_plan_and_the_rest_is_untouched(tmp_path):
    target, report = run_file(tmp_path, write(tmp_path, "c.csv", CUSTOMERS), "csv")
    before, after = read_csv(tmp_path / "c.csv"), read_csv(target)
    assert after[0] == before[0] and len(after) == len(before) and report.rows == 2
    assert [row[4:] for row in after[1:]] == [row[4:] for row in before[1:]]  # plan and joined kept.
    text = open(target, encoding="utf-8").read()
    for real in ("Jessica", "Whitfield", "jessica@", "7946", "1001", "1002"):
        assert real not in text
    assert all(len(row[0]) == 4 for row in after[1:])


def test_joins_survive_across_files(tmp_path):
    fakes = pseudo()
    customers = pseudonymize_file(write(tmp_path, "c.csv", CUSTOMERS), str(tmp_path / "c2.csv"), "csv", fakes, Options())
    orders = pseudonymize_file(write(tmp_path, "o.csv", ORDERS), str(tmp_path / "o2.csv"), "csv", fakes, Options())
    assert customers.rows == 2 and orders.rows == 3
    customer_ids = {row[0] for row in read_csv(tmp_path / "c2.csv")[1:]}
    order_customers = [row[1] for row in read_csv(tmp_path / "o2.csv")[1:]]
    assert set(order_customers) == customer_ids and order_customers[0] == order_customers[2]
    assert [row[2] for row in read_csv(tmp_path / "o2.csv")[1:]] == ["10.5", "7.25", "3.0"]


def test_csv_edge_cases(tmp_path):
    text = '﻿id,name,note\r\n1,"Smith, Jane","said ""hi"""\r\n2,Bo\r\n3\r\n'
    target, _ = run_file(tmp_path, write(tmp_path, "e.csv", text), "csv", overrides={"note": "keep"})
    rows = read_csv(target)
    assert rows[0] == ["id", "name", "note"] and rows[1][2] == 'said "hi"' and len(rows[2]) == 2 and len(rows[3]) == 1
    assert "Smith" not in open(target, encoding="utf-8").read()
    tsv, _ = run_file(tmp_path, write(tmp_path, "e.tsv", "id\temail\n1\ta@b.co\n"), "tsv", output="e_out.tsv")
    assert "a@b.co" not in open(tsv, encoding="utf-8").read() and open(tsv, encoding="utf-8").read().startswith("id\temail\n")
    with pytest.raises(SourceError, match="empty"):
        run_file(tmp_path, write(tmp_path, "empty.csv", ""), "csv", output="x")


def test_a_plan_writes_nothing_and_existing_files_are_never_replaced(tmp_path):
    source = write(tmp_path, "c.csv", CUSTOMERS)
    report = pseudonymize_file(source, None, "csv", pseudo(), Options(plan_only=True))
    assert report.plans["file"]["email"].kind == "email" and report.rows == 0
    existing = write(tmp_path, "exists.csv", "keep me")
    with pytest.raises(SourceError, match="already exists"):
        pseudonymize_file(source, existing, "csv", pseudo(), Options())
    assert open(existing, encoding="utf-8").read() == "keep me"


def test_json_is_treated_by_key_name_wherever_it_appears(tmp_path):
    data = {
        "customers": [
            {"customer_id": 1001, "name": "Jessica Whitfield", "tags": ["vip"], "contact": {"email": "jessica@acme-corp.io", "active": True}, "score": 4.5},
            {"customer_id": 1002, "name": "Tomasz Kowalczyk", "contact": {"email": "tk@acme-corp.io", "active": False}, "score": 3.0},
        ],
        "balances": {"jessica@acme-corp.io": 12.5, "tk@acme-corp.io": 3},
    }
    target, _ = run_file(tmp_path, write(tmp_path, "d.json", json.dumps(data, indent=2)), "json")
    out = json.loads(open(target, encoding="utf-8").read())
    first = out["customers"][0]
    assert first["customer_id"] != 1001 and 1000 <= first["customer_id"] <= 9999 and first["name"] != "Jessica Whitfield"
    assert first["contact"]["email"].endswith("@example.com") and first["contact"]["active"] is True and first["score"] == 4.5
    assert first["tags"] == ["vip"]
    assert all(k.endswith("@example.com") for k in out["balances"]) and sorted(out["balances"].values()) == [3, 12.5]
    assert out["balances"][first["contact"]["email"]] == 12.5  # The email as a value and as a key agree.
    text = open(target, encoding="utf-8").read()
    assert "Jessica" not in text and "acme-corp" not in text and text.startswith("{\n  ")


def test_jsonl_is_streamed_line_by_line(tmp_path):
    lines = [json.dumps({"id": i, "email": f"user{i}@acme-corp.io", "n": i * 2}) for i in range(1, 3001)]
    target, report = run_file(tmp_path, write(tmp_path, "d.jsonl", "\n".join(lines) + "\n"), "jsonl")
    out = [json.loads(line) for line in open(target, encoding="utf-8")]
    assert report.rows == 3000 and len(out) == 3000
    assert len({r["id"] for r in out}) == 3000 and len({r["email"] for r in out}) == 3000
    assert [r["n"] for r in out][:3] == [2, 4, 6]
    with pytest.raises(SourceError, match="line 2"):
        run_file(tmp_path, write(tmp_path, "bad.jsonl", '{"a": 1}\n{oops\n'), "jsonl", output="bad_out")


def make_database(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table customers (id integer primary key, name text not null, email text unique, notes text, photo blob);
        create table orders (id integer primary key, customer_id integer references customers(id), total real, placed text);
        create table links (a integer, b integer, primary key (a, b)) without rowid;
        create index orders_by_customer on orders(customer_id);
        create view big_orders as select * from orders where total > 5;
        create trigger t after insert on orders begin select 1; end;
        """
    )
    for i in range(1, 301):
        connection.execute("insert into customers values (?,?,?,?,?)", (i, f"Person Number {i}", f"user{i}@acme-corp.io", "regular", b"\x89PNG"))
        connection.execute("insert into orders values (?,?,?,?)", (i * 3, i, i / 4, "2024-01-02"))
        connection.execute("insert into links values (?,?)", (i, 301 - i))
    connection.commit()
    connection.close()
    return str(path)


def test_sqlite_keeps_keys_valid_and_the_schema_and_leaves_the_original_alone(tmp_path):
    source = make_database(tmp_path)
    digest = hashlib.sha256(open(source, "rb").read()).hexdigest()
    target, report = run_file(tmp_path, source, "sqlite", output="copy.db")
    assert hashlib.sha256(open(source, "rb").read()).hexdigest() == digest
    connection = sqlite3.connect(target)
    assert connection.execute("pragma foreign_key_check").fetchall() == []
    assert connection.execute("select count(*) from customers").fetchone() == (300,)
    assert connection.execute("select count(distinct id), count(distinct email) from customers").fetchone() == (300, 300)
    assert connection.execute("select count(*) from orders o join customers c on c.id = o.customer_id").fetchone() == (300,)
    assert connection.execute("select count(*) from big_orders").fetchone()[0] > 0
    assert connection.execute("select photo from customers limit 1").fetchone() == (b"",)  # Binary values can't be checked.
    assert connection.execute("select notes from customers limit 1").fetchone() == ("regular",)
    assert connection.execute("select count(*) from links").fetchone() == (300,)
    names = [row[0] for row in connection.execute("select name from customers")]
    assert not any(n.startswith("Person Number") for n in names)
    assert connection.execute("select count(*) from sqlite_master where type='trigger'").fetchone() == (0,)
    assert any("trigger" in note for note in report.notes)
    connection.close()
    assert render(extract(target), "text") == render(extract(source), "text")


def test_sqlite_overrides_can_be_for_one_table(tmp_path):
    source = make_database(tmp_path)
    target, _ = run_file(tmp_path, source, "sqlite", output="o.db", overrides={"customers.notes": "category"})
    connection = sqlite3.connect(target)
    assert connection.execute("select notes from customers limit 1").fetchone() != ("regular",)
    connection.close()


def test_a_bad_database_or_file_is_a_clear_error(tmp_path):
    with pytest.raises(SourceError):
        run_file(tmp_path, write(tmp_path, "x.db", "not a database"), "sqlite")
    with pytest.raises(SourceError, match="valid JSON"):
        run_file(tmp_path, write(tmp_path, "x.json", "{oops"), "json")
    with pytest.raises(SourceError, match="UTF-8"):
        (tmp_path / "b.csv").write_bytes(b"a,b\n\xff\xfe,1\n")
        run_file(tmp_path, str(tmp_path / "b.csv"), "csv", output="b_out")


# --- Command line -------------------------------------------------------------------------------------------


def test_cli_plan_shows_the_decisions_and_writes_nothing(tmp_path, capsys):
    source = write(tmp_path, "c.csv", CUSTOMERS)
    assert main(["pseudonymize", source, "--plan"]) == 0
    err = capsys.readouterr().err
    assert "customer_id  id" in err.replace("  ", "  ") and "email" in err and "Nothing was written" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.csv"]


def test_cli_writes_a_copy_reports_what_changed_and_keeps_a_key_for_next_time(tmp_path, capsys):
    source = write(tmp_path, "c.csv", CUSTOMERS)
    key = tmp_path / "pseudonyms.key"
    assert main(["pseudonymize", source, "-o", str(tmp_path / "a.csv"), "--key-file", str(key)]) == 0
    err = capsys.readouterr().err
    assert ("Wrote 2 rows" in err and "Replaced:" in err and "kept as they are" in err.lower()) or "Kept as they are" in err
    assert key.read_text(encoding="utf-8").strip() not in err
    assert main(["pseudonymize", source, "-o", str(tmp_path / "b.csv"), "--key-file", str(key)]) == 0
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()  # Same key, same fakes.
    assert main(["pseudonymize", source, "-o", str(tmp_path / "c2.csv")]) == 0
    assert "no --key-file" in capsys.readouterr().err
    assert (tmp_path / "c2.csv").read_bytes() != (tmp_path / "a.csv").read_bytes()


@pytest.mark.needs_model
def test_cli_mapping_overrides_strict_and_text(tmp_path, capsys):
    source = write(tmp_path, "c.csv", CUSTOMERS.replace("pro", "Jessica Whitfield referred").replace("free", "plain"))
    mapping = tmp_path / "m.mapping.json"
    assert main(["pseudonymize", source, "-o", str(tmp_path / "o.csv"), "--mapping", str(mapping), "--column", "plan=text", "--strict"]) == 0
    capsys.readouterr()
    pairs = json.loads(mapping.read_text(encoding="utf-8"))
    assert "Jessica Whitfield" in pairs["name"].values() and "1001" in pairs["id"].values()
    assert "<PERSON_1> referred" in (tmp_path / "o.csv").read_text(encoding="utf-8")


def test_cli_errors(tmp_path, capsys):
    source = write(tmp_path, "c.csv", CUSTOMERS)
    assert main(["pseudonymize", source]) == 1 and "-o OUTPUT" in capsys.readouterr().err
    assert main(["pseudonymize", write(tmp_path, "d.dat", "x"), "-o", str(tmp_path / "o")]) == 1
    assert "can't tell the input type" in capsys.readouterr().err
    assert main(["pseudonymize", source, "-o", source]) == 1
    assert main(["pseudonymize", source, "-o", str(tmp_path / "o.csv"), "--column", "x=nope"]) == 1
    assert "unknown kind" in capsys.readouterr().err
    assert main(["pseudonymize", str(tmp_path / "missing.csv"), "-o", str(tmp_path / "m.csv")]) == 1
    assert main(["pseudonymize", source, "-o", str(tmp_path / "ok.csv"), "--mapping", source]) == 1


def test_foreign_key_with_a_personal_looking_name_still_follows_its_parent(tmp_path):
    path = tmp_path / "blog.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table users (id integer primary key, email text);
        create table posts (id integer primary key, author integer references users(id));
        """
    )
    for i in range(1, 21):
        connection.execute("insert into users values (?,?)", (i, f"user{i}@acme-corp.io"))
        connection.execute("insert into posts values (?,?)", (i, i))
    connection.commit()
    connection.close()
    target, _ = run_file(tmp_path, str(path), "sqlite", output="copy.db")
    copy = sqlite3.connect(target)
    assert copy.execute("pragma foreign_key_check").fetchall() == []
    assert copy.execute("select count(*) from posts join users on users.id = posts.author").fetchone() == (20,)


def test_pseudonymize_reads_and_writes_a_custom_delimiter(tmp_path):
    source = tmp_path / "people.csv"
    source.write_text("customer_id;name;email\n1;Jessica Whitfield;jessica@acme-corp.io\n", encoding="utf-8")
    target, _ = run_file(tmp_path, str(source), "csv", delimiter=";")
    text = open(target, encoding="utf-8").read()
    assert text.splitlines()[0] == "customer_id;name;email"
    assert "Jessica" not in text and len(text.splitlines()[1].split(";")) == 3
