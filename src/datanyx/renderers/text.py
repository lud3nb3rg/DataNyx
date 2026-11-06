"""Render as compact TypeScript-like text, which costs an agent few tokens to read."""

from __future__ import annotations

from ..schema import (
    Array,
    Boolean,
    DataSchema,
    Entity,
    Field,
    Integer,
    Node,
    Null,
    Number,
    Object,
    String,
    Union,
    Unknown,
)

_INDENT = "  "


def render_text(schema: DataSchema) -> str:
    return "\n\n".join(_entity(entity) for entity in schema.entities)


def _entity(entity: Entity) -> str:
    lines = [f"{entity.name or 'root'}: {_node(entity.node, 0)}"]
    if entity.primary_key:
        lines.append(f"{_INDENT}primary key ({', '.join(entity.primary_key)})")
    for key in entity.foreign_keys:
        guess = "  -- inferred from the values" if key.inferred else ""
        lines.append(
            f"{_INDENT}foreign key ({', '.join(key.columns)})"
            f" -> {key.referenced_entity} ({', '.join(key.referenced_columns)}){guess}"
        )
    if entity.scale:
        lines.append(f"{_INDENT}scale: {entity.scale}")
    return "\n".join(lines)


def _field(name: str, field: Field, depth: int) -> str:
    marker = "" if field.required else "?"
    line = f"{name}{marker}: {_node(field.node, depth)}"
    notes = [item for item in (field.native_type, "unique (inferred)" if field.unique else None) if item]
    return f"{line}  -- {', '.join(notes)}" if notes else line


def _node(node: Node, depth: int) -> str:
    if isinstance(node, Unknown):
        return "unknown"
    if isinstance(node, Null):
        return "null"
    if isinstance(node, Boolean):
        return "boolean"
    if isinstance(node, Integer):
        return "integer"
    if isinstance(node, Number):
        return "number"
    if isinstance(node, String):
        return f"string<{node.format}>" if node.format else "string"
    if isinstance(node, Array):
        return f"array<{_node(node.items, depth)}>"
    if isinstance(node, Union):
        return " | ".join(_node(option, depth) for option in node.options)
    if isinstance(node, Object):
        return _object(node, depth)
    raise TypeError(f"unhandled node: {type(node).__name__}")


def _object(node: Object, depth: int) -> str:
    if not node.fields and node.extra is None:
        return "{}"
    pad = _INDENT * (depth + 1)
    lines = [pad + _field(name, field, depth + 1) for name, field in node.fields.items()]
    if node.extra is not None:
        key_type = f"string<{node.key_format}>" if node.key_format else "string"
        lines.append(f"{pad}[key: {key_type}]: {_node(node.extra, depth + 1)}")
    return "{\n" + "\n".join(lines) + "\n" + _INDENT * depth + "}"
