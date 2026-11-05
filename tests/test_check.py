"""The checker finds name and type mistakes in SQL and pandas code, and reveals nothing hidden."""

import json
import sqlite3

import pytest

from datanyx.check import check_code, format_diagnostics, load_tables
from datanyx.cli import main
from datanyx.sources import SourceError

pytest.importorskip("sqlglot")

CANARY = "zq-canary-value"
CUSTOMERS = "id,email,signed_up,plan,lifetime_value,notes\n1,a@x.com,2024-01-02,pro,129.5,hi\n2,b@x.com,2024-02-03,free,0,\n"
ORDERS = "id,customer_id,total,placed\n1,1,10.5,2024-01-05\n2,2,7.25,2024-01-06\n"


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "customers.csv").write_text(CUSTOMERS, encoding="utf-8")
    (tmp_path / "orders.csv").write_text(ORDERS, encoding="utf-8")
    return tmp_path


@pytest.fixture
def tables(folder):
    return load_tables([str(folder)])[0]


def sql(code, tables, **options):
    return check_code(code, tables, "sql", **options)


def messages(findings):
    return [item.message for item in findings]


# --- SQL: names ----------------------------------------------------------------


def test_unknown_column_is_an_error_with_position_and_suggestion(tables):
    findings = sql("SELECT id,\n       emial\nFROM customers.csv", tables)
    assert [(f.severity, f.line, f.column, f.hint) for f in findings] == [("error", 2, 8, "did you mean 'email'?")]
    assert messages(findings) == ["unknown column 'emial'"]


def test_tables_answer_to_their_stem_and_file_name(tables):
    assert sql("SELECT id FROM customers", tables) == []
    assert sql('SELECT id FROM "customers.csv"', tables) == []


def test_unknown_table_lists_the_known_ones_and_skips_its_columns(tables):
    findings = sql("SELECT x.a, b FROM custmers x", tables)
    assert messages(findings) == ["unknown table 'custmers'"]
    assert findings[0].hint == "did you mean 'customers.csv' or 'orders.csv'?" or "customers" in findings[0].hint


def test_qualified_columns_and_aliases(tables):
    assert sql("SELECT c.id, o.total FROM customers c JOIN orders o ON o.customer_id = c.id", tables) == []
    assert messages(sql("SELECT c.emial FROM customers c", tables)) == ["unknown column 'emial' in 'customers.csv'"]
    assert messages(sql("SELECT z.id FROM customers c", tables)) == ["unknown table or alias 'z'"]


def test_ambiguous_columns_in_a_join(tables):
    findings = sql("SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id", tables)
    assert len(findings) == 1 and "ambiguous" in findings[0].message and findings[0].hint


def test_aliases_ctes_subqueries_and_stars_are_understood(tables):
    assert sql("SELECT plan, COUNT(*) AS n FROM customers GROUP BY plan ORDER BY n", tables) == []
    assert sql("WITH t AS (SELECT id, plan FROM customers) SELECT id, plan FROM t", tables) == []
    assert sql("SELECT s.plan FROM (SELECT plan FROM customers) s", tables) == []
    assert sql("SELECT * FROM customers", tables) == []
    assert sql("SELECT t.* FROM customers t", tables) == []
    assert messages(sql("WITH t AS (SELECT id FROM customers) SELECT zip FROM t", tables)) == ["unknown column 'zip'"]
    assert sql("SELECT id FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)", tables) == []


def test_several_statements_and_syntax_errors(tables):
    findings = sql("SELECT emial FROM customers;\nSELECT totl FROM orders", tables)
    assert [f.line for f in findings] == [1, 2]
    broken = sql("SELEC id FROM", tables)
    assert broken[0].severity == "error" and "syntax" in broken[0].message


def test_update_delete_and_insert_columns_are_checked(tables):
    assert messages(sql("UPDATE customers SET emial = 'x' WHERE id = 1", tables)) == ["unknown column 'emial' in 'customers.csv'"]
    assert messages(sql("DELETE FROM customers WHERE plna = 'x'", tables)) == ["unknown column 'plna' in 'customers.csv'"]
    assert messages(sql("INSERT INTO customers (id, emial) VALUES (1, 'x')", tables)) == ["unknown column 'emial' in 'customers.csv'"]


