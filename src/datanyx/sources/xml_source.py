"""XML files.

Elements become objects: attributes are keys starting with "-", text is "$text",
and a child that occurs more than once under one parent becomes an array. Neither
prefix can begin an XML name, so they never collide with an element.
"""

from __future__ import annotations

from xml.parsers import expat

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..schema import DataSchema
from .base import Source, SourceError
from .files import coerce_text, extension, read_bytes, reject_namespace, unnamed

_TEXT_KEY = "$text"
_ATTRIBUTE_PREFIX = "-"


class XmlSource(Source):
    kind = "xml"

    def matches(self, target: str) -> bool:
        return extension(target) == ".xml"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        observation = Observation()
        observation.observe(_parse(read_bytes(target)))
        node, notes = infer(observation, policy)
        return unnamed(self.kind, node, notes)


class _Builder:
    """Turns expat events into nested dicts, as described in the module docstring."""

    def __init__(self) -> None:
        self.root: tuple[str, object] | None = None
        self._stack: list[list] = []  # [name, attributes, text parts, grouped children]

    def start(self, name: str, attributes: dict[str, str]) -> None:
        converted = {
            _ATTRIBUTE_PREFIX + _local(key): coerce_text(value) for key, value in attributes.items() if not _is_namespace_declaration(key)
        }
        self._stack.append([_local(name), converted, [], {}])

    def text(self, data: str) -> None:
        self._stack[-1][2].append(data)

    def end(self, _name: str) -> None:
        name, attributes, parts, grouped = self._stack.pop()
        text = "".join(parts).strip()
        if not grouped and not attributes:
            value: object = coerce_text(text)
        else:
            value = dict(attributes)
            if text:
                value[_TEXT_KEY] = coerce_text(text)
            for child, values in grouped.items():
                value[child] = values if len(values) > 1 else values[0]
        if self._stack:
            self._stack[-1][3].setdefault(name, []).append(value)
        else:
            self.root = (name, value)


def _parse(data: bytes) -> dict[str, object]:
    builder = _Builder()
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.text

    def refuse(*_arguments: object) -> None:
        # Entity declarations are how XML bombs and external-file reads work.
        raise SourceError("XML with a DOCTYPE or entity declarations isn't supported")

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        raise SourceError(f"invalid XML at line {parser.ErrorLineNumber}, column {parser.ErrorColumnNumber + 1}") from None
    except RecursionError:
        raise SourceError("input is too deeply nested") from None
    if builder.root is None:
        raise SourceError("input has no root element")
    name, value = builder.root
    return {name: value}


def _is_namespace_declaration(key: str) -> bool:
    return key == "xmlns" or key.startswith("xmlns:")


def _local(name: str) -> str:
    """Drop a namespace prefix, so `ns:item` is `item`."""
    return name.rpartition(":")[2]
