"""Declared uniqueness and views from the catalogs of real servers. All data is synthetic.

    docker compose -f tests/docker-compose.yml up -d --wait postgres mysql mariadb
    DATANYX_TEST_SERVERS=1 pytest -m server tests/test_catalog_servers.py
"""

import os

import pytest
import sqlalchemy as sa

from datanyx import KeyPolicy, extract
from datanyx.schema import Object

pytestmark = [
    pytest.mark.server,
    pytest.mark.skipif(
        not os.environ.get("DATANYX_TEST_SERVERS"),
        reason="set DATANYX_TEST_SERVERS=1 and start tests/docker-compose.yml",
    ),
]

PW = "datanyx-test-Pw1"
SERVERS = {
    "postgres": (f"postgresql://postgres:{PW}@127.0.0.1:55432/datanyx", "postgresql+psycopg"),
    "mysql": (f"mysql://root:{PW}@127.0.0.1:53306/datanyx", "mysql+pymysql"),
    "mariadb": (f"mariadb://root:{PW}@127.0.0.1:53307/datanyx", "mariadb+pymysql"),
}
DDL = [
    "DROP VIEW IF EXISTS dn_cat_view",
    "DROP TABLE IF EXISTS dn_cat",
    "CREATE TABLE dn_cat (id INTEGER PRIMARY KEY, email VARCHAR(80), a INTEGER, b INTEGER, CONSTRAINT dn_cat_email UNIQUE (email), CONSTRAINT dn_cat_ab UNIQUE (a, b))",
    "CREATE VIEW dn_cat_view AS SELECT id, email FROM dn_cat",
]


@pytest.mark.parametrize("name", SERVERS)
def test_declared_uniques_and_views_come_from_the_catalog(name):
    url, driver = SERVERS[name]
    engine = sa.create_engine(url.replace(url.split(":", 1)[0], driver, 1))
    with engine.begin() as connection:
        for statement in DDL:
            connection.execute(sa.text(statement))
    try:
        assert "dn_cat_view" not in {e.name for e in extract(url).entities}  # Views are opt-in.
        schema = extract(url, policy=KeyPolicy(views=True, include=("dn_cat*",)))
        table = next(e for e in schema.entities if e.name == "dn_cat")
        view = next(e for e in schema.entities if e.name == "dn_cat_view")
        assert isinstance(table.node, Object) and table.node.fields["email"].unique_declared
        assert table.unique_keys == (("a", "b"),)
        assert view.view and view.primary_key == ()
    finally:
        with engine.begin() as connection:
            for statement in DDL[:2]:
                connection.execute(sa.text(statement))
        engine.dispose()
