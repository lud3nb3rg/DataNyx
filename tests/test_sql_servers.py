"""Live tests against real servers. All data is synthetic.

    docker compose -f tests/docker-compose.yml up -d --wait
    DATANYX_TEST_SERVERS=1 pytest -m server
"""

import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
import sqlalchemy as sa

from datanyx import KeyPolicy, SourceError, extract, render
from datanyx.cli import main
from datanyx.renderers import RENDERERS
from datanyx.schema import Integer, Null, Number, String, Union

pytestmark = [
    pytest.mark.server,
    pytest.mark.skipif(
        not os.environ.get("DATANYX_TEST_SERVERS"),
        reason="set DATANYX_TEST_SERVERS=1 and start tests/docker-compose.yml",
    ),
]

CANARY = "CANARY"
SECRET_NUMBER = 918273
PW = "datanyx-test-Pw1"
READER = "dn_reader"
READER_PW = "dn-Reader-Pw1"
ORACLE_SERVICE = "?service_name=FREEPDB1"
VISIBLE_TABLES = {"dn_users", "dn_orders", "dn_items", "dn_refunds", "dn_links"}
DRIVERS = {
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mariadb": "mariadb+pymysql",
    "mssql": "mssql+pymssql",
    "oracle": "oracle+oracledb",
}


@dataclass(frozen=True)
class Backend:
    name: str
    admin_url: str  # Short form, so the default driver choice is exercised too.
    reader_url: str
    reader_namespace: str | None = None


BACKENDS = [
    Backend(
        "postgres",
        f"postgresql://postgres:{PW}@127.0.0.1:55432/datanyx",
        f"postgresql://{READER}:{READER_PW}@127.0.0.1:55432/datanyx",
    ),
    Backend(
        "mysql",
        f"mysql://root:{PW}@127.0.0.1:53306/datanyx",
        f"mysql://{READER}:{READER_PW}@127.0.0.1:53306/datanyx",
    ),
    Backend(
        "mariadb",
        f"mariadb://root:{PW}@127.0.0.1:53307/datanyx",
        f"mariadb://{READER}:{READER_PW}@127.0.0.1:53307/datanyx",
    ),
    Backend(
        "mssql",
        f"mssql://sa:{PW}@127.0.0.1:51433/datanyx",
        f"mssql://{READER}:{READER_PW}@127.0.0.1:51433/datanyx",
    ),
    Backend(
        "oracle",
        f"oracle://datanyx:{PW}@127.0.0.1:51521/{ORACLE_SERVICE}",
        f"oracle://{READER}:{READER_PW}@127.0.0.1:51521/{ORACLE_SERVICE}",
        reader_namespace="datanyx",
    ),
]


def engine_for(url, **options):
    parsed = sa.make_url(url)
    return sa.create_engine(parsed.set(drivername=DRIVERS[parsed.drivername]), **options)


def build_metadata(backend):
    metadata = sa.MetaData()
    columns = [
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("email", sa.String(255), nullable=False, server_default="CANARY@default.example"),
        sa.Column("status", sa.Enum("CANARY_ACTIVE", "CANARY_BANNED", name="dn_status")),
        sa.Column("active", sa.Boolean),
        sa.Column("balance", sa.Numeric(10, 2)),
        sa.Column("ratio", sa.Float),
        sa.Column("joined", sa.Date),
        sa.Column("seen", sa.DateTime),
        sa.Column("bio", sa.Text, comment="CANARY comment"),
        sa.Column("photo", sa.LargeBinary),
        sa.Column("external_id", sa.Uuid),
        sa.Column("CANARY@example.com", sa.String(50)),
        sa.CheckConstraint("email <> 'CANARY_CHECK'", name="dn_check"),
    ]
    if backend.name != "oracle":
        columns.append(sa.Column("meta", sa.JSON))
    sa.Table("dn_users", metadata, *columns)
    sa.Table(
        "dn_orders",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("dn_users.id"), nullable=False),
    )
    sa.Table(
        "dn_items",
        metadata,
        sa.Column("order_id", sa.Integer, sa.ForeignKey("dn_orders.id"), primary_key=True),
        sa.Column("line", sa.Integer, primary_key=True, autoincrement=False),
    )
    sa.Table(
        "dn_refunds",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("order_id", sa.Integer),
        sa.Column("line", sa.Integer),
        sa.ForeignKeyConstraint(["order_id", "line"], ["dn_items.order_id", "dn_items.line"]),
    )
    sa.Table(
        "dn_tenant_00123",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
    )
    sa.Table(
        "dn_links",
        metadata,
        sa.Column("tenant", sa.Integer, sa.ForeignKey("dn_tenant_00123.id")),
    )
    return metadata


def execute_each(connection, statements):
    for statement in statements:
        connection.execute(sa.text(statement))