# --- SQL: types ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        ("SELECT id FROM customers WHERE lifetime_value = 'lots'", "with the text"),
        ("SELECT id FROM customers WHERE plan = 3", "comparing text column plan with the number 3"),
        ("SELECT id FROM customers WHERE signed_up > 5", "date column signed_up with the number 5"),
        ("SELECT id FROM customers WHERE signed_up > 'soon'", "isn't a date"),
        ("SELECT SUM(email) FROM customers", "SUM over string column"),
        ("SELECT AVG(signed_up) FROM customers", "AVG over date column"),
        ("SELECT id FROM customers WHERE notes = NULL", "NULL"),
        ("SELECT id FROM customers WHERE id LIKE '1%'", "LIKE on integer"),
        ("SELECT c.id FROM customers c JOIN orders o ON c.email = o.customer_id", "comparing"),
    ],
)
def test_type_mistakes_are_warnings(tables, query, fragment):
    findings = sql(query, tables)
    assert findings and all(f.severity == "warning" for f in findings)
    assert any(fragment in f.message for f in findings)


def test_sensible_comparisons_are_left_alone(tables):
    for query in (
        "SELECT id FROM customers WHERE lifetime_value > 100",
        "SELECT id FROM customers WHERE lifetime_value > '100'",
        "SELECT id FROM customers WHERE signed_up >= '2024-01-01'",
        "SELECT id FROM customers WHERE plan = 'pro' AND notes IS NULL",
        "SELECT SUM(lifetime_value), AVG(lifetime_value) FROM customers",
    ):
        assert sql(query, tables) == [], query


# --- Hidden names -----------------------------------------------------------------


def test_hidden_columns_make_unknown_names_warnings_not_errors(tmp_path):
    data = [{"id": i, f"{CANARY}@x.com": i} for i in range(8)]
    path = tmp_path / "events.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    tables = load_tables([str(path)])[0]
    assert tables[0].open
    guess = sql("SELECT nope FROM events", tables)
    assert [f.severity for f in guess] == ["warning"] and "hides some column names" in guess[0].message


def test_a_hidden_name_looks_exactly_like_one_that_doesnt_exist(tmp_path):
    hidden = f"{CANARY}@x.com"
    data = [{"id": i, hidden: i} for i in range(8)]
    path = tmp_path / "events.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    tables = load_tables([str(path)])[0]
    real = sql(f'SELECT "{hidden}" FROM events', tables)
    fake = sql('SELECT "other@x.com" FROM events', tables)
    assert [(f.severity, f.hint) for f in real] == [(f.severity, f.hint) for f in fake]
    assert real[0].message.replace(hidden, "NAME") == fake[0].message.replace("other@x.com", "NAME")
    # Suggestions never come from hidden names.
    close = sql(f'SELECT "{CANARY}@y.com" FROM events', tables)
    assert all(CANARY not in (f.hint or "") for f in close)


def test_output_never_contains_values_from_the_data(tmp_path):
    path = tmp_path / "people.json"
    path.write_text(json.dumps([{"name": f"{CANARY} Person", "age": 41}] * 6), encoding="utf-8")
    tables = load_tables([str(path)])[0]
    findings = sql("SELECT nmae, age FROM people WHERE age = 'x'", tables)
    assert findings and CANARY not in format_diagnostics(findings)


# --- Table names for files --------------------------------------------------------


def test_a_file_with_a_sensitive_name_is_called_data_but_still_answers_to_its_name(tmp_path):
    path = tmp_path / "jane doe medical.csv"
    path.write_text("id,plan\n1,x\n", encoding="utf-8")
    tables = load_tables([str(path)])[0]
    assert tables[0].name == "data"
    assert sql('SELECT id FROM "jane doe medical"', tables) == []
    unknown = sql("SELECT id FROM nothing", tables)
    assert "jane" not in format_diagnostics(unknown)


