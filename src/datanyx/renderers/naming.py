"""Turn schema names into identifiers for generated code."""

from __future__ import annotations

import keyword
import re

# Attributes of a Pydantic model: a field with one of these names would shadow it.
_PYDANTIC_RESERVED = frozenset(
    "copy dict json schema schema_json validate construct fields from_orm parse_obj parse_raw "
    "parse_file update_forward_refs".split()
)


def pascal_case(name: str | None, fallback: str = "Root") -> str:
    """`order_lines` and `order-lines.csv` become `OrderLines` and `OrderLinesCsv`."""
    parts = [part for part in re.split(r"[\W_]+", name or "") if part]
    result = "".join(part[:1].upper() + part[1:] for part in parts)
    if not result:
        return fallback
    return result if result.isidentifier() else f"T{result}"


def unique(name: str, used: set[str]) -> str:
    """`name`, or `name2`, `name3`... when it is already taken. Records the result."""
    candidate, number = name, 2
    while candidate in used:
        candidate, number = f"{name}{number}", number + 1
    used.add(candidate)
    return candidate


def python_field_name(name: str) -> str:
    """A legal, non-private Pydantic field name for `name`."""
    cleaned = re.sub(r"\W", "_", name).lstrip("_")
    if not cleaned or not cleaned.isidentifier():
        cleaned = f"f_{cleaned}" if cleaned else "field"
    if keyword.iskeyword(cleaned) or cleaned in _PYDANTIC_RESERVED or cleaned.startswith("model_"):
        cleaned += "_"
    return cleaned