def create_reader(backend, connection):
    """A login that can see the catalog but has no way to read a row."""
    if backend.name == "postgres":
        execute_each(connection, [
            f"DROP ROLE IF EXISTS {READER}",
            f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PW}'",
        ])
    elif backend.name in ("mysql", "mariadb"):
        execute_each(connection, [
            f"DROP USER IF EXISTS '{READER}'@'%'",
            f"CREATE USER '{READER}'@'%' IDENTIFIED BY '{READER_PW}'",
            f"GRANT REFERENCES ON datanyx.* TO '{READER}'@'%'",
        ])
    elif backend.name == "mssql":
        execute_each(connection, [
            f"IF USER_ID('{READER}') IS NOT NULL DROP USER {READER}",
            f"IF SUSER_ID('{READER}') IS NOT NULL DROP LOGIN {READER}",
            f"CREATE LOGIN {READER} WITH PASSWORD = '{READER_PW}'",
            f"CREATE USER {READER} FOR LOGIN {READER}",
            f"GRANT VIEW DEFINITION TO {READER}",
        ])
    elif backend.name == "oracle":
        system = engine_for(f"oracle://system:{PW}@127.0.0.1:51521/{ORACLE_SERVICE}")
        with system.begin() as admin:
            exists = admin.execute(
                sa.text("SELECT count(*) FROM all_users WHERE username = :u"),
                {"u": READER.upper()},
            ).scalar()
            if exists:
                admin.execute(sa.text(f"DROP USER {READER} CASCADE"))
            execute_each(admin, [
                f'CREATE USER {READER} IDENTIFIED BY "{READER_PW}"',
                f"GRANT CREATE SESSION TO {READER}",
            ])
        system.dispose()
        execute_each(
            connection,
            [f"GRANT REFERENCES ON {table} TO {READER}" for table in sorted(VISIBLE_TABLES)],
        )


def drop_view_if_present(engine):
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("DROP VIEW dn_view"))
    except sa.exc.DBAPIError:
        pass


@pytest.fixture(scope="module", params=BACKENDS, ids=lambda backend: backend.name)
def backend(request):
    backend = request.param
    if backend.name == "mssql":
        master_url = backend.admin_url.replace("/datanyx", "/master")
        master = engine_for(master_url, isolation_level="AUTOCOMMIT")
        with master.connect() as connection:
            connection.execute(sa.text("IF DB_ID('datanyx') IS NULL CREATE DATABASE datanyx"))
        master.dispose()

    engine = engine_for(backend.admin_url)
    metadata = build_metadata(backend)
    drop_view_if_present(engine)
    metadata.drop_all(engine)
    metadata.create_all(engine)
    view_from = " FROM dual" if backend.name == "oracle" else ""
    with engine.begin() as connection:
        connection.execute(sa.text(f"CREATE VIEW dn_view AS SELECT 'CANARY_VIEW' AS c{view_from}"))
        connection.execute(
            metadata.tables["dn_users"].insert(),
            {
                "id": SECRET_NUMBER,
                "email": "CANARY.row@example.com",
                "status": "CANARY_ACTIVE",
                "bio": "CANARY bio",
            },
        )
        connection.execute(
            metadata.tables["dn_orders"].insert(), {"id": 1, "user_id": SECRET_NUMBER}
        )
        create_reader(backend, connection)
    engine.dispose()
    return backend


@contextmanager
def recorded_statements():
    statements = []

    def record(_connection, _cursor, statement, *_rest):
        statements.append(statement)

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", record)


def tables(schema):
    return {entity.name: entity for entity in schema.entities}


def base_type(field):
    node = field.node
    options = node.options if isinstance(node, Union) else (node,)
    return next(option for option in options if not isinstance(option, Null))


def test_only_structure_reaches_the_output(backend):
    schema = extract(backend.admin_url)
    output = "\n".join([render(schema, name) for name in RENDERERS] + list(schema.notes))
    assert CANARY not in output
    assert str(SECRET_NUMBER) not in output
    assert PW not in output


def test_tables_and_hidden_names(backend):
    found = tables(extract(backend.admin_url))
    assert VISIBLE_TABLES <= set(found)
    assert "dn_tenant_00123" not in found
    users = found["dn_users"].node
    assert users.extra is not None  # The email-shaped column is hidden, not lost.
    assert "email" in users.fields


def test_primary_and_foreign_keys(backend):
    found = tables(extract(backend.admin_url))
    assert found["dn_users"].primary_key == ("id",)
    assert set(found["dn_items"].primary_key) == {"order_id", "line"}

    order_key = found["dn_orders"].foreign_keys[0]
    assert order_key.columns == ("user_id",)
    assert order_key.referenced_entity == "dn_users"
    assert order_key.referenced_columns == ("id",)

    composite = found["dn_refunds"].foreign_keys[0]
    assert composite.referenced_entity == "dn_items"
    assert dict(zip(composite.columns, composite.referenced_columns)) == {
        "order_id": "order_id",
        "line": "line",
    }
    # The referenced table is hidden, so naming it here would undo that.
    assert found["dn_links"].foreign_keys == ()


