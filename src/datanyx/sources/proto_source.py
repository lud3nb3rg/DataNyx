"""Protocol Buffers definitions (`.proto`): one entity per top-level message.

A `.proto` file describes structure and holds no data. Even so, only the names of messages and fields and their
types are used: comments, option values (such as `[default = "..."]`) and the names of enum values are never shown.
"""

from __future__ import annotations

import re

from ..privacy import KeyPolicy
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
from .base import Source, SourceError
from .catalog import build_object, child_path
from .files import extension, open_text, reject_namespace, require_local

_MAX_BYTES = 5 * 1024 * 1024
_MAX_DEPTH = 12
_TOKEN = re.compile(
    r"""
    (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<string>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<word>[A-Za-z_.][\w.]*)
  | (?P<number>-?\d[\w.+-]*)
  | (?P<punct>[{}\[\]()<>;=,:])
    """,
    re.VERBOSE | re.DOTALL,
)
_SCALARS: dict[str, Node] = {
    "double": Number(), "float": Number(),
    "int32": Integer(), "int64": Integer(), "uint32": Integer(), "uint64": Integer(), "sint32": Integer(), "sint64": Integer(),
    "fixed32": Integer(), "fixed64": Integer(), "sfixed32": Integer(), "sfixed64": Integer(),
    "bool": Boolean(), "string": String(), "bytes": String("binary"),
}
_WELL_KNOWN: dict[str, Node] = {
    "google.protobuf.Timestamp": String("date-time"),
    "google.protobuf.Duration": String(),
    "google.protobuf.Any": Unknown(),
    "google.protobuf.Struct": Object({}, Unknown()),
    "google.protobuf.Value": Unknown(),
    "google.protobuf.Empty": Object({}),
    "google.protobuf.StringValue": Union((String(), Null())),
    "google.protobuf.BytesValue": Union((String("binary"), Null())),
    "google.protobuf.BoolValue": Union((Boolean(), Null())),
    "google.protobuf.Int32Value": Union((Integer(), Null())),
    "google.protobuf.Int64Value": Union((Integer(), Null())),
    "google.protobuf.UInt32Value": Union((Integer(), Null())),
    "google.protobuf.UInt64Value": Union((Integer(), Null())),
    "google.protobuf.DoubleValue": Union((Number(), Null())),
    "google.protobuf.FloatValue": Union((Number(), Null())),
}


class _Message:
    def __init__(self, name: str, parent: _Message | None) -> None:
        self.name = name
        self.parent = parent
        self.fields: list[tuple[str, str, str | None, bool]] = []  # name, type, label, is a map (type is "key,value")
        self.children: dict[str, _Message] = {}
        self.enums: set[str] = set()


class ProtoSource(Source):
    kind = "proto"

    def matches(self, target: str) -> bool:
        return extension(target) == ".proto"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_local(target, "Protocol Buffers")
        with open_text(target) as stream:
            text = stream.read(_MAX_BYTES + 1)
        if len(text) > _MAX_BYTES:
            raise SourceError("the .proto file is larger than 5 MB")
        package, messages, top_enums = _parse(text)
        notes: list[str] = []
        entities = []
        for name, message in messages.items():
            if not policy.shows_name(name):
                notes.append("hid data-like message names (key shape)")
                continue
            node = _message_node(message, messages, package, top_enums, f"$.{name}", policy, notes, ())
            entities.append(Entity(name, node))
        if not entities and not notes:
            notes.append("no messages found")
        return DataSchema(self.kind, tuple(entities), tuple(dict.fromkeys(notes)))


def _tokens(text: str) -> list[str]:
    found: list[str] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            position += 1  # Whitespace, or a character that means nothing here.
            continue
        position = match.end()
        if match.lastgroup == "comment":
            continue
        found.append(match.group())
    return found


def _parse(text: str) -> tuple[str, dict[str, _Message], set[str]]:
    tokens = _tokens(text)
    package = ""
    messages: dict[str, _Message] = {}
    enums: set[str] = set()
    index = 0
    while index < len(tokens):
        word = tokens[index]
        if word == "package" and index + 1 < len(tokens):
            package = tokens[index + 1]
            index = _skip_statement(tokens, index)
        elif word == "message" and index + 1 < len(tokens):
            message = _Message(tokens[index + 1], None)
            index = _message_body(tokens, index + 2, message)
            messages[message.name] = message
        elif word == "enum" and index + 1 < len(tokens):
            enums.add(tokens[index + 1])
            index = _skip_block(tokens, index)
        elif word in ("service", "extend"):
            index = _skip_block(tokens, index)
        else:
            index = _skip_statement(tokens, index)
    return package, messages, enums


def _skip_statement(tokens: list[str], index: int) -> int:
    depth = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in ("{", "[", "("):
            depth += 1
        elif token in ("}", "]", ")"):
            depth -= 1
        elif token == ";" and depth <= 0:
            break
    return index


