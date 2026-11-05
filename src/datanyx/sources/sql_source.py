"""SQL databases, described from the catalog through SQLAlchemy's inspector.

Only catalog calls are made, so no row is ever queried. SQLite files are opened
read-only with an authorizer that makes SQLite itself refuse to read user tables.
Server databases have no client-side equivalent; connect with a role that can't
read rows for the same guarantee (see the README).

The inspector also fetches column defaults, comments and CREATE statements, which
can hold literal data. They stay inside SQLAlchemy: this module only uses names,
types, nullability and keys.
"""

from __future__ import annotations

import os
import re
import sqlite3
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine, Inspector, make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeEngine

from ..formats import detect_format
from ..privacy import KeyPolicy
from ..schema import (
    Array,
    Boolean,
    DataSchema,
    Entity,
    Field,
    ForeignKey,
    Integer,
    Node,
    Null,
    Number,
    Object,
    String,
    Union,
    Unknown,
)
from .base import Source, SourceError
from .catalog import merge_nodes as _merge_nodes

_FILE_EXTENSIONS = (".db", ".sqlite", ".sqlite3")
_ENV_PREFIX = "env:"
_DEFAULT_DRIVERS = {
    "postgres": "postgresql+psycopg",
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mariadb": "mariadb+pymysql",
    "mssql": "mssql+pymssql",
    "oracle": "oracle+oracledb",
}
# Drivers otherwise wait minutes for an unreachable server, stalling an agent's session.
_CONNECT_TIMEOUT_SECONDS = 10
_CONNECT_TIMEOUT_ARGUMENTS = {
    "postgresql": "connect_timeout",
    "mysql": "connect_timeout",
    "mariadb": "connect_timeout",
    "mssql": "login_timeout",
    "oracle": "tcp_connect_timeout",
}
_SQLITE_CATALOG_TABLES = ("sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema")
_MAX_NATIVE_TYPE_LENGTH = 64
# A type name, an optional size and a suffix. Enum labels are the reason for being
# this strict: ENUM('a','b') and SET('a','b') put data in the type's own text, so
# everything from the first character outside this shape is dropped.
_NATIVE_TYPE = re.compile(
    r"[A-Za-z_][\w .]*"
    r"(\(\s*(\d+|max)\s*(,\s*\d+\s*)?(\s(CHAR|BYTE)\s*)?\))?"
    r"[\w \[\]]*",
    re.IGNORECASE,
)
_TYPE_DETAILS = re.compile(r"\s(COLLATE|CHARACTER SET|CHARSET)\s.*$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _Table:
    name: str
    columns: Sequence[Mapping[str, Any]]
    visible: frozenset[str]
    key_columns: frozenset[str]
    primary_key: tuple[str, ...]  # Empty when the key involves a hidden column.


class SqlSource(Source):
    """Any database with a SQLAlchemy dialect, given as a URL or a SQLite file path."""

    kind = "sql"

    def matches(self, target: str) -> bool:
        return (
            target.startswith(_ENV_PREFIX)
            or "://" in target
            or Path(target).suffix.lower() in _FILE_EXTENSIONS
        )

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        # Only fixed messages and exception type names leave this method. Driver and
        # SQLAlchemy messages can quote statements, data or the connection password.
        try:
            engine = _create_engine(_resolve_env(target))
        except SourceError:
            raise
        except ImportError:
            raise SourceError(
                "the driver for this database isn't installed; "
                "try datanyx[postgres], [mysql], [mssql] or [oracle]"
            ) from None
        except NoSuchModuleError:
            raise SourceError("unknown database type in the URL; it may need a dialect package") from None
        except ArgumentError:
            raise SourceError("could not parse the database URL") from None

        try:
            # SQLAlchemy's warnings go to stderr and can name hidden columns, such as
            # "Did not recognize type 'x' of column 'y'".
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return _describe(engine, policy, namespace, self.kind)
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"could not read the database ({type(error).__name__})") from None
        finally:
            engine.dispose()


def _resolve_env(target: str) -> str:
    """Let the URL come from the environment, keeping passwords off the command line."""
    if not target.startswith(_ENV_PREFIX):
        return target
    name = target[len(_ENV_PREFIX) :]
    value = os.environ.get(name)
    if not value:
        raise SourceError(f"environment variable {name} isn't set")
    return value


