"""Helpers shared by sources that read files."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

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
        return int(stripped)
    if _FLOAT.fullmatch(stripped):
        return float(stripped)
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return text


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
