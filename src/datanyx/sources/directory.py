"""Describe every readable file in a folder as one schema.

File names can be as sensitive as what's inside ("jane_doe_medical.csv"), so a
name is shown only if the same rules that apply to a column name accept it. Any
other file is called `file_N`, where N is its place in the sorted listing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from itertools import combinations

from ..privacy import KeyPolicy
from ..schema import Array, DataSchema, Entity, ForeignKey, Node, Object
from .base import Source, SourceError

MAX_FILES = 200
_MAX_SHARED_NOTES = 20
_SEPARATOR = "#"  # Joins a file name and the table or sheet inside it.


def describe_directory(
    root: str,
    sources: Mapping[str, Source],
    kind: str | None,
    policy: KeyPolicy,
    recursive: bool = False,
) -> DataSchema:
    notes: list[str] = []
    entities: list[Entity] = []
    try:
        candidates = _list_files(root, recursive)
    except OSError as error:
        raise SourceError(f"cannot read folder: {error.strerror}") from None
    unsupported = hidden_names = failed = 0

    handled = 0
    for position, relative in enumerate(candidates, start=1):
        path = os.path.join(root, relative)
        source = _pick_source(path, sources, kind)
        if source is None:
            unsupported += 1
            continue
        if handled >= MAX_FILES:
            notes.append(f"stopped after {MAX_FILES} files; point at a smaller folder or a single file")
            break
        handled += 1

        name = relative.replace(os.sep, "/")
        if not all(policy.shows_name(part) for part in name.split("/")):
            name, hidden_names = f"file_{position}", hidden_names + 1
        try:
            schema = source.extract(path, policy)
        except SourceError as error:
            # Source messages never quote data. They're safe, and say what went wrong.
            notes.append(f"could not describe {name}: {error}")
            failed += 1
            continue
        notes.extend(f"{name}: {note}" for note in schema.notes)
        entities.extend(_rename(entity, name) for entity in schema.entities)

    if not entities and not failed:
        notes.append("no readable data files found")
    if unsupported:
        notes.append(f"skipped {unsupported} file(s) of types DataNyx doesn't read")
    if hidden_names:
        notes.append(f"renamed {hidden_names} data-like file name(s) to file_N (key shape)")
    notes.extend(_shared_fields(entities))
    return DataSchema("directory", tuple(entities), tuple(notes))


def _list_files(root: str, recursive: bool) -> list[str]:
    found: list[str] = []
    if not recursive:
        names = sorted(os.listdir(root))
        return [name for name in names if _is_plain_file(os.path.join(root, name))]
    for folder, folders, names in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders if not name.startswith("."))
        for name in sorted(names):
            path = os.path.join(folder, name)
            if _is_plain_file(path):
                found.append(os.path.relpath(path, root))
    return found


def _is_plain_file(path: str) -> bool:
    # Links are skipped so a folder can't point the tool at files outside it.
    return os.path.isfile(path) and not os.path.islink(path) and not os.path.basename(path).startswith(".")


def _pick_source(path: str, sources: Mapping[str, Source], kind: str | None) -> Source | None:
    if kind is not None:
        source = sources[kind]
        return source if source.matches(path) else None
    for source in sources.values():
        if source.matches(path):
            return source
    return None


def _rename(entity: Entity, file_name: str) -> Entity:
    """Name an entity after its file, plus its own name when the file holds several."""
    if entity.name is None:
        return replace(entity, name=file_name)
    prefix = f"{file_name}{_SEPARATOR}"
    keys = tuple(
        ForeignKey(key.columns, prefix + key.referenced_entity, key.referenced_columns)
        for key in entity.foreign_keys
    )
    return replace(entity, name=prefix + entity.name, foreign_keys=keys)


def _record_fields(node: Node) -> frozenset[str]:
    """Named fields of a table-like entity: its records, or the object itself."""
    if isinstance(node, Array):
        node = node.items
    return frozenset(node.fields) if isinstance(node, Object) else frozenset()


def _shared_fields(entities: list[Entity]) -> list[str]:
    """Point out fields the entities have in common, which are likely join keys.

    Only names that are already visible in the schema are used.
    """
    notes: list[str] = []
    fields = [(entity.name, _record_fields(entity.node)) for entity in entities]
    for (first, first_fields), (second, second_fields) in combinations(fields, 2):
        shared = sorted(first_fields & second_fields)
        if shared:
            notes.append(f"{first} and {second} share fields: {', '.join(shared)}")
        if len(notes) >= _MAX_SHARED_NOTES:
            notes.append("more shared fields not listed")
            break
    return notes