def test_table_option_names_a_single_file(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text("id,plan\n1,x\n", encoding="utf-8")
    tables = load_tables([str(path)], ["clients"])[0]
    assert tables[0].name == "clients" and sql("SELECT plan FROM clients", tables) == []


def test_database_types_drive_the_type_checks_and_dialect(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "create table users (id integer primary key, email text not null);"
        "create table posts (id integer primary key, author integer references users(id), title text);"
    )
    connection.close()
    tables, dialect = load_tables([str(path)])
    assert dialect == "sqlite"
    assert sql("SELECT u.id FROM users u JOIN posts p ON p.author = u.id", tables, dialect=dialect) == []
    findings = sql("SELECT u.id FROM users u JOIN posts p ON p.title = u.id", tables, dialect=dialect)
    assert findings and "comparing" in findings[0].message


def test_unknown_dialect_is_an_error(tables):
    with pytest.raises(SourceError):
        sql("SELECT 1", tables, dialect="not-a-dialect")


# --- pandas -------------------------------------------------------------------------


def py(code, tables):
    return check_code(code, tables, "python")


def test_column_names_in_subscripts_attributes_and_methods(tables):
    pytest.importorskip("pandas")
    code = """
import pandas as pd
df = pd.read_csv("customers.csv")
a = df["emial"]
b = df[["id", "plna"]]
c = df.lifetime_valu
d = df.groupby("Plan")["lifetime_value"].sum()
e = df.sort_values("signed_upp")
f = df.drop(columns=["notess"])
g = df.merge(df, on="idd")
h = df.rename(columns={"emial": "e"})
i = df.shape, df.columns, df.head(), df["email"], df.plan, df[["id", "plan"]]
"""
    found = py(code, tables)
    flagged = [f.message.split("'")[1] for f in found]
    assert flagged == ["emial", "plna", "lifetime_valu", "Plan", "signed_upp", "notess", "idd", "idd", "emial"]
    assert all(f.severity == "error" and f.line > 3 for f in found)
    assert next(f for f in found if "'Plan'" in f.message).hint == "did you mean 'plan'? Names are case-sensitive here"


def test_columns_the_script_adds_are_not_flagged(tables):
    code = """
import pandas as pd
df = pd.read_csv("customers.csv")
df["total"] = df["lifetime_value"] * 2
df[["x", "y"]] = 1
print(df["total"], df["x"], df["y"])
df["total"] += 1
"""
    assert py(code, tables) == []


def test_frames_are_followed_through_filters_copies_and_subsets_only(tables):
    code = """
import pandas as pd
df = pd.read_csv("customers.csv")
paid = df[df["plan"] == "pro"].copy()
print(paid["emial"])
view = df[["id", "plan"]]
print(view["email"])
merged = df.merge(df, on="id")
print(merged["anything"])
df = something_else()
print(df["whatever"])
"""
    found = py(code, tables)
    assert [(f.line, f.message.split("'")[1]) for f in found] == [(5, "emial"), (7, "email")]


def test_files_the_schema_does_not_cover_are_not_checked(tables):
    code = 'import pandas as pd\nother = pd.read_csv("unrelated.csv")\nprint(other["anything"])\n'
    assert py(code, tables) == []


def test_reader_imported_by_name_and_excel_style_calls(tables):
    code = 'from pandas import read_csv as load\ndf = load("data/customers.csv", sep=",")\nprint(df["emial"])\n'
    assert len(py(code, tables)) == 1


def test_pandas_type_mistakes(tables):
    pytest.importorskip("pandas")
    code = """
import pandas as pd
df = pd.read_csv("customers.csv")
df["plan"].mean()
df["signed_up"].dt.year
df["lifetime_value"].str.upper()
df[df["plan"] == 3]
df[df["lifetime_value"] > "10"]
df["lifetime_value"].mean()
df["plan"] == "pro"
"""
    found = py(code, tables)
    assert [f.line for f in found] == [4, 5, 6, 7, 8]
    assert all(f.severity == "warning" for f in found)
    assert "always False" in found[3].message


def test_dt_is_fine_after_converting(tables):
    code = """
import pandas as pd
df = pd.read_csv("customers.csv")
df["signed_up"] = pd.to_datetime(df["signed_up"])
df["signed_up"].dt.year
"""
    assert py(code, tables) == []


def test_sql_strings_inside_python_are_checked_with_their_line(tables):
    code = (
        "import pandas as pd\n"
        "rows = pd.read_sql(\n"
        '    """SELECT id,\n'
        "       emial\n"
        '    FROM customers""",\n'
        "    None,\n"
        ")\n"
        'conn.execute(f"SELECT {x} FROM customers")\n'
        'conn.execute("SELECT nope FROM customers")\n'
    )
    found = py(code, tables)
    assert [(f.line, f.message) for f in found] == [(4, "unknown column 'emial'"), (9, "unknown column 'nope'")]


def test_sql_strings_are_skipped_with_a_note_without_sqlglot(tables, monkeypatch):
    monkeypatch.setattr("datanyx.check.python_check.sqlglot_available", lambda: False)
    found = py('conn.execute("SELECT 1")\n', tables)
    assert [f.severity for f in found] == ["warning"] and "datanyx[check]" in found[0].message


def test_python_syntax_errors_and_unknown_language(tables):
    assert py("def broken(:\n", tables)[0].severity == "error"
    with pytest.raises(SourceError):
        check_code("x", tables, "ruby")


# --- Command line ---------------------------------------------------------------------


def run(args, capsys):
    code = main(args)
    return code, capsys.readouterr().out


def test_cli_exit_codes_and_output(folder, tmp_path, capsys):
    good = tmp_path / "good.sql"
    good.write_text("SELECT id FROM customers", encoding="utf-8")
    assert run(["check", str(good), "--against", str(folder)], capsys) == (0, "No problems found.\n")

    bad = tmp_path / "bad.sql"
    bad.write_text("SELECT emial FROM customers", encoding="utf-8")
    code, out = run(["check", str(bad), "--against", str(folder)], capsys)
    assert code == 1 and f"{bad}:1:8: error: unknown column 'emial'" in out and "1 error, 0 warnings" in out


def test_cli_warnings_only_fail_with_strict(folder, tmp_path, capsys):
    query = tmp_path / "q.sql"
    query.write_text("SELECT id FROM customers WHERE plan = 3", encoding="utf-8")
    assert run(["check", str(query), "--against", str(folder)], capsys)[0] == 0
    assert run(["check", str(query), "--against", str(folder), "--strict"], capsys)[0] == 1


def test_cli_json_format_and_python_files(folder, tmp_path, capsys):
    script = tmp_path / "s.py"
    script.write_text('import pandas as pd\ndf = pd.read_csv("customers.csv")\ndf["emial"]\n', encoding="utf-8")
    code, out = run(["check", str(script), "--against", str(folder / "customers.csv"), "--format", "json"], capsys)
    findings = json.loads(out)
    assert code == 1 and findings[0]["line"] == 3 and findings[0]["hint"] == "did you mean 'email'?"


def test_cli_needs_a_language_for_stdin(folder, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"SELECT emial FROM customers"), encoding="utf-8"))
    assert main(["check", "-", "--against", str(folder)]) == 1
    assert "--language" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"SELECT emial FROM customers"), encoding="utf-8"))
    code, out = run(["check", "-", "--language", "sql", "--against", str(folder)], capsys)
    assert code == 1 and "<stdin>:1:8" in out


# --- MCP tool ---------------------------------------------------------------------------


def test_mcp_check_code_tool(folder):
    pytest.importorskip("mcp")
    import asyncio

    from datanyx.mcp_server import create_server

    server = create_server([folder])

    async def call(arguments):
        return await server.call_tool("check_code", arguments)

    target = str(folder / "customers.csv")
    text = asyncio.run(call({"code": "SELECT emial FROM customers", "target": target})).content[0].text
    assert "unknown column 'emial'" in text and "did you mean 'email'?" in text
    assert asyncio.run(call({"code": "SELECT id FROM customers", "target": target})).content[0].text == "No problems found."
    with pytest.raises(Exception, match="outside"):
        asyncio.run(call({"code": "SELECT 1", "target": str(folder.parent / "x.csv")}))
