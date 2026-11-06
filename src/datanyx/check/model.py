"""What the checkers know about the data: tables, columns and their kinds.

Everything here comes from a `DataSchema`, so it holds only names the schema shows.
That matters for what a check can reveal: a name that is hidden is simply absent, so
asking about it gives the same answer as asking about a name that doesn't exist. A
checker can't be used to test guesses about hidden names.
"""

from __future__ import annotations

import difflib
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..schema import (
    Array,
    Boolean,
    DataSchema,
    Integer,
    Node,
    Null,
    Number,
    Object,
    String,
    Union,
    Unknown,
)

NUMERIC = frozenset({"integer", "number"})
TEXT = frozenset({"string", "uuid"})
TEMPORAL = frozenset({"date", "datetime", "time"})


@dataclass(frozen=True)
class Diagnostic:
    """One finding. `error` means the code can't work; `warning` means it probably doesn't."""

    severity: str
    message: str
    line: int = 1
    column: int | None = None
    hint: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class Column:
    """`kind` is one of integer, number, boolean, string, uuid, date, datetime, time, binary,
    array, object, mixed or unknown."""

    kind: str = "unknown"
    nullable: bool = True

    @property
    def known(self) -> bool:
        return self.kind not in ("unknown", "mixed")


@dataclass
class Table:
    """A table, or anything table-shaped: a CSV file, a sheet, a list of JSON records."""

    name: str
    columns: dict[str, Column]
    #: True when some column names are hidden, so a name missing here may still exist.
    open: bool = False
    #: Other names the table answers to, such as a file's name with its extension.
    aliases: frozenset[str] = frozenset()
    #: Where the data came from (`csv`, `json`, `sql`...), which says how a reader will type it.
    source_kind: str = ""
    _lower: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._lower = {name.lower(): name for name in self.columns}

    def column(self, name: str, case_sensitive: bool = False) -> Column | None:
        if name in self.columns:
            return self.columns[name]
        if not case_sensitive and name.lower() in self._lower:
            return self.columns[self._lower[name.lower()]]
        return None

    def answers_to(self, name: str) -> bool:
        wanted = name.lower()
        return wanted == self.name.lower() or wanted in {alias.lower() for alias in self.aliases}


def tables_from_schema(schema: DataSchema, single_name: str | None = None) -> list[Table]:
    """One `Table` per entity. An unnamed entity is called `single_name`, or `data`."""
    tables = []
    for entity in schema.entities:
        node = entity.node.items if isinstance(entity.node, Array) else entity.node
        if not isinstance(node, Object):
            continue  # A list of plain values has no columns to check.
        name = entity.name or single_name or "data"
        tables.append(
            Table(
                name,
                {column: _column(field.node) for column, field in node.fields.items()},
                node.extra is not None,
                frozenset(_aliases(name)),
                _source_kind(schema.source_kind, name),
            )
        )
    return tables


_EXTENSION_KINDS = {
    ".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".yaml": "yaml", ".yml": "yaml", ".xml": "xml",
}


def _source_kind(kind: str, name: str) -> str:
    """For a folder, the kind comes from each file's extension."""
    if kind != "directory":
        return kind
    return _EXTENSION_KINDS.get(os.path.splitext(name)[1].lower(), "")


def _aliases(name: str) -> set[str]:
    """`reports/orders.csv` also answers to `orders.csv` and `orders`; `a.db#items` to `items`."""
    last = re.split(r"[/#\\]", name)[-1]
    return {name, last, last.split(".")[0] if "." in last else last}


def _column(node: Node) -> Column:
    options = node.options if isinstance(node, Union) else (node,)
    nullable = any(isinstance(option, (Null, Unknown)) for option in options)
    kinds = {_kind(option) for option in options if not isinstance(option, (Null, Unknown))}
    if not kinds:
        return Column("unknown", nullable)
    if len(kinds) == 1:
        return Column(next(iter(kinds)), nullable)
    return Column("number" if kinds <= NUMERIC else "mixed", nullable)


def _kind(node: Node) -> str:
    if isinstance(node, Integer):
        return "integer"
    if isinstance(node, Number):
        return "number"
    if isinstance(node, Boolean):
        return "boolean"
    if isinstance(node, String):
        return {"date": "date", "date-time": "datetime", "time": "time", "uuid": "uuid", "binary": "binary"}.get(
            node.format or "", "string"
        )
    if isinstance(node, Array):
        return "array"
    if isinstance(node, Object):
        return "object"
    return "unknown"


def suggest(name: str, candidates: Iterable[str], case_sensitive: bool = False) -> str | None:
    """A hint for a name that wasn't found: the same name in other case, or a close one."""
    options = list(candidates)
    for option in options:
        if option.lower() == name.lower() and option != name:
            return f"did you mean {option!r}? Names are case-sensitive here" if case_sensitive else f"did you mean {option!r}?"
    close = difflib.get_close_matches(name, options, n=3, cutoff=0.6)
    if not close:
        return None
    return "did you mean " + " or ".join(repr(match) for match in close) + "?"

