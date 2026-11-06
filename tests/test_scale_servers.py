"""Row-count hints from the catalog of real servers. All data is synthetic.

    docker compose -f tests/docker-compose.yml up -d --wait postgres mysql mariadb mssql oracle
    DATANYX_TEST_SERVERS=1 pytest -m server tests/test_scale_servers.py
"""

import os

import pytest
import sqlalchemy as sa

from datanyx import extract
from datanyx.privacy import KeyPolicy

pytestmark = [
    pytest.mark.server,
    pytest.mark.skipif(
        not os.environ.get("DATANYX_TEST_SERVERS"),
        reason="set DATANYX_TEST_SERVERS=1 and start tests/docker-compose.yml",
    ),
]

PW = "datanyx-test-Pw1"
SERVERS = {
    "postgres": (f"postgresql://postgres:{PW}@127.0.0.1:55432/datanyx", "postgresql+psycopg", "ANALYZE dn_scale"),
    "mysql": (f"mysql://root:{PW}@127.0.0.1:53306/datanyx", "mysql+pymysql", "ANALYZE TABLE dn_scale"),
    "mariadb": (f"mariadb://root:{PW}@127.0.0.1:53307/datanyx", "mariadb+pymysql", "ANALYZE TABLE dn_scale"),
    # SQL Server counts partition rows as it goes, so it needs no analysing.
    "mssql": (f"mssql://sa:{PW}@127.0.0.1:51433/datanyx", "mssql+pymssql", None),
    "oracle": (
        f"oracle://datanyx:{PW}@127.0.0.1:51521/?service_name=FREEPDB1",
        "oracle+oracledb",
        "BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, 'DN_SCALE'); END;",
    ),
}


@pytest.mark.parametrize("name", SERVERS)
def test_the_servers_own_estimate_becomes_a_bucket(name):
    url, driver, analyze = SERVERS[name]
    engine = sa.create_engine(url.replace(url.split(":", 1)[0], driver, 1))
    if name == "mssql":
        master = sa.create_engine(engine.url.set(database="master"), isolation_level="AUTOCOMMIT")
        with master.connect() as connection:
            connection.execute(sa.text("IF DB_ID('datanyx') IS NULL CREATE DATABASE datanyx"))
        master.dispose()
    with engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE IF EXISTS dn_scale"))
        connection.execute(sa.text("CREATE TABLE dn_scale (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO dn_scale (id) VALUES (:id)"), [{"id": i} for i in range(1, 1501)])
    try:
        if analyze:
            with engine.connect() as connection:
                result = connection.execute(sa.text(analyze))
                if result.returns_rows:  # MySQL and MariaDB report a status row.
                    result.fetchall()
                connection.commit()
        schema = extract(url, policy=KeyPolicy(scale_hints=True))
        entity = next(e for e in schema.entities if e.name == "dn_scale")
        assert entity.scale == "10^3 to 10^4 rows"
        assert "1500" not in repr(schema.notes)
        assert extract(url).entities[0].scale is None  # Off unless asked.
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text("DROP TABLE IF EXISTS dn_scale"))
        engine.dispose()
