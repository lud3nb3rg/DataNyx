"""How a database is named on the command line, and what its failures reveal. Needs no server."""

import sqlite3
import sys
import warnings
from contextlib import closing

import pytest

from datanyx import SourceError, extract
from datanyx.cli import main

CANARY = "CANARY"


def make_db(tmp_path):
    path = tmp_path / "a.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE a (b INT)")
    return str(path)


def table_names(target):
    return {entity.name for entity in extract(target).entities}


def test_sqlite_url_is_read_only_too(tmp_path):
    assert table_names(f"sqlite:///{make_db(tmp_path)}") == {"a"}
    missing = tmp_path / "missing.db"
    with pytest.raises(SourceError, match="No such file"):
        extract(f"sqlite:///{missing}")
    assert not missing.exists()


def test_url_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DN_TEST_URL", f"sqlite:///{make_db(tmp_path)}")
    assert table_names("env:DN_TEST_URL") == {"a"}


def test_unset_environment_variable_is_reported_by_name(monkeypatch):
    monkeypatch.delenv("DN_TEST_URL", raising=False)
    with pytest.raises(SourceError, match="DN_TEST_URL"):
        extract("env:DN_TEST_URL")


@pytest.fixture(autouse=True)
def short_connect_timeout(monkeypatch):
    monkeypatch.setattr("datanyx.sources.sql_source._CONNECT_TIMEOUT_SECONDS", 1)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:CANARY@127.0.0.1:1/db",  # Nothing listening.
        "nosuchdb://user:CANARY@host/db",  # Unknown dialect.
        "postgresql://user:CANARY@host:notaport/db",  # Unparseable.
    ],
)
def test_connection_failures_never_show_the_password(url, capsys):
    assert main(["schema", url]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert CANARY not in captured.out + captured.err


def test_missing_driver_is_explained(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(SourceError, match=r"datanyx\[postgres\]"):
        extract("postgresql://u:p@127.0.0.1:1/db")


def test_library_warnings_are_not_passed_on(tmp_path, monkeypatch):
    def warn_like_sqlalchemy(*_args):
        warnings.warn("Did not recognize type 'x' of column 'CANARY@example.com'")
        return "described"

    monkeypatch.setattr("datanyx.sources.sql_source._describe", warn_like_sqlalchemy)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert extract(make_db(tmp_path)) == "described"
    assert seen == []


def test_schema_option_is_rejected_for_json(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(SourceError, match="SQL"):
        extract(str(path), namespace="x")
