"""Avro container files, described from the schema in the file header: no data block is read.

Enum symbols are values in all but name, so an enum is a plain string and its symbols are never
shown. Record and field names go through the same rules as any other name, and documentation
strings, defaults and the file's own metadata are never looked at.
"""

from __future__ import annotations

from ..privacy import KeyPolicy
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
from .base import Source, SourceError
from .catalog import build_object, child_path
from .files import extension, missing_extra, reject_namespace, require_path, unnamed

_MAX_DEPTH = 40
_PRIMITIVES: dict[str, Node] = {
    "null": Null(),
    "boolean": Boolean(),
    "int": Integer(),
    "long": Integer(),
    "float": Number(),
    "double": Number(),
    "bytes": String("binary"),
    "string": String(),
}
_LOGICAL: dict[str, Node] = {
    "date": String("date"),
    "time-millis": String("time"),
    "time-micros": String("time"),
    "timestamp-millis": String("date-time"),
    "timestamp-micros": String("date-time"),
    "timestamp-nanos": String("date-time"),
    "local-timestamp-millis": String("date-time"),
    "local-timestamp-micros": String("date-time"),
    "uuid": String("uuid"),
    "decimal": Number(),
}


class AvroSource(Source):
    kind = "avro"

    def matches(self, target: str) -> bool:
        return extension(target) == ".avro"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_path(target, "Avro")
        try:
            import fastavro
        except ImportError:
            raise missing_extra("Avro", "avro") from None
        try:
            with open(target, "rb") as stream:
                writer_schema = fastavro.reader(stream).writer_schema  # Parsed from the header.
        except FileNotFoundError:
            raise SourceError("cannot read input: no such file") from None
        except OSError as error:
            raise SourceError(f"cannot read input: {error.strerror}") from None
        except Exception as error:
            raise SourceError(f"input isn't a readable Avro file ({type(error).__name__})") from None
        notes: list[str] = []
        node = _node(writer_schema, "$[]", policy, notes, {}, 0)
        # A file holds a sequence of values of the schema's type: a table when that is a record.
        return unnamed(self.kind, Array(node), tuple(notes))


def _node(schema, path: str, policy: KeyPolicy, notes: list[str], named: dict[str, Node | None], depth: int) -> Node:
    if depth > _MAX_DEPTH:
        return Unknown()
    if isinstance(schema, str):
        if schema in _PRIMITIVES:
            return _PRIMITIVES[schema]
        if schema in named:  # A reference to a type defined earlier.
            return named[schema] or Unknown()  # None: still being defined, so a recursive type.
        return Unknown()
    if isinstance(schema, list):
        flat: list[Node] = []
        for option in schema:
            node = _node(option, path, policy, notes, named, depth + 1)
            for inner in node.options if isinstance(node, Union) else (node,):
                if inner not in flat:
                    flat.append(inner)
        return flat[0] if len(flat) == 1 else Union(tuple(flat))
    if not isinstance(schema, dict):
        return Unknown()

    kind = schema.get("type")
    logical = schema.get("logicalType")
    if isinstance(logical, str) and logical in _LOGICAL and kind in ("int", "long", "bytes", "fixed", "string"):
        return _LOGICAL[logical]
    if kind in ("record", "error"):
        full = str(schema.get("name", ""))
        named[full] = None
        entries = []
        for position, avro_field in enumerate(schema.get("fields", []), start=1):
            name = str(avro_field.get("name", f"field_{position}"))
            node = _node(avro_field.get("type"), child_path(path, name, policy), policy, notes, named, depth + 1)
            entries.append((name, node, _native(avro_field.get("type"))))
        built = build_object(entries, path, policy, notes)
        named[full] = built
        return built
    if kind == "array":
        return Array(_node(schema.get("items"), path + "[]", policy, notes, named, depth + 1))
    if kind == "map":
        # Map keys are data, so none is ever named.
        return Object({}, _node(schema.get("values"), path + ".{*}", policy, notes, named, depth + 1))
    if kind == "enum":
        named[str(schema.get("name", ""))] = String()
        return String()  # The symbols are values.
    if kind == "fixed":
        named[str(schema.get("name", ""))] = String("binary")
        return String("binary")
    if isinstance(kind, (str, list, dict)):
        return _node(kind, path, policy, notes, named, depth + 1)  # {"type": "string"} and the like.
    return Unknown()


def _native(schema) -> str | None:
    """The Avro type's name. Nested and named types are summarised, as their names are the writer's."""
    if isinstance(schema, str):
        return schema if schema in _PRIMITIVES else "named"
    if isinstance(schema, list):
        return "union"
    if isinstance(schema, dict):
        kind = schema.get("type")
        return kind if isinstance(kind, str) and kind in ("record", "array", "map", "enum", "fixed") else _native(kind)
    return None
