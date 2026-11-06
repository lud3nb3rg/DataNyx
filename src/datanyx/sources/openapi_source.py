"""OpenAPI and Swagger documents: one entity per schema under `components.schemas` (or `definitions`).

An API description holds structure and no data, but it is full of text that looks like data: examples, defaults,
enum labels and descriptions. None of those is used. What is used is the name of each schema and property, its
type and format, and whether it is required.
"""

from __future__ import annotations

import json
import re
from typing import Any

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
from .files import extension, is_remote, missing_extra, open_text, reject_namespace

_MAX_BYTES = 25 * 1024 * 1024
_MAX_DEPTH = 12
_SNIFF_BYTES = 4096
_MARKER = re.compile(r"""(?m)^\s*\{?\s*["']?(openapi|swagger)["']?\s*:\s*["']?\d""")
_FORMATS = {"date": "date", "date-time": "date-time", "time": "time", "uuid": "uuid", "email": "email", "uri": "uri", "byte": "binary", "binary": "binary", "ipv4": "ipv4", "ipv6": "ipv6"}
_REFERENCE = re.compile(r"#/(?:components/schemas|definitions)/(.+)")


class OpenApiSource(Source):
    kind = "openapi"

    def matches(self, target: str) -> bool:
        if is_remote(target) or extension(target) not in (".json", ".yaml", ".yml"):
            return False
        try:
            with open(target, encoding="utf-8-sig", errors="ignore") as stream:
                return bool(_MARKER.search(stream.read(_SNIFF_BYTES)))
        except OSError:
            return False

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        document = _load(target)
        schemas = (document.get("components") or {}).get("schemas") if isinstance(document.get("components"), dict) else None
        schemas = schemas if isinstance(schemas, dict) else document.get("definitions")
        if not isinstance(schemas, dict) or not schemas:
            return DataSchema(self.kind, (), ("no schemas found under components.schemas or definitions",))
        notes: list[str] = []
        entities = []
        for name, schema in schemas.items():
            if not policy.shows_name(str(name)):
                notes.append("hid data-like schema names (key shape)")
                continue
            entities.append(Entity(str(name), _node(schema, schemas, f"$.{name}", policy, notes, ())))
        return DataSchema(self.kind, tuple(entities), tuple(dict.fromkeys(notes)))


def _load(target: str) -> dict[str, Any]:
    with open_text(target) as stream:
        text = stream.read(_MAX_BYTES + 1)
    if len(text) > _MAX_BYTES:
        raise SourceError("the API description is larger than 25 MB")
    try:
        if extension(target) == ".json":
            document = json.loads(text)
        else:
            try:
                import yaml
            except ImportError:
                raise missing_extra("YAML", "yaml") from None
            document = yaml.safe_load(text)
    except SourceError:
        raise
    except Exception:  # Messages from parsers can quote the text.
        raise SourceError("the API description isn't valid JSON or YAML") from None
    if not isinstance(document, dict):
        raise SourceError("the API description isn't an object")
    return document


def _node(schema: Any, schemas: dict, path: str, policy: KeyPolicy, notes: list[str], stack: tuple[str, ...]) -> Node:
    if not isinstance(schema, dict) or len(stack) >= _MAX_DEPTH:
        return Unknown()
    reference = schema.get("$ref")
    if isinstance(reference, str):
        match = _REFERENCE.fullmatch(reference)
        name = match.group(1) if match else None
        if name is None or name in stack or name not in schemas:
            return Unknown()  # Elsewhere, or a schema that contains itself.
        return _node(schemas[name], schemas, path, policy, notes, (*stack, name))
    node = _plain(schema, schemas, path, policy, notes, stack)
    if schema.get("nullable") is True and not isinstance(node, (Unknown, Null)):
        node = Union((node, Null()))
    return node


def _plain(schema: dict, schemas: dict, path: str, policy: KeyPolicy, notes: list[str], stack: tuple[str, ...]) -> Node:
    if isinstance(schema.get("allOf"), list):
        return _merge_all_of(schema, schemas, path, policy, notes, stack)
    for key in ("oneOf", "anyOf"):
        if isinstance(schema.get(key), list) and schema[key]:
            options: list[Node] = []
            for option in schema[key]:
                node = _node(option, schemas, path, policy, notes, stack)
                if node not in options:
                    options.append(node)
            return options[0] if len(options) == 1 else Union(tuple(options))
    kind = schema.get("type")
    if isinstance(kind, list):  # OpenAPI 3.1: ["string", "null"].
        options = [_plain({**schema, "type": k}, schemas, path, policy, notes, stack) if k != "null" else Null() for k in kind]
        return options[0] if len(options) == 1 else Union(tuple(options))
    if kind == "object" or "properties" in schema:
        return _object(schema, schemas, path, policy, notes, stack)
    if kind == "array":
        return Array(_node(schema.get("items"), schemas, path + "[]", policy, notes, stack))
    if kind == "integer":
        return Integer()
    if kind == "number":
        return Number()
    if kind == "boolean":
        return Boolean()
    if kind == "string":
        return String(_FORMATS.get(str(schema.get("format"))))
    return Unknown()


def _object(schema: dict, schemas: dict, path: str, policy: KeyPolicy, notes: list[str], stack: tuple[str, ...]) -> Object:
    properties: dict = schema["properties"] if isinstance(schema.get("properties"), dict) else {}
    required = {str(n) for n in schema.get("required") or [] if isinstance(n, str)} if isinstance(schema.get("required"), list) else set()
    entries = [
        (str(name), _node(sub, schemas, child_path(path, str(name), policy), policy, notes, stack), _native(sub))
        for name, sub in properties.items()
    ]
    built = build_object(entries, path, policy, notes)
    extra_schema = schema.get("additionalProperties")
    extra = built.extra
    if isinstance(extra_schema, dict):
        extra = _node(extra_schema, schemas, path + ".{*}", policy, notes, stack)
    fields = {n: Field(f.node, n in required, f.native_type) for n, f in built.fields.items()}
    return Object(fields, extra, built.key_format)


def _merge_all_of(schema: dict, schemas: dict, path: str, policy: KeyPolicy, notes: list[str], stack: tuple[str, ...]) -> Node:
    """Several schemas that all apply: their properties together."""
    parts = [_node(part, schemas, path, policy, notes, stack) for part in schema["allOf"]]
    own = {k: v for k, v in schema.items() if k != "allOf"}
    if own.get("properties"):
        parts.append(_object(own, schemas, path, policy, notes, stack))
    objects = [p for p in parts if isinstance(p, Object)]
    if not objects:
        return parts[0] if parts else Unknown()
    fields: dict[str, Field] = {}
    extra = None
    for item in objects:
        fields.update(item.fields)
        extra = item.extra if item.extra is not None else extra
    return Object(fields, extra)


def _native(schema: Any) -> str | None:
    if isinstance(schema, dict) and isinstance(schema.get("type"), str):
        return str(schema["type"]) + (f":{schema['format']}" if isinstance(schema.get("format"), str) and re.fullmatch(r"[\w-]{1,20}", schema["format"]) else "")
    return None
