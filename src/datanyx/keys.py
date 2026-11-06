"""Opt-in inference of unique columns and of joins between tables, as yes/no flags.

A schema from a file has no keys, so an agent can't tell that `orders.customer_id` points at
`customers.id`. With `--infer-keys` the sources that read every row of a table (CSV, TSV, JSON Lines,
Excel and JSON arrays) also hand their key-looking columns to an `Evidence` object while they read.
It keeps a hash of each value, in memory only, and what comes out is a flag: "this column is unique" or
"this column appears to reference that one". Never a count, never a value.

To keep the flags from saying more than they should:

- only columns whose *names* look like keys (`id`, `user_id`, `uuid`, `code`, ...) are examined;
- a table needs at least `MIN_ROWS` rows, and a column no nulls and no repeats, to be called unique;
- a join needs a name match as well as the values being contained, because small integer ids are
  contained in each other all the time;
- a column with more than `MAX_DISTINCT` distinct values is given up on, which bounds the memory used.

The flags are guesses from the data seen: the output says "inferred", and nothing enforces them.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from typing import Any

from .schema import Array, DataSchema, ForeignKey, Node, Object

MIN_ROWS = 20
MAX_DISTINCT = 200_000
_SEPARATOR = "#"
_SNAKE_KEY = re.compile(r"(^|[_\-\s.])(id|uuid|guid|key|code|ref|reference|number|num|no)$")
_CAMEL_KEY = re.compile(r"[a-z0-9]Id$")


def is_key_name(name: str) -> bool:
    return bool(_SNAKE_KEY.search(name.lower()) or _CAMEL_KEY.search(name))


class _Column:
    """What was seen in one candidate column: hashes of its values, and how clean they were."""

    __slots__ = ("duplicate", "invalid", "non_null", "overflow", "values")

    def __init__(self) -> None:
        self.values: set[int] = set()
        self.non_null = 0
        self.duplicate = False
        self.overflow = False
        self.invalid = False  # A kind of value that can't be a key: a float, a list, a boolean.

    def add(self, value: object) -> None:
        if value is None or value == "" or self.invalid:
            return
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            self.invalid = True
            self.values.clear()
            return
        self.non_null += 1
        if self.overflow:
            return
        digest = hash(str(value))
        if digest in self.values:
            self.duplicate = True
            return
        if len(self.values) >= MAX_DISTINCT:
            self.overflow = True
            self.values.clear()
            return
        self.values.add(digest)

    def usable(self) -> bool:
        return not self.invalid and not self.overflow


class TableEvidence:
    def __init__(self) -> None:
        self.rows = 0
        self.columns: dict[str, _Column | None] = {}  # None: not a key-looking name.

    def add(self, record: Any) -> None:
        if not isinstance(record, dict):
            return
        self.rows += 1
        for name, value in record.items():
            if not isinstance(name, str):
                continue
            if name not in self.columns:
                self.columns[name] = _Column() if is_key_name(name) else None
            column = self.columns[name]
            if column is not None:
                column.add(value)

    def is_unique(self, name: str) -> bool:
        column = self.columns.get(name)
        return (
            column is not None
            and column.usable()
            and not column.duplicate
            and self.rows >= MIN_ROWS
            and column.non_null == self.rows
        )

    def can_reference(self, name: str) -> _Column | None:
        column = self.columns.get(name)
        if column is None or not column.usable() or column.non_null < MIN_ROWS or len(column.values) < 2:
            return None
        return column


class Evidence:
    """The key evidence of one `extract` call, keyed by the name each table ends up with."""

    def __init__(self) -> None:
        #: Set by a folder reader to the file being read, so a table is named as the folder will name it.
        self.scope: str | None = None
        self.tables: dict[str | None, TableEvidence] = {}

    def table(self, name: str | None) -> TableEvidence:
        key = name if self.scope is None else (self.scope if name is None else f"{self.scope}{_SEPARATOR}{name}")
        return self.tables.setdefault(key, TableEvidence())


def apply_evidence(schema: DataSchema, evidence: Evidence) -> DataSchema:
    """The schema with `unique` flags on key columns and inferred foreign keys between tables."""
    entities = list(schema.entities)
    fields = [_fields_of(entity.node) for entity in entities]
    tables = [evidence.tables.get(entity.name) for entity in entities]
    unique = [
        {name for name in fields[i] if table is not None and table.is_unique(name)}
        for i, table in enumerate(tables)
    ]

    found = _candidate_joins(entities, fields, tables, unique)
    # Two tables that each hold the other's key under one name are a tie, not a join: neither is the parent.
    pairs = {(child, name, parent, target) for child, name, parent, target in found}
    found = [item for item in found if (item[2], item[3], item[0], item[1]) not in pairs]
    keys: list[list[ForeignKey]] = [list(entity.foreign_keys) for entity in entities]
    for child, name, parent, target in found:
        keys[child].append(ForeignKey((name,), entities[parent].name or "", (target,), inferred=True))
    joins = len(found)

    flagged = sum(len(columns) for columns in unique)
    if not flagged and not joins:
        return schema
    result = [
        replace(entity, node=_mark_unique(entity.node, unique[i]), foreign_keys=tuple(keys[i]))
        for i, entity in enumerate(entities)
    ]
    note = f"inferred {flagged} unique column(s) and {joins} join(s) from the values; they are guesses, and nothing enforces them"
    return DataSchema(schema.source_kind, tuple(result), (*schema.notes, note))


def _fields_of(node: Node) -> dict[str, Any]:
    node = node.items if isinstance(node, Array) else node
    return dict(node.fields) if isinstance(node, Object) else {}


def _mark_unique(node: Node, columns: set[str]) -> Node:
    if not columns:
        return node
    if isinstance(node, Array):
        return Array(_mark_unique(node.items, columns))
    if isinstance(node, Object):
        marked = {name: replace(field, unique=True) if name in columns else field for name, field in node.fields.items()}
        return Object(marked, node.extra, node.key_format)
    return node


def _base(name: str | None) -> str:
    """`customers` from `data/customers.csv` or `book.xlsx#customers`."""
    if not name:
        return ""
    part = name.rsplit(_SEPARATOR, 1)[-1]
    return os.path.splitext(part.rsplit("/", 1)[-1])[0]


def _singular(word: str) -> str:
    lowered = word.lower()
    if lowered.endswith("ies") and len(lowered) > 3:
        return lowered[:-3] + "y"
    if lowered.endswith("ses") and len(lowered) > 3:
        return lowered[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss") and len(lowered) > 1:
        return lowered[:-1]
    return lowered


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_match(child_column: str, parent_table: str, parent_column: str) -> bool:
    """Whether the child's column name points at the parent: `user_id` with `users.id`, or `ref_id` with `ref_id`."""
    child, target = _norm(child_column), _norm(parent_column)
    if child == target and target != "id":
        return True  # The same specific name in both tables.
    return bool(parent_table) and child in {_norm(_singular(parent_table)) + target, _norm(parent_table) + target}



def _candidate_joins(entities, fields, tables, unique) -> list[tuple[int, str, int, str]]:
    """(child table, child column, parent table, parent column) for every column that looks like a reference."""
    found: list[tuple[int, str, int, str]] = []
    for child, table in enumerate(tables):
        if table is None:
            continue
        declared = {column for key in entities[child].foreign_keys for column in key.columns}
        for name in fields[child]:
            column = None if name in declared else table.can_reference(name)
            if column is None:
                continue
            for parent, parent_table in enumerate(tables):
                if parent == child or parent_table is None:
                    continue
                target = next(
                    (
                        candidate
                        for candidate in sorted(unique[parent])
                        if parent_table.columns[candidate] is not None
                        and _name_match(name, _base(entities[parent].name), candidate)
                        and column.values <= parent_table.columns[candidate].values
                    ),
                    None,
                )
                if target is not None:
                    found.append((child, name, parent, target))
                    break
    return found