def _create_engine(target: str) -> Engine:
    if "://" not in target:
        return _sqlite_engine(Path(target))
    url = make_url(target)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        return _sqlite_engine(Path(url.database))
    if url.drivername in _DEFAULT_DRIVERS:
        url = url.set(drivername=_DEFAULT_DRIVERS[url.drivername])
    connect_args = {}
    timeout_argument = _CONNECT_TIMEOUT_ARGUMENTS.get(url.get_backend_name())
    if timeout_argument and timeout_argument not in url.query:
        connect_args[timeout_argument] = _CONNECT_TIMEOUT_SECONDS
    return sa.create_engine(url, poolclass=NullPool, connect_args=connect_args)


def _sqlite_engine(path: Path) -> Engine:
    if not path.is_file():
        raise SourceError("cannot read input: No such file or directory")
    return sa.create_engine(
        "sqlite://", creator=lambda: connect_sqlite_catalog_only(path), poolclass=NullPool
    )


def connect_sqlite_catalog_only(path: Path) -> sqlite3.Connection:
    # mode=ro also stops sqlite3 creating an empty file at a mistyped path.
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.set_authorizer(_authorize_sqlite)
    return connection


def _authorize_sqlite(action: int, arg1: str | None, *_rest: object) -> int:
    """Have SQLite itself refuse to read anything but the catalog, or to change anything."""
    if action == sqlite3.SQLITE_READ:
        allowed = arg1 in _SQLITE_CATALOG_TABLES or (arg1 or "").startswith("pragma_")
    else:
        allowed = action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_FUNCTION)
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def _describe(engine: Engine, policy: KeyPolicy, namespace: str | None, kind: str) -> DataSchema:
    inspector = sa.inspect(engine)
    names = list(inspector.get_table_names(schema=namespace))
    visible_names = [name for name in names if policy.shows_name(name)]

    notes: list[str] = []
    if not names:
        notes.append("no tables found")
    if len(visible_names) != len(names):
        notes.append("hid data-like table names (key shape)")

    tables: dict[str, _Table] = {}
    for name in visible_names:
        try:
            tables[name] = _read_table(inspector, name, namespace, policy)
        except Exception:
            # Inspecting a SQLite virtual table runs its module's own queries, which
            # the authorizer refuses; a server may deny access to a single table.
            notes.append(f"could not describe the table at $.{name}")

    enforced = engine.dialect.name != "sqlite"
    entities = tuple(
        _build_entity(table, _read_foreign_keys(inspector, table, tables, namespace), engine, enforced, policy, notes)
        for table in tables.values()
    )
    return DataSchema(kind, entities, tuple(notes))


def _read_table(inspector: Inspector, name: str, namespace: str | None, policy: KeyPolicy) -> _Table:
    columns = inspector.get_columns(name, schema=namespace)
    hide_all = f"$.{name}" in policy.map_paths
    visible = frozenset(
        column["name"]
        for column in columns
        if not hide_all and policy.shows_name(column["name"])
    )
    key = inspector.get_pk_constraint(name, schema=namespace).get("constrained_columns") or []
    # A partial key would mislead, so a key with a hidden column isn't shown at all.
    primary_key = tuple(key) if all(column in visible for column in key) else ()
    return _Table(name, columns, visible, frozenset(key), primary_key)


def _read_foreign_keys(
    inspector: Inspector, table: _Table, tables: dict[str, _Table], namespace: str | None
) -> tuple[ForeignKey, ...]:
    keys = (
        _resolve_foreign_key(key, table, tables, namespace)
        for key in inspector.get_foreign_keys(table.name, schema=namespace)
    )
    return tuple(key for key in keys if key is not None)


