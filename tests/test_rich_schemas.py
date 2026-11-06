"""Scale hints and inferred keys: opt-in, coarse, flag-only, and never a value or a count."""

import json
import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from datanyx import extract, render
from datanyx.cli import main
from datanyx.privacy import KeyPolicy
from datanyx.scale import SMALL, scale_label

CANARY = "zq-canary"
HINTS = KeyPolicy(scale_hints=True)
KEYS = KeyPolicy(infer_keys=True)
BOTH = KeyPolicy(scale_hints=True, infer_keys=True)
FORMATS = ("json-schema", "text", "typescript", "pydantic", "sql")


def everything(path, policy=BOTH, **options):
    schema = extract(str(path), policy=policy, **options)
    return "\n".join(render(schema, name) for name in FORMATS) + "\n".join(schema.notes)


def write_csv(path, header, rows):
    path.write_text(header + "\n" + "".join(f"{row}\n" for row in rows), encoding="utf-8")
    return path


@pytest.fixture
def shop(tmp_path):
    """customers(id), orders(customer_id -> customers.id), events with ids that aren't customers'."""
    write_csv(tmp_path / "customers.csv", "id,email,plan", [f"{i},c{i}@zq.com,pro" for i in range(1, 31)])
    write_csv(tmp_path / "orders.csv", "order_id,customer_id,total", [f"{100 + i},{i % 30 + 1},9.5" for i in range(1, 41)])
    write_csv(tmp_path / "events.csv", "event_id,customer_id", [f"{i},{i % 5 + 1}" for i in range(1, 25)])
    return tmp_path


# --- the buckets ---------------------------------------------------------------------------------------------


def test_buckets_are_decades_and_everything_small_is_one_bucket():
    assert scale_label(0) == scale_label(1) == scale_label(999) == SMALL
    assert scale_label(1000) == scale_label(9999) == "10^3 to 10^4 rows"
    assert scale_label(10_000) == "10^4 to 10^5 rows"
    assert scale_label(2_500_000) == "10^6 to 10^7 rows"
    assert scale_label(10**9) == "10^9 to 10^10 rows"


# --- off by default ------------------------------------------------------------------------------------------


def test_nothing_is_added_unless_asked(shop):
    schema = extract(str(shop))
    assert all(e.scale is None and not e.foreign_keys for e in schema.entities)
    text = "\n".join(render(schema, name) for name in FORMATS)
    assert "x-scale" not in text and "x-unique" not in text and "inferred" not in text and "scale:" not in text


# --- scale hints ---------------------------------------------------------------------------------------------


def scale_of(schema, name=None):
    return next(e.scale for e in schema.entities if e.name == name)


def test_csv_jsonl_json_and_yaml_report_a_bucket_not_a_count(tmp_path):
    rows = [f"{i},x" for i in range(1, 1501)]
    csv_path = write_csv(tmp_path / "t.csv", "id,name", rows)
    assert scale_of(extract(str(csv_path), policy=HINTS)) == "10^3 to 10^4 rows"
    jsonl = tmp_path / "t.jsonl"
    jsonl.write_text("".join(json.dumps({"id": i}) + "\n" for i in range(1, 1501)), encoding="utf-8")
    assert scale_of(extract(str(jsonl), policy=HINTS)) == "10^3 to 10^4 rows"
    document = tmp_path / "t.json"
    document.write_text(json.dumps([{"id": i} for i in range(1, 51)]), encoding="utf-8")
    assert scale_of(extract(str(document), policy=HINTS)) == SMALL
    out = render(extract(str(csv_path), policy=HINTS), "json-schema")
    assert json.loads(out)["x-scale"] == "10^3 to 10^4 rows"
    assert "1500" not in everything(csv_path, HINTS)


