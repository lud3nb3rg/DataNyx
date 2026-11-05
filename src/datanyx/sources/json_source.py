"""JSON documents and JSON Lines files."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from typing import IO, Iterator

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..schema import DataSchema, Entity
from .base import Source, SourceError

STDIN = "-"
# Documents stay unnamed: the filename could itself be sensitive
# ("jane_doe_medical.json"), so it must not appear in the schema.


class JsonSource(Source):
    kind = "json"

    def matches(self, target: str) -> bool:
        return target == STDIN or _extension(target) == ".json"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        _reject_namespace(namespace)
        observation = Observation()
        with _open_text(target) as stream:
            try:
                observation.observe(json.load(stream))
            except json.JSONDecodeError as error:
                raise SourceError(_describe_decode_error(error)) from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
        node, notes = infer(observation, policy)
        return DataSchema(self.kind, (Entity(None, node),), notes)


class JsonLinesSource(Source):
    kind = "jsonl"

    def matches(self, target: str) -> bool:
        return _extension(target) in (".jsonl", ".ndjson")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        _reject_namespace(namespace)
        # Modelled as an array of records. Reading line by line means files
        # larger than memory still work, since values are discarded as they pass.
        root = Observation()
        root.seen = root.arrays = 1
        with _open_text(target) as stream:
            line_number = 0
            try:
                for line_number, line in enumerate(stream, start=1):
                    if line.strip():
                        root.observe_item(json.loads(line))
            except json.JSONDecodeError as error:
                detail = f"line {line_number}, column {error.colno}: {error.msg}"
                raise SourceError(f"invalid JSON at {detail}") from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
        node, notes = infer(root, policy)
        return DataSchema(self.kind, (Entity(None, node),), notes)


def _reject_namespace(namespace: str | None) -> None:
    if namespace is not None:
        raise SourceError("a schema can only be selected for SQL databases")


def _extension(target: str) -> str:
    return os.path.splitext(target)[1].lower()


def _describe_decode_error(error: json.JSONDecodeError) -> str:
    # Built by hand so the message can never pick up the document text.
    return f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"


@contextmanager
def _open_text(target: str) -> Iterator[IO[str]]:
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
