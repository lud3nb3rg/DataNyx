"""Helpers shared by sources that read files."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from ..formats import detect_format
from ..inference import Observation
from ..schema import Array, DataSchema, Entity, Node
from .base import SourceError

STDIN = "-"


def extension(target: str) -> str:
    return os.path.splitext(target)[1].lower()


def reject_namespace(namespace: str | None) -> None:
    if namespace is not None:
        raise SourceError("a schema can only be selected for SQL databases")


def require_path(target: str, what: str) -> None:
    """For binary formats, which can't be streamed from stdin."""
    if target == STDIN:
        raise SourceError(f"{what} can't be read from stdin; pass a file path")


@contextmanager
def open_text(target: str) -> Iterator[IO[str]]:
    if target == STDIN:
        # Windows consoles and pipes rarely default to UTF-8, so decode explicitly.
        stream = open(sys.stdin.fileno(), encoding="utf-8-sig", closefd=False)
        yield stream
        return
    try:
        stream = open(target, encoding="utf-8-sig")  # -sig tolerates a Windows BOM.
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    with stream:
        yield stream


def read_bytes(target: str) -> bytes:
    try:
        if target == STDIN:
            return sys.stdin.buffer.read()
        with open(target, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None


def sqlite_read_only_uri(path: str | Path) -> str:
    """A URI that opens a SQLite file read-only, whatever its name holds: `#`, `?` and `%` are escaped."""
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def missing_extra(what: str, extra: str) -> SourceError:
    return SourceError(f"reading {what} needs an extra package; install datanyx[{extra}]")


def records_observation() -> Observation:
    """An observation of an array whose items are fed in one at a time."""
    root = Observation()
    root.seen = root.arrays = 1
    return root


def unnamed(kind: str, node: Node, notes: tuple[str, ...], scale: str | None = None) -> DataSchema:
    return DataSchema(kind, (Entity(None, node, scale=scale),), notes)


def items_seen(observation: Observation) -> int | None:
    """How many records a source read, when what it read is a list of them."""
    if observation.arrays and observation.items is not None:
        return observation.items.seen
    return None


EMPTY_TABLE: Node = Array()

_INTEGER = re.compile(r"[+-]?(0|[1-9]\d*)")
_FLOAT = re.compile(r"[+-]?((0|[1-9]\d*)(\.\d+)?|\.\d+)([eE][+-]?\d+)?")


def coerce_text(text: str) -> object:
    """Give text from an untyped format (CSV, XML) the type it plainly has.

    Leading zeros keep a value a string, since "007" is an identifier, not a number.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if _INTEGER.fullmatch(stripped):
        try:
            return int(stripped)
        except ValueError:  # Past Python's limit of digits, so it's no ordinary number.
            return text
    if _FLOAT.fullmatch(stripped):
        return float(stripped)
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return text


HEADERLESS_NOTE = (
    "the first row looked like data rather than a header, so the columns are called column_N; "
    "if it is a header, list its names under keep_names or with --keep-name"
)
# Backwards-compatible alias used by older call sites.
FIRST_ROW_IS_DATA = HEADERLESS_NOTE
_CAMEL_CASE = re.compile(r"[a-z0-9][A-Z]")
_LOWERCASE_NAME = re.compile(r"[a-z][a-z0-9 .-]*")
_CELL_WORDS = re.compile(r"[a-z]+")
# Words that head columns. Capitalised words alone can't separate "Name,City" from "Jane,Portland".
_HEADER_WORDS = frozenset(
    ["id", "name", "first", "last", "full", "middle", "email", "mail", "phone", "mobile", "fax", "tel", "address", "street", "city", "town", "state", "province", "country", "region", "zip", "postal", "postcode", "date", "time", "year", "month", "day", "week", "quarter", "age", "dob", "birth", "gender", "sex", "title", "status", "type", "kind", "category", "class", "group", "code", "number", "num", "no", "key", "ref", "reference", "value", "amount", "total", "sum", "count", "qty", "quantity", "price", "cost", "rate", "tax", "fee", "balance", "currency", "salary", "revenue", "sales", "profit", "percent", "score", "rank", "level", "grade", "description", "desc", "details", "notes", "note", "comment", "comments", "text", "message", "subject", "summary", "body", "url", "link", "website", "user", "username", "login", "password", "customer", "client", "contact", "company", "organization", "organisation", "org", "department", "dept", "team", "employee", "manager", "owner", "author", "product", "item", "sku", "order", "invoice", "account", "payment", "transaction", "created", "updated", "modified", "deleted", "start", "end", "active", "enabled", "flag", "latitude", "longitude", "lat", "lon", "lng", "size", "length", "width", "height", "weight", "color", "colour", "language", "file", "path", "host", "ip", "version", "source", "target", "label", "tag", "field", "column", "row", "index", "position", "role", "location", "site", "place", "project", "task", "patient", "doctor", "provider", "diagnosis"]
)


def first_row_is_data(cells: Iterable[object], keep_names: Collection[str] = ()) -> bool:
    """Whether a table's first row is a record, not a header, so its cells must not become names.

    Nothing marks a header, so this looks for what headers have and data doesn't. A name the user
    confirmed, a snake_case, camelCase or lowercase name, or mostly words that head columns, make
    it a header. An email or a date makes it data, as does a row with none of those signs:
    `Jane,Doe,Oncology,Portland`. Mistaking a header costs some names; mistaking data shows it.
    """
    present = [cell for cell in cells if cell is not None and str(cell).strip()]
    if not present:
        return False
    texts = [cell for cell in present if isinstance(cell, str)]
    if any(cell in keep_names for cell in texts):
        return False
    if any(detect_format(cell.strip()) for cell in texts):
        return True
    if any("_" in cell or _CAMEL_CASE.search(cell) or _LOWERCASE_NAME.fullmatch(cell.strip()) for cell in texts):
        return False
    familiar = sum(any(word in _HEADER_WORDS for word in _CELL_WORDS.findall(cell.lower())) for cell in texts)
    return familiar * 2 < len(present)


def row_is_data(row: list[str]) -> bool:
    """Backward-compatible wrapper for older call sites."""
    return first_row_is_data(row)


def positional_names(count: int) -> list[str]:
    return [f"column_{position}" for position in range(1, count + 1)]


def column_names(header: list[object]) -> list[str]:
    """Unique names for header cells. Empty ones get a positional name, never a value."""
    names: list[str] = []
    seen: set[str] = set()
    for position, cell in enumerate(header, start=1):
        # Not stripped: "name " has a space, which the privacy rules read as data. Trimming
        # it first would turn a hidden name into a plain one.
        name = "" if cell is None else str(cell)
        name = name if name.strip() else f"column_{position}"
        base, suffix = name, 2
        while name in seen:
            name, suffix = f"{base}_{suffix}", suffix + 1
        seen.add(name)
        names.append(name)
    return names
