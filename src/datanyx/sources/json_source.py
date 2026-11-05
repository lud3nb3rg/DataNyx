"""JSON documents and JSON Lines files."""

from __future__ import annotations

import json

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..schema import DataSchema
from .base import Source, SourceError
from .files import STDIN, extension, open_text, records_observation, reject_namespace, unnamed

# Documents stay unnamed: the filename could itself be sensitive
# ("jane_doe_medical.json"), so it must not appear in the schema.


class JsonSource(Source):
    kind = "json"

    def matches(self, target: str) -> bool:
        return target == STDIN or extension(target) == ".json"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        observation = Observation()
        with open_text(target) as stream:
            try:
                observation.observe(json.load(stream))
            except json.JSONDecodeError as error:
                raise SourceError(_describe_decode_error(error)) from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
        node, notes = infer(observation, policy)
        return unnamed(self.kind, node, notes)


class JsonLinesSource(Source):
    kind = "jsonl"

    def matches(self, target: str) -> bool:
        return extension(target) in (".jsonl", ".ndjson")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        # Modelled as an array of records. Reading line by line means files
        # larger than memory still work, since values are discarded as they pass.
        root = records_observation()
        with open_text(target) as stream:
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
        return unnamed(self.kind, node, notes)


def _describe_decode_error(error: json.JSONDecodeError) -> str:
    # Built by hand so the message can never pick up the document text.
    return f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