def test_yaml_and_excel_and_parquet_report_a_bucket(tmp_path):
    yaml = pytest.importorskip("yaml")
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump([{"id": i} for i in range(1, 2001)]), encoding="utf-8")
    assert scale_of(extract(str(path), policy=HINTS)) == "10^3 to 10^4 rows"

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Big"
    sheet.append(["id"])
    for i in range(1, 1201):
        sheet.append([i])
    small = book.create_sheet("Small")
    small.append(["id"])
    small.append([1])
    book.save(tmp_path / "t.xlsx")
    schema = extract(str(tmp_path / "t.xlsx"), policy=HINTS)
    assert {e.name: e.scale for e in schema.entities} == {"Big": "10^3 to 10^4 rows", "Small": SMALL}

    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pq.write_table(pa.table({"id": list(range(12_000))}), tmp_path / "t.parquet")
    assert scale_of(extract(str(tmp_path / "t.parquet"), policy=HINTS)) == "10^4 to 10^5 rows"


def test_duckdb_and_mongodb_use_the_catalogs_own_estimate(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "t.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create table big as select range as id from range(5000)")
    con.close()
    schema = extract(str(path), policy=HINTS)
    assert scale_of(schema, "big") == "10^3 to 10^4 rows"

    mongomock = pytest.importorskip("mongomock")
    from datanyx.sources import mongodb_source

    client = mongomock.MongoClient("mongodb://localhost/shop")
    client["shop"]["users"].insert_many([{"n": i} for i in range(1500)])
    monkeypatch.setattr(mongodb_source, "_client", lambda url: client)
    assert scale_of(extract("mongodb://localhost/shop", policy=HINTS), "users") == "10^3 to 10^4 rows"
    # Documents read for the types are capped at a sample, but the size isn't taken from the sample.


def test_sqlite_says_it_cannot_give_a_hint(tmp_path):
    import sqlite3

    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute("create table t(id integer primary key)")
    con.commit()
    con.close()
    schema = extract(str(path), policy=HINTS)
    assert all(e.scale is None for e in schema.entities)
    assert any("row-count hints" in note for note in schema.notes)


def test_every_format_shows_the_bucket(tmp_path):
    path = write_csv(tmp_path / "t.csv", "id", [str(i) for i in range(1, 2001)])
    schema = extract(str(path), policy=HINTS)
    for name in FORMATS:
        assert "10^3 to 10^4" in render(schema, name), name


# --- inferred keys -------------------------------------------------------------------------------------------


def test_unique_columns_and_joins_are_inferred_from_names_and_values(shop):
    schema = extract(str(shop), policy=KEYS)
    by_name = {e.name: e for e in schema.entities}
    fields = {name: entity.node.items.fields for name, entity in by_name.items()}
    assert fields["customers.csv"]["id"].unique
    assert fields["orders.csv"]["order_id"].unique and fields["events.csv"]["event_id"].unique
    assert not fields["customers.csv"]["email"].unique  # Not a key-looking name.
    assert not fields["orders.csv"]["customer_id"].unique  # Repeats.
    for name in ("orders.csv", "events.csv"):
        (key,) = by_name[name].foreign_keys
        assert (key.columns, key.referenced_entity, key.referenced_columns, key.inferred) == (("customer_id",), "customers.csv", ("id",), True)
    assert not by_name["customers.csv"].foreign_keys
    assert any(note.startswith("inferred 3 unique column(s) and 2 join(s)") for note in schema.notes)


def test_inferred_keys_are_marked_as_guesses_in_every_format(shop):
    schema = extract(str(shop), policy=KEYS)
    document = json.loads(render(schema, "json-schema"))["$defs"]
    assert document["orders.csv"]["x-foreign-keys"][0]["inferred"] is True
    assert document["customers.csv"]["items"]["properties"]["id"]["x-unique"] is True
    assert "inferred from the values" in render(schema, "text") and "unique (inferred)" in render(schema, "text")
    sql = render(schema, "sql")
    assert "FOREIGN KEY" not in sql and "-- inferred join (not enforced)" in sql and "-- inferred unique (not enforced)" in sql
    assert "inferred join" in render(schema, "typescript") and "unique (inferred)" in render(schema, "pydantic")