def test_column_types(backend):
    fields = tables(extract(backend.admin_url))["dn_users"].node.fields
    assert isinstance(fields["id"].node, Integer)
    assert isinstance(fields["email"].node, String)
    assert isinstance(base_type(fields["balance"]), Number)
    assert isinstance(base_type(fields["ratio"]), Number)
    assert isinstance(base_type(fields["bio"]), String)
    assert isinstance(base_type(fields["status"]), String)
    assert base_type(fields["seen"]).format == "date-time"
    assert base_type(fields["photo"]).format == "binary"
    for name in ("id", "email", "balance", "seen"):
        assert fields[name].native_type


def test_no_statement_reads_a_user_table(backend):
    with recorded_statements() as statements:
        extract(backend.admin_url)
    assert statements
    reads_rows = re.compile(r"\bfrom\s+[`\"\[]?(\w+[`\"\]]?\.)?[`\"\[]?dn_", re.IGNORECASE)
    assert [s for s in statements if reads_rows.search(s)] == []
    writes = re.compile(r"\s*(insert|update|delete|drop|alter|create)\b", re.IGNORECASE)
    assert [s for s in statements if writes.match(s)] == []


def test_login_without_row_access_still_gets_the_schema(backend):
    prefix = f"{backend.reader_namespace}." if backend.reader_namespace else ""
    engine = engine_for(backend.reader_url)
    with engine.connect() as connection:
        with pytest.raises(sa.exc.DBAPIError):
            connection.execute(sa.text(f"SELECT email FROM {prefix}dn_users")).fetchall()
    engine.dispose()

    found = tables(extract(backend.reader_url, namespace=backend.reader_namespace))
    assert VISIBLE_TABLES <= set(found)
    assert "email" in found["dn_users"].node.fields
    assert found["dn_orders"].foreign_keys[0].referenced_entity == "dn_users"


def test_map_path_and_keep_name(backend):
    policy = KeyPolicy(
        map_paths=frozenset({"$.dn_orders"}),
        keep_names=frozenset({"dn_tenant_00123"}),
    )
    found = tables(extract(backend.admin_url, policy=policy))
    assert found["dn_orders"].node.fields == {}
    assert "dn_tenant_00123" in found
    assert found["dn_links"].foreign_keys[0].referenced_entity == "dn_tenant_00123"


def test_wrong_password_shows_no_secret(backend, capsys):
    url = backend.admin_url.replace(PW, "CANARY-wrong")
    assert main(["schema", url]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert CANARY not in captured.out + captured.err


def test_cli_reads_the_url_from_the_environment(backend, capsys, monkeypatch):
    monkeypatch.setenv("DN_TEST_URL", backend.admin_url)
    assert main(["schema", "env:DN_TEST_URL"]) == 0
    captured = capsys.readouterr()
    assert PW not in captured.out + captured.err
    assert VISIBLE_TABLES <= set(json.loads(captured.out)["$defs"])


def test_unrecognised_type_does_not_reveal_a_hidden_column_on_stderr():
    # SQLAlchemy warns about these, naming the column. A real process is used here
    # because pytest would otherwise capture the warning before it reached stderr.
    url = BACKENDS[0].admin_url
    engine = engine_for(url)
    with engine.begin() as connection:
        execute_each(connection, [
            "DROP SCHEMA IF EXISTS dn_warn CASCADE",
            "CREATE SCHEMA dn_warn",
            'CREATE TABLE dn_warn.t (id INT, "CANARY@example.com" pg_snapshot)',
        ])
    engine.dispose()
    source_dir = os.path.join(os.path.dirname(__file__), "..", "src")
    result = subprocess.run(
        [sys.executable, "-m", "datanyx", "schema", url, "--schema", "dn_warn"],
        env={**os.environ, "PYTHONPATH": source_dir},
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert CANARY not in result.stdout + result.stderr


def test_postgres_schema_selection_and_native_types():
    url = BACKENDS[0].admin_url
    engine = engine_for(url)
    with engine.begin() as connection:
        execute_each(connection, [
            "DROP SCHEMA IF EXISTS dn_other CASCADE",
            "CREATE SCHEMA dn_other",
            "CREATE TABLE dn_other.parent (id INT PRIMARY KEY, tags TEXT[], ip INET, doc JSONB)",
            "CREATE TABLE dn_other.child (id INT PRIMARY KEY, parent_id INT REFERENCES dn_other.parent (id))",
        ])
    engine.dispose()
    found = tables(extract(url, namespace="dn_other"))
    assert set(found) == {"parent", "child"}
    assert found["child"].foreign_keys[0].referenced_entity == "parent"
    assert found["parent"].node.fields["tags"].native_type == "TEXT[]"
    with pytest.raises(SourceError):
        extract(url.replace("55432", "1"))
