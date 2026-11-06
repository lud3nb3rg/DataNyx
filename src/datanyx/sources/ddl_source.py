"""SQL files (`.sql`): the tables their `CREATE TABLE` and `ALTER TABLE ... ADD CONSTRAINT` statements define.

A schema dump or a migration describes a database without needing to connect to it. Only the statements that
define tables are parsed. An `INSERT`, a `COPY ... FROM stdin` block or a view is passed over unread, and the
parts of a table definition that can hold literals (defaults, `CHECK` expressions, comments, enum labels) are
never used: this module takes names, types, nullability and keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ..privacy import KeyPolicy
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
    Object,
    String,
    Union,
    Unknown,
)
from .base import Source, SourceError
from .catalog import build_object
from .files import extension, missing_extra, open_text, reject_namespace, require_local
from .sql_source import _find_name, clean_native_type

_MAX_BYTES = 25 * 1024 * 1024
# Tried in turn until one reads every table definition. The first, the generic dialect, covers a lot.
_DIALECTS = ("", "postgres", "mysql", "tsql", "sqlite", "oracle", "bigquery", "snowflake")
_COPY_BLOCK = re.compile(r"^COPY\s[^\n;]*?\sFROM\s+stdin;\n.*?^\\\.[ \t]*$", re.IGNORECASE | re.MULTILINE | re.DOTALL)


@dataclass
class _Table:
    name: str
    columns: list[tuple[str, Any]] = field(default_factory=list)  # name, sqlglot DataType or None
    not_null: set[str] = field(default_factory=set)
    primary_key: list[str] = field(default_factory=list)
    unique: list[tuple[str, ...]] = field(default_factory=list)
    foreign_keys: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = field(default_factory=list)


class DdlSource(Source):
    kind = "ddl"

    def matches(self, target: str) -> bool:
        return extension(target) == ".sql"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_local(target, "SQL")
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            raise missing_extra("SQL files", "ddl") from None
        with open_text(target) as stream:
            text = stream.read(_MAX_BYTES + 1)
        if len(text) > _MAX_BYTES:
            raise SourceError("the SQL file is larger than 25 MB; point at the schema part")
        text = _COPY_BLOCK.sub("", text)
        tables, dialect = _best_reading(text)
        notes: list[str] = []
        if not tables:
            return DataSchema(self.kind, (), ("no CREATE TABLE statements found",))
        return DataSchema(self.kind, _entities(tables, dialect, policy, notes), tuple(notes))


def _best_reading(text: str) -> tuple[dict[str, _Table], str]:
    """Tables as the first dialect that reads every definition cleanly sees them, else the dialect that read most."""
    best: tuple[dict[str, _Table], str] = ({}, "")
    best_score = (-1, 0)
    for dialect in _DIALECTS:
        try:
            tables, failures = _read(text, dialect)
        except Exception:  # A tokenizer error: the quoting of another dialect.
            continue
        if tables and not failures:
            return tables, dialect
        score = (len(tables), -failures)
        if score > best_score:
            best, best_score = (tables, dialect), score
    return best


def _read(text: str, dialect: str) -> tuple[dict[str, _Table], int]:
    import sqlglot
    from sqlglot import exp
    from sqlglot.tokens import TokenType

    tokens = sqlglot.tokenize(text, read=dialect or None)
    tables: dict[str, _Table] = {}
    alters: list[Any] = []
    failures = 0
    for first, last in _statements(tokens, TokenType):
        head = tokens[first].token_type
        if head not in (TokenType.CREATE, TokenType.ALTER):
            continue
        statement_text = text[tokens[first].start : tokens[last].end + 1]
        try:
            statement = sqlglot.parse_one(statement_text, read=dialect or None)
        except Exception:
            failures += 1
            continue
        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Schema) and _is_table(statement):
            table = _table_from_create(statement, exp)
            if table is not None:
                tables[table.name] = table
        elif isinstance(statement, exp.Alter):
            alters.append(statement)
    for statement in alters:
        _apply_alter(statement, tables, exp)
    return tables, failures


def _statements(tokens: list, token_type) -> list[tuple[int, int]]:
    """(first, last) token positions of each statement, split at semicolons."""
    spans: list[tuple[int, int]] = []
    start = 0
    for position, token in enumerate(tokens):
        if token.token_type == token_type.SEMICOLON:
            if position > start:
                spans.append((start, position - 1))
            start = position + 1
    if start < len(tokens):
        spans.append((start, len(tokens) - 1))
    return spans


def _is_table(statement) -> bool:
    return str(statement.args.get("kind") or "").upper() == "TABLE"


def _name(identifier) -> str:
    return str(getattr(identifier, "name", identifier))


def _columns_of(schema_like) -> tuple[str, ...]:
    return tuple(_name(item) for item in (getattr(schema_like, "expressions", None) or []))


def _table_from_create(statement, exp) -> _Table | None:
    schema = statement.this
    if not isinstance(schema.this, exp.Table):
        return None
    table = _Table(schema.this.name)
    for item in schema.expressions:
        if isinstance(item, exp.ColumnDef):
            column = _name(item.this)
            table.columns.append((column, item.args.get("kind")))
            for constraint in item.args.get("constraints") or []:
                _column_constraint(table, column, constraint.args.get("kind"), exp)
        else:
            _table_constraint(table, item, exp)
    return table


def _column_constraint(table: _Table, column: str, kind, exp) -> None:
    if isinstance(kind, exp.NotNullColumnConstraint):
        if not kind.args.get("allow_null"):
            table.not_null.add(column)
    elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
        table.primary_key.append(column)
    elif isinstance(kind, exp.UniqueColumnConstraint):
        table.unique.append((column,))
    elif isinstance(kind, exp.Reference):
        _reference(table, (column,), kind, exp)


def _table_constraint(table: _Table, item, exp) -> None:
    if isinstance(item, exp.Constraint):
        for inner in item.expressions:
            _table_constraint(table, inner, exp)
    elif isinstance(item, exp.PrimaryKey):
        table.primary_key.extend(_columns_of(item))
    elif isinstance(item, exp.UniqueColumnConstraint):
        columns = _columns_of(item.this)
        if columns:
            table.unique.append(columns)
    elif isinstance(item, exp.ForeignKey):
        reference = item.args.get("reference")
        if reference is not None:
            _reference(table, _columns_of(item), reference, exp)


def _reference(table: _Table, columns: tuple[str, ...], reference, exp) -> None:
    target = reference.this
    if isinstance(target, exp.Schema) and isinstance(target.this, exp.Table):
        table.foreign_keys.append((columns, target.this.name, _columns_of(target)))


def _apply_alter(statement, tables: dict[str, _Table], exp) -> None:
    target = statement.this
    table = tables.get(target.name) if isinstance(target, exp.Table) else None
    if table is None:
        return
    for action in statement.args.get("actions") or []:
        if isinstance(action, exp.AddConstraint):
            for item in action.expressions:
                _table_constraint(table, item, exp)


def _entities(tables: dict[str, _Table], dialect: str, policy: KeyPolicy, notes: list[str]) -> tuple[Entity, ...]:
    shown = {name: table for name, table in tables.items() if policy.shows_name(name)}
    if len(shown) != len(tables):
        notes.append("hid data-like table names (key shape)")
    visible = {name: frozenset(c for c, _ in t.columns if policy.shows_name(c) and f"$.{name}" not in policy.map_paths) for name, t in shown.items()}
    entities = []
    for name, table in shown.items():
        entries = [(c, _column_node(kind, c in table.not_null or c in table.primary_key), _native(kind, dialect)) for c, kind in table.columns]
        node = build_object(entries, f"$.{name}", policy, notes)
        entities.append(_entity(name, table, node, shown, visible))
    return tuple(entities)


def _entity(name: str, table: _Table, node: Object, tables: dict[str, _Table], visible: dict[str, frozenset[str]]) -> Entity:
    own = visible[name]
    primary = tuple(table.primary_key) if table.primary_key and all(c in own for c in table.primary_key) else ()
    keys = []
    for columns, target, target_columns in table.foreign_keys:
        referenced = _find_name(tables, target)
        sources = [_find_name(own, c) for c in columns]
        targets = [_find_name(visible[referenced], c) for c in target_columns] if referenced else []
        if referenced and targets and None not in sources and None not in targets and len(sources) == len(targets):
            keys.append(ForeignKey(tuple(c for c in sources if c), referenced, tuple(c for c in targets if c)))
    single = {columns[0] for columns in table.unique if len(columns) == 1 and columns[0] in own and (columns[0],) != primary}
    node = Object(
        {n: replace(f, unique=True, unique_declared=True) if n in single else f for n, f in node.fields.items()},
        node.extra,
        node.key_format,
    )
    composite = tuple(c for c in table.unique if len(c) > 1 and all(x in own for x in c) and set(c) != set(primary))
    return Entity(name, node, primary, tuple(keys), True, unique_keys=composite)


def _column_node(kind, required: bool) -> Node:
    node = _type_node(kind)
    return node if required or isinstance(node, Unknown) else Union((node, Null()))


def _type_node(kind) -> Node:
    from sqlglot import exp

    if kind is None or not isinstance(kind, exp.DataType):
        return Unknown()
    name = kind.this.name if hasattr(kind.this, "name") else str(kind.this)
    types = exp.DataType.Type
    if kind.this in (types.BOOLEAN,):
        return Boolean()
    if kind.this in (types.DECIMAL, types.BIGDECIMAL, types.DECFLOAT) or name in ("DECIMAL", "NUMERIC", "NUMBER"):
        params = [p.this.this for p in kind.expressions if isinstance(p, exp.DataTypeParam)]
        scale = params[1] if len(params) > 1 else None
        return Integer() if str(scale) == "0" else Number()
    if kind.this in exp.DataType.INTEGER_TYPES or name in ("SERIAL", "BIGSERIAL", "SMALLSERIAL"):
        return Integer()
    if kind.this in exp.DataType.REAL_TYPES or kind.this in exp.DataType.NUMERIC_TYPES:
        return Number()
    if name in ("UUID",):
        return String("uuid")
    if name in ("DATE", "DATE32"):
        return String("date")
    if name in ("TIME", "TIMETZ"):
        return String("time")
    if kind.this in exp.DataType.TEMPORAL_TYPES:
        return String("date-time")
    if kind.this in exp.DataType.TEXT_TYPES or name in ("ENUM", "SET"):
        return String()
    if name in ("BINARY", "VARBINARY", "BLOB", "IMAGE", "BYTEA", "LONGBLOB", "MEDIUMBLOB", "TINYBLOB"):
        return String("binary")
    if name == "ARRAY":
        inner = kind.expressions[0] if kind.expressions else None
        return Array(_type_node(inner))
    return Unknown()


def _native(kind, dialect: str) -> str | None:
    if kind is None:
        return None
    try:
        return clean_native_type(kind.sql(dialect=dialect or None))
    except Exception:
        return None