def test_small_tables_dirty_columns_and_weak_names_give_no_flags(tmp_path):
    write_csv(tmp_path / "few.csv", "id", [str(i) for i in range(1, 20)])  # 19 rows
    write_csv(tmp_path / "dup.csv", "id", [str(i % 25) for i in range(1, 41)])  # Repeats
    write_csv(tmp_path / "nulls.csv", "id,x", [f"{i}," if i else ",y" for i in range(25)])  # A missing id
    write_csv(tmp_path / "plain.csv", "name,value", [f"n{i},{i}" for i in range(1, 40)])  # No key-looking name
    schema = extract(str(tmp_path), policy=KEYS)
    assert not any(f.unique for e in schema.entities for f in e.node.items.fields.values())
    assert not any(e.foreign_keys for e in schema.entities)


def test_a_join_needs_the_names_to_match_and_every_value_to_be_contained(tmp_path):
    write_csv(tmp_path / "customers.csv", "id", [str(i) for i in range(1, 31)])
    write_csv(tmp_path / "things.csv", "thing_id,owner", [f"{i},{i}" for i in range(1, 31)])  # `thing_id` isn't customers'
    write_csv(tmp_path / "orders.csv", "order_id,customer_id", [f"{i},{i + 5}" for i in range(1, 31)])  # 31..35 aren't customers
    write_csv(tmp_path / "plain.csv", "id,customer_id", [f"{i},{i % 30 + 1}" for i in range(1, 31)])  # The generic `id` isn't matched
    schema = extract(str(tmp_path), policy=KEYS)
    by_name = {e.name: e for e in schema.entities}
    assert not by_name["things.csv"].foreign_keys  # Values fit, names don't.
    assert not by_name["orders.csv"].foreign_keys  # Names fit, values don't.
    assert [k.referenced_entity for k in by_name["plain.csv"].foreign_keys] == ["customers.csv"]


def test_two_tables_holding_each_others_key_are_a_tie_not_a_join(tmp_path):
    write_csv(tmp_path / "accounts.csv", "account_id", [str(i) for i in range(1, 31)])
    write_csv(tmp_path / "profiles.csv", "account_id,name", [f"{i},n" for i in range(1, 31)])
    schema = extract(str(tmp_path), policy=KEYS)
    assert not any(e.foreign_keys for e in schema.entities)


