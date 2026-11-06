"""FHIR resources: one entity per resource type, whether in a Bundle, a single resource or a bulk export.

A Bundle mixes resource types, so describing it as one JSON document would blend a Patient with an
Observation. Here each `resourceType` gets its own entity and the fields are inferred from the resources
of that type, the same way a JSON file's are. Values are never kept: only types and field names.

The resource type is a FHIR name and shows only if it looks like one (a capitalised word); anything else
is called `Resource_N`. `.fhir.json`, `.fhir.ndjson` and `.fhir` files are recognised by name; for any
other file, pass `--source fhir`.
"""

from __future__ import annotations

import json
import re

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..schema import DataSchema, Entity
from .base import Source, SourceError
from .files import extension, open_text, records_observation, reject_namespace

_RESOURCE_TYPE = re.compile(r"[A-Z][A-Za-z]{2,40}")
_MAX_TYPES = 200
_LINE_FORMATS = (".ndjson", ".jsonl")


class FhirSource(Source):
    kind = "fhir"

    def matches(self, target: str) -> bool:
        lowered = target.lower()
        return lowered.endswith((".fhir.json", ".fhir.ndjson", ".fhir.jsonl", ".fhir"))

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        groups: dict[str, Observation] = {}
        notes: list[str] = []
        skipped = 0
        for document in _documents(target):
            for resource in _resources(document):
                name = resource.get("resourceType") if isinstance(resource, dict) else None
                if not isinstance(name, str) or not _RESOURCE_TYPE.fullmatch(name):
                    skipped += 1
                    continue
                if name not in groups and len(groups) >= _MAX_TYPES:
                    continue
                groups.setdefault(name, records_observation()).observe_item(resource)

        if not groups:
            raise SourceError("input has no FHIR resources (objects with a resourceType)")
        entities = []
        for position, (name, root) in enumerate(sorted(groups.items()), start=1):
            shown = name if policy.shows_name(name) else f"Resource_{position}"
            node, inferred = infer(root, policy, f"$.{shown}")
            notes.extend(inferred)
            entities.append(Entity(shown, node))
        if skipped:
            notes.append(f"skipped {skipped} object(s) that aren't FHIR resources")
        return DataSchema(self.kind, tuple(entities), tuple(notes))


def _documents(target: str):
    """The JSON documents in the file: one, or one per line for NDJSON."""
    with open_text(target) as stream:
        try:
            if extension(target) in _LINE_FORMATS or target.lower().endswith((".fhir.ndjson", ".fhir.jsonl")):
                for line in stream:
                    if line.strip():
                        yield json.loads(line)
            else:
                yield json.load(stream)
        except json.JSONDecodeError:
            raise SourceError("input isn't valid JSON") from None
        except UnicodeDecodeError:
            raise SourceError("input is not valid UTF-8") from None


def _resources(document):
    """The resources in a document: a Bundle's entries, or the document itself."""
    if isinstance(document, list):
        for item in document:
            yield from _resources(item)
    elif isinstance(document, dict):
        if document.get("resourceType") == "Bundle":
            for entry in document.get("entry") or []:
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict):
                    yield entry["resource"]
        else:
            yield document
