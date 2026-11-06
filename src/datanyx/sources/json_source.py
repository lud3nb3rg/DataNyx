"""JSON documents and JSON Lines files."""

from __future__ import annotations

import codecs
import json
import os

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import DataSchema
from .base import Source, SourceError
from .files import (
    STDIN,
    binary_stream,
    compression_of,
    extension,
    is_remote,
    items_seen,
    open_text,
    records_observation,
    reject_namespace,
    unnamed,
)

# Above this size a document is read as a stream when ijson is installed, so memory
# use depends on the document's structure, not its length. Smaller files use the
# standard library, whose error messages say where a problem is.
STREAM_THRESHOLD_BYTES = 64 * 1024 * 1024

# Documents stay unnamed: the filename could itself be sensitive
# ("jane_doe_medical.json"), so it must not appear in the schema.


class JsonSource(Source):
    kind = "json"

    def matches(self, target: str) -> bool:
        return target == STDIN or extension(target) == ".json"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        streamer = _streamer() if _should_stream(target) else None
        if streamer is not None:
            streamed = _observe_stream(streamer, target)
            if policy.evidence is not None:
                _feed_evidence(streamer, target, policy.evidence.table(None))
            node, notes = infer(streamed, policy)
            return unnamed(self.kind, node, notes, scale_for(policy, items_seen(streamed)))
        observation = Observation()
        with open_text(target) as stream:
            try:
                document = json.load(stream)
                observation.observe(document)
                if policy.evidence is not None and isinstance(document, list):
                    table = policy.evidence.table(None)
                    for record in document:
                        table.add(record)
            except json.JSONDecodeError as error:
                raise SourceError(_describe_decode_error(error)) from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
        node, notes = infer(observation, policy)
        return unnamed(self.kind, node, notes, scale_for(policy, items_seen(observation)))


class JsonLinesSource(Source):
    kind = "jsonl"

    def matches(self, target: str) -> bool:
        return extension(target) in (".jsonl", ".ndjson")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        # Modelled as an array of records. Reading line by line means files
        # larger than memory still work, since values are discarded as they pass.
        root = records_observation()
        evidence = policy.evidence.table(None) if policy.evidence is not None else None
        with open_text(target) as stream:
            line_number = 0
            try:
                for line_number, line in enumerate(stream, start=1):  # noqa: B007
                    if line.strip():
                        record = json.loads(line)
                        root.observe_item(record)
                        if evidence is not None:
                            evidence.add(record)
            except json.JSONDecodeError as error:
                detail = f"line {line_number}, column {error.colno}: {error.msg}"
                raise SourceError(f"invalid JSON at {detail}") from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
        node, notes = infer(root, policy)
        return unnamed(self.kind, node, notes, scale_for(policy, items_seen(root)))


def _should_stream(target: str) -> bool:
    if target == STDIN:
        return False  # Its size isn't known, and piped documents are rarely huge.
    if is_remote(target) or compression_of(target):
        return True  # Streaming never holds the document, and its size isn't known without reading it.
    try:
        return os.path.getsize(target) > STREAM_THRESHOLD_BYTES
    except OSError:
        return False


def _streamer():
    """The ijson module, or None when it isn't installed."""
    try:
        import ijson
    except ImportError:
        return None
    return ijson


_BOM = codecs.BOM_UTF8  # Added by some Windows tools; ijson's parser rejects it.


class _Replay:
    """A stream with some bytes that were read ahead put back in front. Decompressors can't always seek back."""

    def __init__(self, head: bytes, stream) -> None:
        self._head = head
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        if not self._head:
            return self._stream.read(size)
        if size is None or size < 0:
            data, self._head = self._head + self._stream.read(), b""
            return data
        data, self._head = self._head[:size], self._head[size:]
        return data + self._stream.read(size - len(data)) if len(data) < size else data


def _without_bom(stream):
    head = stream.read(3)
    return stream if head == _BOM else _Replay(head, stream)


def _observe_stream(ijson, path: str) -> Observation:
    """Observe a document from parser events, without ever building it in memory."""
    root = Observation()
    stack: list[list] = []  # [observation of the container, is an object, current key]
    try:
        with binary_stream(path) as stream:
            stream = _without_bom(stream)
            for event, value in ijson.basic_parse(stream, use_float=True):
                _apply(event, value, root, stack)
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None
    except ijson.JSONError:
        # ijson's messages can quote the text around the problem.
        raise SourceError("invalid JSON") from None
    return root


def _feed_evidence(ijson, path: str, table) -> None:
    """Key evidence is gathered per record, so the records of a top-level list are read one at a time."""
    try:
        with binary_stream(path) as stream:
            stream = _without_bom(stream)
            for record in ijson.items(stream, "item", use_float=True):
                table.add(record)
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None
    except ijson.JSONError:
        raise SourceError("invalid JSON") from None


def _apply(event: str, value: object, root: Observation, stack: list[list]) -> None:
    if event == "map_key":
        stack[-1][2] = value
    elif event in ("end_map", "end_array"):
        stack.pop()
    elif event in ("start_map", "start_array"):
        target = _slot(stack, root)
        target.seen += 1
        is_object = event == "start_map"
        if is_object:
            target.objects += 1
        else:
            target.arrays += 1
        stack.append([target, is_object, None])
    else:
        _slot(stack, root).observe(value)


def _slot(stack: list[list], root: Observation) -> Observation:
    """Where the next value belongs."""
    if not stack:
        return root
    container, is_object, key = stack[-1]
    if is_object:
        return container.fields.setdefault(key, Observation())
    if container.items is None:
        container.items = Observation()
    return container.items


def _describe_decode_error(error: json.JSONDecodeError) -> str:
    # Built by hand so the message can never pick up the document text.
    return f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
