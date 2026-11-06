"""Delta Lake tables, described from the transaction log alone: no data file is opened.

The table's schema is in the `metaData` action of the log, a JSON document of names and types. The log also lists
the data files (with their paths and partition values, which can be data), so only the record counts in it are
used, and only for the optional size hint.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import Array, Boolean, DataSchema, Integer, Node, Null, Number, Object, String, Union, Unknown
from .base import Source, SourceError
from .catalog import build_object, child_path
from .files import reject_namespace, unnamed

LOG_FOLDER = "_delta_log"
_COMMIT = re.compile(r"\d{20}\.json")
_MAX_LOG_LINE = 64 * 1024 * 1024
_MAX_COMMITS_FOR_ROW_COUNT = 1000
_PRIMITIVES: dict[str, Node] = {
    "string": String(),
    "long": Integer(), "integer": Integer(), "short": Integer(), "byte": Integer(),
    "float": Number(), "double": Number(),
    "boolean": Boolean(),
    "binary": String("binary"),
    "date": String("date"),
    "timestamp": String("date-time"), "timestamp_ntz": String("date-time"),
}
_DECIMAL = re.compile(r"decimal\(\s*\d+\s*,\s*(\d+)\s*\)")
_SAFE_TYPE = re.compile(r"[a-z_]+(\(\s*\d+\s*(,\s*\d+\s*)?\))?")


class DeltaSource(Source):
    kind = "delta"

    def matches(self, target: str) -> bool:
        return os.path.isdir(os.path.join(target, LOG_FOLDER))

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        log = os.path.join(target, LOG_FOLDER)
        try:
            commits = sorted(name for name in os.listdir(log) if _COMMIT.fullmatch(name))
            has_checkpoint = any(name.endswith(".checkpoint.parquet") or name == "_last_checkpoint" for name in os.listdir(log))
        except OSError as error:
            raise SourceError(f"cannot read input: {error.strerror}") from None
        schema_string = _latest_schema_string(log, commits)
        if schema_string is None:
            if has_checkpoint:
                schema_string = _schema_from_checkpoint(log)
            if schema_string is None:
                raise SourceError("the Delta log has no table schema in it")
        try:
            document = json.loads(schema_string)
            fields = document["fields"]
        except (ValueError, KeyError, TypeError):
            raise SourceError("the Delta table's schema isn't readable") from None

        notes: list[str] = []
        node = Array(_struct(fields, "$[]", policy, notes))
        rows = None
        if policy.scale_hints:
            rows = None if has_checkpoint or len(commits) > _MAX_COMMITS_FOR_ROW_COUNT else _count_rows(log, commits)
            if rows is None:
                notes.append("no row-count hint: the Delta log has a checkpoint, is long, or lacks file statistics")
        return unnamed(self.kind, node, tuple(notes), scale_for(policy, rows))


def _actions(path: str):
    """The actions in one commit file, one JSON object per line."""
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if line.strip() and len(line) <= _MAX_LOG_LINE:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        yield value
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None


def _latest_schema_string(log: str, commits: list[str]) -> str | None:
    for name in reversed(commits):
        found = None
        for action in _actions(os.path.join(log, name)):
            meta = action.get("metaData")
            if isinstance(meta, dict) and isinstance(meta.get("schemaString"), str):
                found = meta["schemaString"]  # The last one in a commit wins.
        if found is not None:
            return found
    return None


def _schema_from_checkpoint(log: str) -> str | None:
    """The table schema from a checkpoint file, when the commits that set it have been cleaned up."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise SourceError("this Delta table's log was checkpointed; reading it needs datanyx[parquet]") from None
    for name in sorted(os.listdir(log), reverse=True):
        if not name.endswith(".checkpoint.parquet"):
            continue
        try:
            table = pq.read_table(os.path.join(log, name), columns=["metaData"])
            for cell in table.column("metaData").to_pylist():
                if cell and isinstance(cell.get("schemaString"), str):
                    return str(cell["schemaString"])
        except Exception:
            continue
    return None


def _count_rows(log: str, commits: list[str]) -> int | None:
    files: dict[str, int | None] = {}
    for name in commits:
        for action in _actions(os.path.join(log, name)):
            add = action.get("add")
            if isinstance(add, dict) and isinstance(add.get("path"), str):
                files[add["path"]] = _records_in(add.get("stats"))
            remove = action.get("remove")
            if isinstance(remove, dict):
                files.pop(str(remove.get("path")), None)
    if not files or any(count is None for count in files.values()):
        return None
    return sum(count for count in files.values() if count is not None)


def _records_in(stats: Any) -> int | None:
    try:
        value = json.loads(stats)["numRecords"] if isinstance(stats, str) else None
    except (ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _struct(fields: list[dict], path: str, policy: KeyPolicy, notes: list[str]) -> Object:
    entries = []
    for field in fields:
        name = str(field.get("name", ""))
        node = _type(field.get("type"), child_path(path, name, policy), policy, notes)
        if field.get("nullable", True) and not isinstance(node, Unknown):
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
        return Unknown()
    if not isinstance(spec, dict):
        return Unknown()
    kind = spec.get("type")
    if kind == "struct":
        return _struct(spec.get("fields") or [], path, policy, notes)
    if kind == "array":
        element = _type(spec.get("elementType"), path + "[]", policy, notes)
        if spec.get("containsNull", True) and not isinstance(element, Unknown):
            element = Union((element, Null()))
        return Array(element)
    if kind == "map":
        value = _type(spec.get("valueType"), path + ".{*}", policy, notes)
        return Object({}, value)  # Map keys are data, so none is named.
    return Unknown()


def _native(spec: Any) -> str | None:
    if isinstance(spec, dict):
        kind = spec.get("type")
        return kind if kind in ("struct", "array", "map") else None
    return spec if isinstance(spec, str) and _SAFE_TYPE.fullmatch(spec) else None
