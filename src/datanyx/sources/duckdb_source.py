"""DuckDB database files, described from the catalog: no table is ever queried.

The file is opened read-only with external access switched off, so the connection can't read
other files or write anything, and only `information_schema` and `duckdb_constraints()` are asked.
Enum labels, column defaults and comments can hold data, so they are never read: an enum is a
plain string, and nested STRUCT, MAP and UNION types are summarised rather than spelled out.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import (
    Array,
    Boolean,
    DataSchema,
    Entity,
    ForeignKey,
    Integer,
    Node,
    Null,
    Number,
    String,
    Union,
    Unknown,
)
from .base import Source, SourceError
from .catalog import build_object
from .files import extension, missing_extra

_EXTENSIONS = (".duckdb", ".ddb")
_INTEGERS = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
    "INT1", "INT2", "INT4", "INT8", "INT", "SHORT", "LONG", "SIGNED", "BIGNUM", "VARINT",
}  # fmt: skip
_NUMBERS = {"FLOAT", "DOUBLE", "REAL", "FLOAT4", "FLOAT8", "INTERVAL"}
_STRINGS = {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR", "JSON"}
_BINARY = {"BLOB", "BYTEA", "BINARY", "VARBINARY", "BIT", "BITSTRING"}


class DuckDbSource(Source):
    kind = "duckdb"

    def matches(self, target: str) -> bool:
        return extension(target) in _EXTENSIONS

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        try:
            import duckdb
        except ImportError:
            raise missing_extra("DuckDB", "duckdb") from None
        try:
            connection = duckdb.connect(target, read_only=True, config={"enable_external_access": False})
        except Exception as error:
            raise SourceError(f"could not open the DuckDB file ({type(error).__name__})") from None
        try:
            return _describe(connection, policy, namespace or "main", self.kind)
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"could not read the database ({type(error).__name__})") from None
        finally:
            connection.close()


def _describe(connection, policy: KeyPolicy, schema: str, kind: str) -> DataSchema:
    tables = [
        name
        for (name,) in connection.execute(
            "select table_name from information_schema.tables where table_schema = ? and table_type = 'BASE TABLE' order by table_name",
            [schema],
        ).fetchall()
    ]
    notes: list[str] = []
    if not tables:
        notes.append("no tables found")
    visible_tables = [name for name in tables if policy.shows_name(name)]
    if len(visible_tables) != len(tables):
        notes.append("hid data-like table names (key shape)")

    columns: dict[str, list[tuple[str, str, bool]]] = {name: [] for name in visible_tables}
    for table, column, data_type, nullable in connection.execute(
        "select table_name, column_name, data_type, is_nullable from information_schema.columns "
        "where table_schema = ? order by table_name, ordinal_position",
        [schema],
    ).fetchall():
        if table in columns:
            columns[table].append((column, data_type, nullable == "YES"))

    primary_keys: dict[str, list[str]] = {}
    foreign_rows: list[tuple[str, list[str], str, list[str]]] = []
    for table, ctype, names, referenced, referenced_names in connection.execute(
        "select table_name, constraint_type, constraint_column_names, referenced_table, referenced_column_names "
        "from duckdb_constraints() where schema_name = ?",
        [schema],
    ).fetchall():
        if ctype == "PRIMARY KEY":
            primary_keys[table] = list(names)
        elif ctype == "FOREIGN KEY" and referenced:
            foreign_rows.append((table, list(names), referenced, list(referenced_names)))

    sizes: dict[str, int] = {}
    if policy.scale_hints:  # The catalog's own row estimate: no table is read.
        sizes = {
            table: int(size)
            for table, size in connection.execute(
                "select table_name, estimated_size from duckdb_tables() where schema_name = ?", [schema]
            ).fetchall()
            if size is not None
        }
    visible: dict[str, frozenset[str]] = {}
    for table in visible_tables:
        hide_all = f"$.{table}" in policy.map_paths
        visible[table] = frozenset(c for c, _, _ in columns[table] if not hide_all and policy.shows_name(c))

    entities = []
    for table in visible_tables:
        key = primary_keys.get(table, [])
        shown_key = tuple(key) if all(column in visible[table] for column in key) else ()
        keys = tuple(
            ForeignKey(tuple(names), referenced, tuple(referenced_names))
            for owner, names, referenced, referenced_names in foreign_rows
            if owner == table
            and referenced in visible
            and all(n in visible[table] for n in names)
            and all(n in visible[referenced] for n in referenced_names)
        )
        entity = _entity(table, columns[table], set(key), shown_key, keys, policy, notes)
        entities.append(replace(entity, scale=scale_for(policy, sizes.get(table))))
    return DataSchema(kind, tuple(entities), tuple(notes))


def _entity(table, columns, key_columns, primary_key, foreign_keys, policy: KeyPolicy, notes: list[str]) -> Entity:
    entries = []
    for name, data_type, nullable in columns:
        node = _type_node(data_type)
        if nullable and name not in key_columns and not isinstance(node, Unknown):
            node = Union((node, Null()))
        entries.append((name, node, _native(data_type)))
    return Entity(table, build_object(entries, f"$.{table}", policy, notes), primary_key, foreign_keys, True)


def _type_node(data_type: str) -> Node:
    text = data_type.strip().upper()
    if text.endswith("]"):  # LIST (`INTEGER[]`) and ARRAY (`INTEGER[3]`).
        return Array(_type_node(re.sub(r"\[\d*\]$", "", text)))
    base = re.match(r"[A-Z_0-9 ]+", text)
    name = base.group(0).strip() if base else ""
    if name in _INTEGERS:
        return Integer()
    if name == "DECIMAL" or name == "NUMERIC":
        scale = re.search(r"\(\s*\d+\s*,\s*(\d+)\s*\)", text)
        return Integer() if scale and scale.group(1) == "0" else Number()
    if name in _NUMBERS:
        return Number()
    if name == "BOOLEAN" or name == "BOOL" or name == "LOGICAL":
        return Boolean()
    if name in _STRINGS or name == "ENUM":
        return String()  # An enum's labels are values, so only its being text is kept.
    if name in _BINARY:
        return String("binary")
    if name == "UUID":
        return String("uuid")
    if name == "DATE":
        return String("date")
    if name.startswith("TIMESTAMP") or name == "DATETIME":
        return String("date-time")
    if name.startswith("TIME"):
        return String("time")
    return Unknown()  # STRUCT, MAP, UNION and anything newer.


def _native(data_type: str) -> str | None:
    """The type's name and size only: ENUM('a','b') and STRUCT(name VARCHAR) put names and labels in the text."""
    text = data_type.strip()
    suffix = "[]" if text.endswith("]") else ""
    match = re.match(r"[A-Za-z_][A-Za-z_ ]*(\(\s*\d+\s*(,\s*\d+\s*)?\))?", re.sub(r"\[\d*\]$", "", text))
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(0)).upper().replace("WITHTIMEZONE", " WITH TIME ZONE") + suffix