def test_sheets_of_one_workbook_and_files_in_subfolders_are_joined(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    users = book.active
    users.title = "users"
    users.append(["user_id", "name"])
    for i in range(1, 31):
        users.append([i, "n"])
    orders = book.create_sheet("orders")
    orders.append(["order_id", "user_id"])
    for i in range(1, 31):
        orders.append([i, i % 10 + 1])
    book.save(tmp_path / "shop.xlsx")
    schema = extract(str(tmp_path / "shop.xlsx"), policy=KEYS)
    by_name = {e.name: e for e in schema.entities}
    assert [(k.columns, k.referenced_entity) for k in by_name["orders"].foreign_keys] == [(("user_id",), "users")]

    nested = tmp_path / "data"
    nested.mkdir()
    write_csv(nested / "accounts.csv", "account_id", [str(i) for i in range(1, 31)])
    write_csv(nested / "payments.csv", "payment_id,account_id", [f"{i},{i % 10 + 1}" for i in range(1, 31)])
    folder = extract(str(nested), policy=KEYS)
    assert [(k.columns, k.referenced_entity) for e in folder.entities for k in e.foreign_keys] == [(("account_id",), "accounts.csv")]


def test_a_json_array_and_json_lines_feed_the_same_inference(tmp_path):
    (tmp_path / "users.json").write_text(json.dumps([{"id": i, "n": "x"} for i in range(1, 31)]), encoding="utf-8")
    (tmp_path / "posts.jsonl").write_text("".join(json.dumps({"post_id": i, "user_id": i}) + "\n" for i in range(1, 31)), encoding="utf-8")
    schema = extract(str(tmp_path), policy=KEYS)
    posts = next(e for e in schema.entities if e.name == "posts.jsonl")
    assert [(k.columns, k.referenced_entity) for k in posts.foreign_keys] == [(("user_id",), "users.json")]


def test_a_source_that_cannot_infer_says_so(tmp_path):
    import sqlite3

    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute("create table t(id integer primary key)")
    con.commit()
    con.close()
    schema = extract(str(path), policy=KEYS)
    assert any("no unique columns or joins were inferred" in note for note in schema.notes)


def test_inferred_keys_drive_the_synthetic_data(shop, capsys, tmp_path):
    out = tmp_path / "fake"
    assert main(["synth", str(shop), "--infer-keys", "--rows", "30", "--format", "jsonl", "-o", str(out)]) == 0
    capsys.readouterr()
    rows = {p.stem: [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] for p in out.iterdir()}
    ids = {row["id"] for row in rows["customers_csv"]}
    assert len(ids) == len(rows["customers_csv"])  # Unique.
    assert {row["customer_id"] for row in rows["orders_csv"]} <= ids  # Points at real rows.


# --- no values, no counts ------------------------------------------------------------------------------------


def test_key_values_never_reach_the_output(tmp_path):
    write_csv(tmp_path / "a.csv", "id,note", [f"{CANARY}-{i:03d},{CANARY}" for i in range(40)])
    write_csv(tmp_path / "b.csv", "a_id", [f"{CANARY}-{i:03d}" for i in range(40)])
    text = everything(tmp_path)
    assert CANARY not in text and "-039" not in text
    schema = extract(str(tmp_path), policy=BOTH)
    assert [k.referenced_entity for e in schema.entities for k in e.foreign_keys] == ["a.csv"]


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(values=st.lists(st.text(alphabet="abc-_ 12", max_size=6), min_size=20, max_size=60), rows=st.integers(0, 3000))
def test_flags_never_leak_whatever_the_values_are(tmp_path_factory, values, rows):
    folder = tmp_path_factory.mktemp("keys")
    (folder / "a.jsonl").write_text("".join(json.dumps({"id": CANARY + v, "x": CANARY}) + "\n" for v in values), encoding="utf-8")
    (folder / "b.jsonl").write_text("".join(json.dumps({"a_id": CANARY + v}) + "\n" for v in values), encoding="utf-8")
    assert CANARY not in everything(folder)


def test_the_only_numbers_a_hint_adds_are_exponents(tmp_path):
    path = write_csv(tmp_path / "t.csv", "id", [str(i) for i in range(1, 4568)])
    text = render(extract(str(path), policy=BOTH), "text")
    assert "4567" not in text and "4568" not in text
    assert re.search(r"scale: 10\^3 to 10\^4 rows", text)


# --- settings and the command line ---------------------------------------------------------------------------


def test_cli_flags_and_config_turn_them_on(shop, tmp_path, capsys):
    assert main(["--no-config", "schema", str(shop), "--format", "text"]) == 0
    assert "scale:" not in capsys.readouterr().out
    assert main(["--no-config", "schema", str(shop), "--format", "text", "--scale-hints", "--infer-keys"]) == 0
    out = capsys.readouterr().out
    assert "scale: fewer than 1,000 rows" in out and "foreign key (customer_id) -> customers.csv (id)  -- inferred" in out

    config = tmp_path / "datanyx.toml"
    config.write_text("[schema]\nscale_hints = true\ninfer_keys = true\n", encoding="utf-8")
    assert main(["--config", str(config), "schema", str(shop), "--format", "text"]) == 0
    assert "scale: fewer than 1,000 rows" in capsys.readouterr().out
    config.write_text("[schema]\nscale_hints = 1\n", encoding="utf-8")
    assert main(["--config", str(config), "schema", str(shop)]) == 1
    assert "true or false" in capsys.readouterr().err


def test_the_mcp_server_applies_the_owners_choice_and_the_agent_cannot_change_it(shop):
    pytest.importorskip("mcp")
    import asyncio

    from datanyx.mcp_server import create_server

    def describe(server):
        return str(asyncio.run(server.call_tool("describe_data", {"target": str(shop), "format": "text"})))

    assert "scale:" not in describe(create_server([shop]))
    hinted = describe(create_server([shop], scale_hints=True, infer_keys=True))
    assert "scale:" in hinted and "inferred" in hinted
    tool_names = {tool.name for tool in asyncio.run(create_server([shop]).list_tools())}
    assert "describe_data" in tool_names
    parameters = str(asyncio.run(create_server([shop]).list_tools()))
    assert "scale_hints" not in parameters and "infer_keys" not in parameters
