"""Choosing which tables to describe, so a database with thousands of them doesn't flood an agent.

Only names that may be shown are ever considered, so a pattern can't be used to find out whether a
hidden name exists, and the counts in the notes never include one.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from dataclasses import replace

from .privacy import KeyPolicy
from .schema import DataSchema, Entity


def _matches(name: str, patterns: Sequence[str]) -> bool:
    leaf = name.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(leaf, pattern) for pattern in patterns)


def wanted(name: str, policy: KeyPolicy) -> bool:
    """Whether the include and exclude patterns let this name through."""
    if policy.include and not _matches(name, policy.include):
        return False
    return not (policy.exclude and _matches(name, policy.exclude))


def choose(names: Sequence[str], policy: KeyPolicy) -> tuple[list[str], list[str]]:
    """The names to describe, and notes about what was left out. `names` are all showable."""
    notes: list[str] = []
    kept = [name for name in names if wanted(name, policy)]
    if policy.include or policy.exclude:
        if not kept and names:
            notes.append("no table matches the include and exclude patterns")
        elif len(kept) != len(names):
            notes.append(f"left out {len(names) - len(kept)} of {len(names)} tables by the include and exclude patterns")
    limit = policy.max_tables
    if limit is not None and len(kept) > limit:
        notes.append(
            f"showing {limit} of {len(kept)} tables; narrow it with --include (or the tables option), "
            "or ask for the summary format to list them all"
        )
        kept = kept[:limit]
    return kept, notes


def apply_selection(schema: DataSchema, policy: KeyPolicy) -> DataSchema:
    """Select among the entities of any source. A source that selected early leaves nothing to do."""
    named = [entity for entity in schema.entities if entity.name is not None]
    if not (policy.include or policy.exclude or policy.max_tables is not None) or not named:
        return schema
    kept_names, notes = choose([entity.name for entity in named if entity.name], policy)
    keep = set(kept_names)
    entities = tuple(entity for entity in schema.entities if entity.name is None or entity.name in keep)
    if len(entities) == len(schema.entities):
        return schema
    entities = tuple(_without_dangling(entity, keep) for entity in entities)
    return replace(schema, entities=entities, notes=(*schema.notes, *notes))


def _without_dangling(entity: Entity, keep: set[str]) -> Entity:
    """A foreign key to a table that isn't described would point at nothing."""
    keys = tuple(key for key in entity.foreign_keys if key.referenced_entity in keep)
    return entity if len(keys) == len(entity.foreign_keys) else replace(entity, foreign_keys=keys)
