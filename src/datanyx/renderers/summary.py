"""Render a short list: one line per table, for a first look at a database too big to read in full."""

from __future__ import annotations

from ..schema import Array, DataSchema, Entity, Object


def render_summary(schema: DataSchema) -> str:
    lines = [f"{len(schema.entities)} {'table' if len(schema.entities) == 1 else 'tables'}"]
    lines.extend(_line(entity) for entity in schema.entities)
    return "\n".join(lines)


def _line(entity: Entity) -> str:
    node = entity.node.items if isinstance(entity.node, Array) else entity.node
    details = []
    if isinstance(node, Object):
        count = len(node.fields)
        details.append(f"{count} {'column' if count == 1 else 'columns'}" + (" and more hidden" if node.extra is not None else ""))
    if entity.primary_key:
        details.append(f"primary key ({', '.join(entity.primary_key)})")
    if entity.foreign_keys:
        details.append("references " + ", ".join(sorted({key.referenced_entity for key in entity.foreign_keys})))
    if entity.scale:
        details.append(entity.scale)
    name = (entity.name or "root") + (" (view)" if entity.view else "")
    return f"{name}: {'; '.join(details)}" if details else name
