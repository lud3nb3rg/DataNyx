"""The eval recall gate and the SQL and pandas checks."""

import json

import pytest

from datanyx.check import check_code
from datanyx.check.model import Column, Table
from datanyx.cli import main
from datanyx.evaluation import EvalReport, KindResult

# --- Eval and checks -----------------------------------------------------------------------------------------------------


def test_a_kind_nobody_labelled_cant_pass_the_recall_gate(tmp_path, capsys):
    path = tmp_path / "e.json"
    path.write_text(json.dumps([{"text": "Hello Jane Doe", "sensitive": {"PERSON": ["Jane Doe"]}}]), encoding="utf-8")
    assert main(["--no-config", "eval", str(path), "--no-ner", "--kind", "PERSNO", "--min-recall", "0.9"]) == 1
    assert "nothing in the file is labelled PERSNO" in capsys.readouterr().err


def test_nothing_measured_is_no_pass():
    assert EvalReport().below(0.5) == ["overall"]
    assert EvalReport(kinds={"A": KindResult(total=0)}).below(0.5) == ["overall"]
    assert EvalReport(kinds={"A": KindResult(total=1, hidden=1)}).below(0.5) == []


def sql_warnings(sql, **columns):
    table = Table("t", {name: Column(kind) for name, kind in columns.items()})
    return [item.message for item in check_code(sql, [table], "sql")]


def test_a_time_column_takes_a_time():
    pytest.importorskip("sqlglot")
    for value in ("12:30:00", "12:30", "23:59:59.5", "9:15 PM"):
        assert sql_warnings(f"select * from t where at_time > '{value}'", at_time="time") == [], value
    assert any("isn't a time" in message for message in sql_warnings("select * from t where at_time > 'noon'", at_time="time"))


def test_a_date_column_still_takes_a_date():
    pytest.importorskip("sqlglot")
    assert sql_warnings("select * from t where d > '2024-01-31'", d="date") == []
    assert any("isn't a date" in message for message in sql_warnings("select * from t where d > '12:30:00'", d="date"))


def test_aggregates_over_distinct_are_type_checked():
    pytest.importorskip("sqlglot")
    assert any("SUM over string" in message for message in sql_warnings("select sum(distinct name) from t", name="string"))
    assert sql_warnings("select sum(distinct n) from t", n="integer") == []


PANDAS_TABLES = [
    Table("l", {"id": Column("integer"), "key": Column("string")}, aliases=frozenset({"l", "l.csv"})),
    Table("r", {"val": Column("integer")}, aliases=frozenset({"r", "r.csv"})),
]


def python_findings(code):
    return check_code(code, PANDAS_TABLES, "python")


def test_join_on_is_a_column_of_the_left_frame_only():
    code = 'import pandas as pd\nleft = pd.read_csv("l.csv")\nright = pd.read_csv("r.csv")\nx = left.join(right, on="{}")\n'
    assert python_findings(code.format("key")) == []
    assert any("nope" in item.message for item in python_findings(code.format("nope")))


def test_merge_on_is_still_checked_against_both():
    code = 'import pandas as pd\nleft = pd.read_csv("l.csv")\nright = pd.read_csv("r.csv")\nx = left.merge(right, on="key")\n'
    assert any("isn't in 'r'" in item.message for item in python_findings(code))


def test_attribute_checks_need_no_pandas(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pandas(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pandas)
    code = 'import pandas as pd\ndf = pd.read_csv("l.csv")\ndf.head()\nprint(df.shape, df.key, df.typo)\n'
    messages = [item.message for item in python_findings(code)]
    assert len(messages) == 1 and "typo" in messages[0]


def test_every_public_pandas_attribute_is_known():
    pandas = pytest.importorskip("pandas")
    from datanyx.check.python_check import _DATAFRAME_ATTRIBUTES

    missing = {name for name in dir(pandas.DataFrame) if not name.startswith("_")} - _DATAFRAME_ATTRIBUTES
    assert not missing
