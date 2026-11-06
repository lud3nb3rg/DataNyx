"""Source-neutral schema model shared by every source and renderer.

Nothing in this module can hold a data value: nodes describe only types,
string format labels, field names and optionality. Sources produce a
`DataSchema`; renderers consume one; neither needs to know about the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class Node:
    """Base class for a node in a schema tree."""


@dataclass(frozen=True)
class Unknown(Node):
    """Nothing was observed, e.g. the items of an array that was always empty."""


@dataclass(frozen=True)
class Null(Node):
    pass


@dataclass(frozen=True)
class Boolean(Node):
    pass


@dataclass(frozen=True)
class Integer(Node):
    pass


@dataclass(frozen=True)
class Number(Node):
    pass


@dataclass(frozen=True)
class String(Node):
    format: str | None = None


@dataclass(frozen=True)
class Array(Node):
    items: Node = Unknown()


@dataclass(frozen=True)
class Field:
    """`native_type` is the source's own type name, such as "VARCHAR(255)"."""

    node: Node
    required: bool = True
    native_type: str | None = None
    #: Set only by `--infer-keys`: the values seen were all different. A guess, not a constraint.
    unique: bool = False
    #: True when the source itself declares the column unique (a constraint or a unique index), so it is no guess.
    unique_declared: bool = False
    #: Set only by `--column-hints`: coarse ranges taken from the values ("10 to 99 distinct values", "rarely missing").
    profile: tuple[str, ...] = ()


@dataclass(frozen=True)
class Object(Node):
    """A record (named `fields`), a map (`extra`), or a mix of the two.

    `extra` describes the values under keys that are not safe to name.
    `key_format` is the format shared by all of those keys, when there is one.
    """

    fields: dict[str, Field] = field(default_factory=dict)
    extra: Node | None = None
    key_format: str | None = None


@dataclass(frozen=True)
class Union(Node):
    options: tuple[Node, ...]


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    referenced_entity: str
    referenced_columns: tuple[str, ...]
    #: True when guessed from the values (`--infer-keys`) rather than declared by the source.
    inferred: bool = False


@dataclass(frozen=True)
class Entity:
    """One thing in a data source, such as a database table or a whole document.

    `name` is None when the thing has no name of its own, as with a JSON document.
    """

    name: str | None
    node: Node
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    #: True when `node` describes one row of many, as for a database table, rather than
    #: a whole document or a list of records.
    is_table: bool = False
    #: Set only by `--scale-hints`: the row count as an order of magnitude, such as "10^3 to 10^4 rows".
    scale: str | None = None
    #: Sets of columns the source declares unique together (a constraint or unique index); one column is on its `Field`.
    unique_keys: tuple[tuple[str, ...], ...] = ()
    #: Sets of columns found unique together by `--infer-keys`. A guess, not a constraint.
    inferred_unique_keys: tuple[tuple[str, ...], ...] = ()
    #: True for a database view: it has columns but no keys of its own, and holds no data itself.
    view: bool = False


@dataclass(frozen=True)
class DataSchema:
    """The complete description of a source.

    `notes` are shown to whoever runs the tool, which may be the agent, so they
    must never contain raw keys or values.
    """

    source_kind: str
    entities: tuple[Entity, ...]
    notes: tuple[str, ...] = ()