def _skip_block(tokens: list[str], index: int) -> int:
    """Past the next `{ ... }`, however deeply nested."""
    while index < len(tokens) and tokens[index] != "{":
        index += 1
    depth = 0
    while index < len(tokens):
        if tokens[index] == "{":
            depth += 1
        elif tokens[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def _message_body(tokens: list[str], index: int, message: _Message) -> int:
    """Read `{ ... }` into `message`; return the index after the closing brace."""
    if index >= len(tokens) or tokens[index] != "{":
        return index
    index += 1
    while index < len(tokens) and tokens[index] != "}":
        word = tokens[index]
        if word == "message" and index + 1 < len(tokens):
            child = _Message(tokens[index + 1], message)
            index = _message_body(tokens, index + 2, child)
            message.children[child.name] = child
        elif word == "enum" and index + 1 < len(tokens):
            message.enums.add(tokens[index + 1])
            index = _skip_block(tokens, index)
        elif word == "oneof":
            index = _oneof(tokens, index + 2, message)
        elif word in ("option", "reserved", "extensions", "extend"):
            index = _skip_block(tokens, index) if word == "extend" else _skip_statement(tokens, index)
        elif word == ";":
            index += 1
        else:
            index = _field(tokens, index, message, None)
    return index + 1


def _oneof(tokens: list[str], index: int, message: _Message) -> int:
    if index < len(tokens) and tokens[index] == "{":
        index += 1
    while index < len(tokens) and tokens[index] != "}":
        index = _field(tokens, index, message, "optional") if tokens[index] != ";" else index + 1
    return index + 1


def _field(tokens: list[str], index: int, message: _Message, label: str | None) -> int:
    """`[label] type name = number [options];` or `map<key, value> name = number;`."""
    start = index
    if tokens[index] in ("repeated", "optional", "required"):
        label = tokens[index]
        index += 1
    if index < len(tokens) and tokens[index] == "map" and index + 1 < len(tokens) and tokens[index + 1] == "<":
        end = index + 2
        while end < len(tokens) and tokens[end] != ">":
            end += 1
        inner = [t for t in tokens[index + 2 : end] if t != ","]
        if len(inner) == 2 and end + 1 < len(tokens):
            message.fields.append((tokens[end + 1], inner[1], label, True))
        return _skip_statement(tokens, start)
    if index + 1 < len(tokens) and tokens[index + 1] not in ("=", ";", "{") and tokens[index] != "group":
        message.fields.append((tokens[index + 1], tokens[index], label, False))
    return _skip_statement(tokens, start)


def _lookup(name: str, scope: _Message | None, top: dict[str, _Message], package: str) -> _Message | None:
    """The message a type name refers to: this message's own nested ones first, then outward, then the file."""
    parts = name.lstrip(".").split(".")
    if package and name.startswith(package + "."):
        parts = name[len(package) + 1 :].split(".")
    current: _Message | None = scope
    while True:
        container = current.children if current else top
        node = container.get(parts[0])
        for part in parts[1:]:
            node = node.children.get(part) if node else None
        if node is not None:
            return node
        if current is None:
            return None
        current = current.parent


def _is_enum(name: str, scope: _Message | None, top_enums: set[str]) -> bool:
    short = name.split(".")[-1]
    current = scope
    while current is not None:
        if short in current.enums:
            return True
        current = current.parent
    return short in top_enums


def _message_node(
    message: _Message, top: dict[str, _Message], package: str, top_enums: set[str], path: str, policy: KeyPolicy,
    notes: list[str], stack: tuple[_Message, ...],
) -> Node:
    if message in stack or len(stack) >= _MAX_DEPTH:
        return Unknown()  # A message that contains itself.
    entries = []
    required: dict[str, bool] = {}
    for name, type_name, label, is_map in message.fields:
        child = child_path(path, name, policy)
        if is_map:
            value = _type_node(type_name, message, top, package, top_enums, child + ".{*}", policy, notes, (*stack, message))
            node: Node = Object({}, value)  # Map keys are data, so none is named.
        else:
            node = _type_node(type_name, message, top, package, top_enums, child, policy, notes, (*stack, message))
            if label == "repeated":
                node = Array(node)
        entries.append((name, node, _native(type_name, is_map, label)))
        required[name] = label == "required"
    built = build_object(entries, path, policy, notes)
    return Object({n: Field(f.node, required.get(n, False), f.native_type) for n, f in built.fields.items()}, built.extra, built.key_format)


def _type_node(
    name: str, scope: _Message, top: dict[str, _Message], package: str, top_enums: set[str], path: str,
    policy: KeyPolicy, notes: list[str], stack: tuple[_Message, ...],
) -> Node:
    if name in _SCALARS:
        return _SCALARS[name]
    qualified = name.lstrip(".")
    if qualified in _WELL_KNOWN:
        return _WELL_KNOWN[qualified]
    if _is_enum(name, scope, top_enums):
        return String()  # Its value names are labels, which are never shown.
    target = _lookup(name, scope, top, package)
    if target is None:
        return Unknown()  # Defined in a file that isn't here.
    return _message_node(target, top, package, top_enums, path, policy, notes, stack)


def _native(type_name: str, is_map: bool, label: str | None) -> str | None:
    if is_map:
        return "map"
    return type_name if type_name in _SCALARS else "enum or message"
