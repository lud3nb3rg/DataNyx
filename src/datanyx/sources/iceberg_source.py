"""Apache Iceberg tables, described from the table's metadata file alone: no data or manifest file is opened.

The metadata JSON holds the schema, the table properties (which can name buckets, owners and the like, and are
never shown) and a summary of each snapshot. Only the schema, and the record count in the current snapshot's
summary for the optional size hint, are used.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from typing import Any

from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import Array, Boolean, DataSchema, Entity, Integer, Node, Null, Number, Object, String, Union, Unknown
from .base import Source, SourceError
from .catalog import build_object, child_path
from .files import reject_namespace

METADATA_FOLDER = "metadata"
_MAX_METADATA_BYTES = 64 * 1024 * 1024
_VERSIONED = re.compile(r"(?:v)?(\d+)(?:-[0-9a-fA-F-]+)?(?:\.gz)?\.metadata\.json")
_DECIMAL = re.compile(r"decimal\(\s*\d+\s*,\s*(\d+)\s*\)")
_PRIMITIVES: dict[str, Node] = {
    "boolean": Boolean(),
    "int": Integer(), "long": Integer(),
    "float": Number(), "double": Number(),
    "date": String("date"),
    "time": String("time"),
    "timestamp": String("date-time"), "timestamptz": String("date-time"),
    "timestamp_ns": String("date-time"), "timestamptz_ns": String("date-time"),
    "string": String(),
    "uuid": String("uuid"),
    "binary": String("binary"),
}
_FIXED = re.compile(r"fixed\[\d+\]")
_SAFE_TYPE = re.compile(r"[a-z_]+(\(\s*\d+\s*(,\s*\d+\s*)?\)|\[\d+\])?")


class IcebergSource(Source):
    kind = "iceberg"

    def matches(self, target: str) -> bool:
        folder = os.path.join(target, METADATA_FOLDER)
        try:
            return os.path.isdir(folder) and any(_VERSIONED.fullmatch(name) for name in os.listdir(folder))
        except OSError:
            return False

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        document = _load(_current_metadata_file(os.path.join(target, METADATA_FOLDER)))
        schema = _current_schema(document)
        if schema is None:
            raise SourceError("the Iceberg metadata has no table schema in it")
        notes: list[str] = []
        fields = schema.get("fields") or []
        node: Node = Array(_struct(fields, "$[]", policy, notes))
        node, unique_keys = _identifier_fields(node, schema, fields)
        scale = scale_for(policy, _current_record_count(document)) if policy.scale_hints else None
        return DataSchema(self.kind, (Entity(None, node, scale=scale, unique_keys=unique_keys),), tuple(notes))


def _current_metadata_file(folder: str) -> str:
    try:
        names = os.listdir(folder)
        hint = os.path.join(folder, "version-hint.text")
        if os.path.isfile(hint):
            with open(hint, encoding="utf-8") as stream:
                wanted = stream.read().strip()
            for name in names:
                match = _VERSIONED.fullmatch(name)
                if match and wanted.isdigit() and int(match.group(1)) == int(wanted):
                    return os.path.join(folder, name)
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    versions = sorted(((int(m.group(1)), name) for name in names if (m := _VERSIONED.fullmatch(name))), reverse=True)
    if not versions:
        raise SourceError("no Iceberg metadata file found")
    return os.path.join(folder, versions[0][1])


def _load(path: str) -> dict[str, Any]:
    try:
        if os.path.getsize(path) > _MAX_METADATA_BYTES:
            raise SourceError("the Iceberg metadata file is too large to read")
        if path.endswith(".gz"):
            import gzip

            with gzip.open(path, "rt", encoding="utf-8") as stream:
                document = json.load(stream)
        else:
            with open(path, encoding="utf-8") as stream:
                document = json.load(stream)
    except SourceError:
        raise
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    except ValueError:
        raise SourceError("the Iceberg metadata isn't valid JSON") from None
    if not isinstance(document, dict):
        raise SourceError("the Iceberg metadata isn't valid")
    return document


def _current_schema(document: dict[str, Any]) -> dict[str, Any] | None:
    schemas = document.get("schemas")
    if isinstance(schemas, list):
        current = document.get("current-schema-id")
        for schema in schemas:
            if isinstance(schema, dict) and schema.get("schema-id") == current:
                return schema
        if schemas and isinstance(schemas[-1], dict):
            return schemas[-1]
    schema = document.get("schema")  # Format version 1.
    return schema if isinstance(schema, dict) else None


def _current_record_count(document: dict[str, Any]) -> int | None:
    current = document.get("current-snapshot-id")
    for snapshot in document.get("snapshots") or []:
        if isinstance(snapshot, dict) and snapshot.get("snapshot-id") == current:
            total = (snapshot.get("summary") or {}).get("total-records")
            try:
                return int(str(total))
            except (TypeError, ValueError):
                return None
    return None


def _identifier_fields(node: Node, schema: dict[str, Any], fields: list[dict]) -> tuple[Node, tuple[tuple[str, ...], ...]]:
    """The fields the table declares as its row identifier: one is flagged unique, several make a unique key."""
    ids = schema.get("identifier-field-ids")
    if not isinstance(ids, list) or not ids or not isinstance(node, Array) or not isinstance(node.items, Object):
        return node, ()
    names = [str(f.get("name")) for f in fields if f.get("id") in ids]
    shown = [name for name in names if name in node.items.fields]
    if len(shown) != len(ids):  # One is hidden, and a partial key would mislead.
        return node, ()
    if len(shown) > 1:
        return node, (tuple(shown),)
    marked = {n: replace(f, unique=True, unique_declared=True) if n == shown[0] else f for n, f in node.items.fields.items()}
    return Array(Object(marked, node.items.extra, node.items.key_format)), ()


def _struct(fields: list[dict], path: str, policy: KeyPolicy, notes: list[str]) -> Object:
    entries = []
    for field in fields:
        name = str(field.get("name", ""))
        node = _type(field.get("type"), child_path(path, name, policy), policy, notes)
        if not field.get("required", False) and not isinstance(node, Unknown):
            node = Union((node, Null()))
        entries.append((name, node, _native(field.get("type"))))
    return build_object(entries, path, policy, notes)


def _type(spec: Any, path: str, policy: KeyPolicy, notes: list[str]) -> Node:
    if isinstance(spec, str):
        if spec in _PRIMITIVES:
            return _PRIMITIVES[spec]
        decimal = _DECIMAL.fullmatch(spec)
        if decimal:
            return Integer() if decimal.group(1) == "0" else Number()
        return String("binary") if _FIXED.fullmatch(spec) else Unknown()
    if not isinstance(spec, dict):
        return Unknown()
    kind = spec.get("type")
    if kind == "struct":
        return _struct(spec.get("fields") or [], path, policy, notes)
    if kind == "list":
        element = _type(spec.get("element"), path + "[]", policy, notes)
        if not spec.get("element-required", False) and not isinstance(element, Unknown):
            element = Union((element, Null()))
        return Array(element)
    if kind == "map":
        return Object({}, _type(spec.get("value"), path + ".{*}", policy, notes))  # Map keys are data.
    return Unknown()


def _native(spec: Any) -> str | None:
    if isinstance(spec, dict):
        kind = spec.get("type")
        return kind if kind in ("struct", "list", "map") else None
    return spec if isinstance(spec, str) and _SAFE_TYPE.fullmatch(spec) else None
