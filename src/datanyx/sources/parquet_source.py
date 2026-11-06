"""Parquet files, described from the footer alone: no row group is ever read."""

from __future__ import annotations

import re

from ..privacy import KeyPolicy
from ..scale import scale_for
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

# What Arrow's own type names look like. A type can carry text from the file (a timezone, an extension
# type's name), and whatever is in the file must not reach the schema or the SQL made from it.
_TYPE_NAME = re.compile(r"[a-z0-9_]+(\[[a-z0-9_, ]+\]|\(\d+(, ?-?\d+)?\))?")
_TIME_ZONE = re.compile(r"[A-Za-z0-9_+/:-]{1,40}")


class ParquetSource(Source):
    kind = "parquet"

    def matches(self, target: str) -> bool:
        return extension(target) in (".parquet", ".pq")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_path(target, "Parquet")
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise missing_extra("Parquet", "parquet") from None
        try:
            # schema_arrow comes from the footer. Its metadata (which pandas fills
            # with column names and index details) is never looked at.
            parquet_file = pq.ParquetFile(target)
            arrow_schema = parquet_file.schema_arrow
            rows = parquet_file.metadata.num_rows  # Also from the footer.
        except FileNotFoundError:
            raise SourceError("cannot read input: no such file") from None
        except Exception as error:
            raise SourceError(f"input isn't a readable Parquet file ({type(error).__name__})") from None

        notes: list[str] = []
        return unnamed(self.kind, arrow_table_node(arrow_schema, policy, notes, pa), tuple(notes), scale_for(policy, rows))


def arrow_table_node(arrow_schema, policy: KeyPolicy, notes: list[str], pa) -> Node:
    """A table of records, from an Arrow schema. Shared with the other Arrow-backed formats (ORC)."""
    return Array(_struct(list(arrow_schema), "$[]", policy, notes, pa))


def _struct(fields, path: str, policy: KeyPolicy, notes: list[str], pa) -> Object:
    entries = [
        (f.name, _field_node(f, child_path(path, f.name, policy), policy, notes, pa), _native(f.type, pa)) for f in fields
    ]
    return build_object(entries, path, policy, notes)


def _field_node(field, path: str, policy: KeyPolicy, notes: list[str], pa) -> Node:
    node = _type_node(field.type, path, policy, notes, pa)
    if not field.nullable or isinstance(node, Unknown):
        return node
    return Union((node, Null()))


def _type_node(t, path: str, policy: KeyPolicy, notes: list[str], pa) -> Node:
    types = pa.types
    if types.is_dictionary(t):
        return _type_node(t.value_type, path, policy, notes, pa)
    if types.is_boolean(t):
        return Boolean()
    if types.is_integer(t):
        return Integer()
    if types.is_decimal(t):
        return Integer() if t.scale == 0 else Number()
    if types.is_floating(t):
        return Number()
    if types.is_string(t) or types.is_large_string(t):
        return String()
    if types.is_binary(t) or types.is_large_binary(t) or types.is_fixed_size_binary(t):
        return String("binary")
    if types.is_timestamp(t):
        return String("date-time")
    if types.is_date(t):
        return String("date")
    if types.is_time(t):
        return String("time")
    if types.is_null(t):
        return Null()
    if types.is_list(t) or types.is_large_list(t) or types.is_fixed_size_list(t):
        return Array(_field_node(t.value_field, path + "[]", policy, notes, pa))
    if types.is_struct(t):
        return _struct([t.field(i) for i in range(t.num_fields)], path, policy, notes, pa)
    if types.is_map(t):
        # Map keys are data, so none is ever named.
        return Object({}, _field_node(t.item_field, path + ".{*}", policy, notes, pa))
    return Unknown()


def _native(t, pa) -> str | None:
    """The Arrow type's name. Nested types print their field names, so they're summarised."""
    if pa.types.is_dictionary(t):
        return "dictionary"
    if pa.types.is_nested(t):
        return "struct" if pa.types.is_struct(t) else "map" if pa.types.is_map(t) else "list"
    if pa.types.is_timestamp(t):
        return f"timestamp[{t.unit}, tz={t.tz}]" if t.tz and _TIME_ZONE.fullmatch(t.tz) else f"timestamp[{t.unit}]"
    text = str(t)
    return text if _TYPE_NAME.fullmatch(text) else None