def _resolve_foreign_key(
    key: Mapping[str, Any], table: _Table, tables: dict[str, _Table], namespace: str | None
) -> ForeignKey | None:
    """Build the key, or return None if any part of it must stay hidden.

    Naming a hidden table or column here would undo hiding it.
    """
    if key.get("referred_schema") not in (None, namespace):
        return None
    referenced_name = _find_name(tables, key["referred_table"])
    if referenced_name is None:
        return None
    referenced = tables[referenced_name]
    sources = [_find_name(table.visible, name) for name in key["constrained_columns"]]
    targets = [_find_name(referenced.visible, name) for name in key["referred_columns"]]
    if not targets or None in sources or None in targets or len(sources) != len(targets):
        return None
    return ForeignKey(
        tuple(name for name in sources if name is not None),
        referenced_name,
        tuple(name for name in targets if name is not None),
    )


def _find_name(names: Iterable[str], wanted: str) -> str | None:
    """Exact match, else a unique case-insensitive one (`users` can reference `Users`)."""
    names = list(names)
    if wanted in names:
        return wanted
    matches = [name for name in names if name.lower() == wanted.lower()]
    return matches[0] if len(matches) == 1 else None


def _build_entity(
    table: _Table,
    foreign_keys: tuple[ForeignKey, ...],
    engine: Engine,
    enforced: bool,
    policy: KeyPolicy,
    notes: list[str],
) -> Entity:
    fields = {}
    hidden: list[Mapping[str, Any]] = []
    for column in table.columns:
        if column["name"] in table.visible:
            native = _native_type(column["type"], engine)
            fields[column["name"]] = Field(_column_node(column, table, enforced), True, native)
        else:
            hidden.append(column)

    extra = key_format = None
    if hidden:
        configured = f"$.{table.name}" in policy.map_paths
        reason = "configured map path" if configured else "key shape"
        notes.append(f"hid data-like columns at $.{table.name} ({reason})")
        extra = _merge_nodes([_column_node(column, table, enforced) for column in hidden])
        formats = {detect_format(column["name"]) for column in hidden}
        key_format = formats.pop() if len(formats) == 1 else None

    return Entity(table.name, Object(fields, extra, key_format), table.primary_key, foreign_keys, True)


def _column_node(column: Mapping[str, Any], table: _Table, enforced: bool) -> Node:
    node = _type_node(column["type"], enforced)
    in_key = column["name"] in table.key_columns
    if not column.get("nullable", True) or in_key or isinstance(node, Unknown):
        return node
    return Union((node, Null()))


def _type_node(column_type: TypeEngine, enforced: bool) -> Node:
    """Map a reflected type. Order matters: several types subclass more than one of these."""
    if isinstance(column_type, sa.Boolean):
        return Boolean()
    # Before Integer, as Oracle's NUMBER is both. Float isn't a Numeric in SQLAlchemy 2.1.
    if isinstance(column_type, (sa.Numeric, sa.Float)):
        return Integer() if getattr(column_type, "scale", None) == 0 else Number()
    if isinstance(column_type, sa.Integer):
        return Integer()
    if isinstance(column_type, sa.ARRAY):
        return Array(_type_node(column_type.item_type, enforced))
    if isinstance(column_type, sa.JSON):
        return Unknown()
    if isinstance(column_type, sa.Uuid):
        return String("uuid")
    if isinstance(column_type, sa.LargeBinary):
        return String("binary")
    if isinstance(column_type, sa.String):
        return String()
    if isinstance(column_type, (sa.DateTime, sa.Date, sa.Time)):
        # SQLite doesn't enforce types, so a date there may be text or an epoch number.
        return _temporal_node(column_type) if enforced else Unknown()
    return Unknown()


def _temporal_node(column_type: TypeEngine) -> Node:
    if isinstance(column_type, sa.DateTime):
        return String("date-time")
    return String("date" if isinstance(column_type, sa.Date) else "time")


def _native_type(column_type: TypeEngine, engine: Engine) -> str | None:
    try:
        return clean_native_type(column_type.compile(dialect=engine.dialect))
    except Exception:  # NullType and some custom types can't be compiled.
        return None


def clean_native_type(declared: str) -> str | None:
    match = _NATIVE_TYPE.match(_TYPE_DETAILS.sub("", declared).strip())
    if match is None:
        return None
    cleaned = match.group(0).strip()
    return cleaned if len(cleaned) <= _MAX_NATIVE_TYPE_LENGTH else None
