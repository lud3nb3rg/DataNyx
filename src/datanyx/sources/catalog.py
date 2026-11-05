"""Build objects from a list of named, already-typed entries.

For sources that know their structure up front (database catalogs, Parquet
footers) rather than inferring it from values.
"""

from __future__ import annotations

from ..formats import detect_format
from ..privacy import KeyPolicy
from ..schema import Field, Node, Object, Union, Unknown


def merge_nodes(nodes: list[Node]) -> Node:
    if any(isinstance(node, Unknown) for node in nodes):
        return Unknown()
    options: list[Node] = []
    for node in nodes:
        for option in node.options if isinstance(node, Union) else (node,):
            if option not in options:
                options.append(option)
    return options[0] if len(options) == 1 else Union(tuple(options))


def build_object(
    entries: list[tuple[str, Node, str | None]], path: str, policy: KeyPolicy, notes: list[str]
) -> Object:
    """Name the entries whose names are structure; fold the rest into `extra`."""
    hide_all = path in policy.map_paths
    fields: dict[str, Field] = {}
    hidden: list[Node] = []
    hidden_names: list[str] = []
    for name, node, native in entries:
        if not hide_all and policy.shows_name(name):
            fields[name] = Field(node, True, native)
        else:
            hidden.append(node)
            hidden_names.append(name)
    if not hidden:
        return Object(fields)
    notes.append(f"hid data-like names at {path} ({'configured map path' if hide_all else 'key shape'})")
    formats = {detect_format(name) for name in hidden_names}
    return Object(fields, merge_nodes(hidden), formats.pop() if len(formats) == 1 else None)
