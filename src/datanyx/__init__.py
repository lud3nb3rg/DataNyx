"""DataNyx: describe data to an AI agent without showing it the data."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import replace

from .keys import Evidence, apply_evidence
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .schema import DataSchema
from .selection import apply_selection
from .sources import Source, SourceError, describe_folder, resolve_source
from .sources.files import remote_url
from .text import DEFAULT_MODEL

__version__ = "0.1.0"


def extract(
    target: str,
    source: str | None = None,
    policy: KeyPolicy | None = None,
    namespace: str | None = None,
    recursive: bool = False,
) -> DataSchema:
    """Build a value-free schema for a file path, "-" for stdin, a database URL or a folder.

    `namespace` picks a database schema; the database's default is used otherwise.
    For a folder, every readable file in it is described (and in its subfolders,
    when `recursive` is set); `source` then limits which files are read.
    """
    policy = policy or KeyPolicy()
    target = remote_url(target) or target  # A URL may be named by an environment variable.
    if policy.infer_keys and policy.evidence is None:
        policy = replace(policy, evidence=Evidence())
    try:
        if os.path.isdir(target):
            if namespace is not None:
                raise SourceError("a schema can only be selected for SQL databases")
            schema = describe_folder(target, source, policy, recursive)
        else:
            schema = resolve_source(target, source).extract(target, policy, namespace)
    except RecursionError:
        raise SourceError("input is too deeply nested") from None
    schema = apply_selection(schema, policy)
    if policy.infer_keys and policy.evidence is not None:
        schema = apply_evidence(schema, policy.evidence)
        if not any(e.foreign_keys or _has_unique(e) for e in schema.entities):
            schema = replace(schema, notes=(*schema.notes, "no unique columns or joins were inferred; this kind of source may not support it, or no key-looking column qualified"))
    if policy.scale_hints and not any(entity.scale for entity in schema.entities):
        schema = replace(schema, notes=(*schema.notes, "no row-count hints: this kind of source can't give one without reading it"))
    return schema


def _has_unique(entity) -> bool:
    node = getattr(entity.node, "items", entity.node)
    return any(field.unique for field in getattr(node, "fields", {}).values())


def render(schema: DataSchema, output_format: str = "json-schema") -> str:
    """Render a schema in one of the formats listed in `RENDERERS`."""
    return RENDERERS[output_format](schema)


def schema_of(
    target: str,
    output_format: str = "json-schema",
    *,
    source: str | None = None,
    map_paths: Iterable[str] = (),
    keep_names: Iterable[str] = (),
    namespace: str | None = None,
    recursive: bool = False,
) -> str:
    """The schema of `target` as text, in one call.

    >>> print(datanyx.schema_of("customers.csv", "text"))

    Raises `SourceError` with a message that never quotes the data. Use `extract` and
    `render` instead to get the schema as objects, or to see its `notes`.
    """
    policy = KeyPolicy(map_paths=frozenset(map_paths), keep_names=frozenset(keep_names))
    schema = extract(target, source, policy, namespace, recursive)
    return render(schema, output_format)


def anonymize_text(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    hide: Iterable[str] = (),
    keep: Iterable[str] = (),
    keep_entity: Iterable[str] = (),
) -> str:
    """`text` with names and identifiers replaced by placeholders such as <PERSON_1>.

    Needs `datanyx[text]` and a spaCy model. Best effort: review the result.
    """
    from .text import Detector, PatternDetector, SecretDetector, SpacyDetector, anonymize

    detectors: list[Detector] = [PatternDetector(), SecretDetector(), SpacyDetector(model)]
    return anonymize(text, detectors, hide, keep, keep_entity)


def synthesize(schema: DataSchema, rows: int = 100, seed: int | None = 0):
    """Fake data matching `schema`, as a list of `datanyx.synth.Table`. See `datanyx.synth`."""
    from .synth import synthesize as generate

    return generate(schema, rows, seed)


__all__ = [
    "DataSchema",
    "KeyPolicy",
    "Source",
    "SourceError",
    "anonymize_text",
    "extract",
    "render",
    "schema_of",
    "synthesize",
]
