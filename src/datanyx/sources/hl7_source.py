"""HL7 version 2 messages: one entity per segment type, with its fields named by position.

HL7 v2 is positional text: `PID|1||123^^^MRN||DOE^JANE`. Each segment type (PID, OBX, ...) becomes an
entity and each field a property named by its place (`PID-5`), because a field's meaning comes from its
position, and the message itself holds none of it. A field with components (`^`) becomes an object with
properties `c1`, `c2`, ..., and a repeating field (`~`) an array. Only types are recorded, never values.

MSH-1 and MSH-2 are the separators themselves, so they're left out. Several messages in one file are
read as one stream of segments.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..schema import DataSchema, Entity
from .base import Source, SourceError
from .files import coerce_text, extension, read_bytes, records_observation, reject_namespace

_SEGMENT_ID = re.compile(r"[A-Z][A-Z0-9]{2}")
_MAX_SEGMENT_TYPES = 200
_TIMESTAMP = re.compile(r"(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?(?:\.\d+)?)?(?:[+-]\d{4})?")


class Hl7Source(Source):
    kind = "hl7"

    def matches(self, target: str) -> bool:
        return extension(target) in (".hl7", ".er7")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        try:
            text = read_bytes(target).decode("utf-8-sig").strip("\x0b\x1c")  # MLLP framing
        except UnicodeDecodeError:
            raise SourceError("input is not valid UTF-8") from None
        segments = [line for line in re.split(r"\r\n|\r|\n", text) if line.strip()]
        if not segments or not segments[0].startswith("MSH") or len(segments[0]) < 8:
            raise SourceError("input isn't an HL7 v2 message (it should start with an MSH segment)")

        groups: dict[str, Observation] = {}
        field_sep = comp_sep = rep_sep = sub_sep = None
        skipped = 0
        for line in segments:
            if line.startswith("MSH") and len(line) >= 8:  # A new message may use other separators.
                field_sep, (comp_sep, rep_sep, _escape, sub_sep) = line[3], (list(line[4:8]) + [None] * 4)[:4]
            if field_sep is None:
                skipped += 1
                continue
            parts = line.split(field_sep)
            segment = parts[0]
            if not _SEGMENT_ID.fullmatch(segment):
                skipped += 1
                continue
            if segment not in groups and len(groups) >= _MAX_SEGMENT_TYPES:
                continue
            fields = parts[2:] if segment == "MSH" else parts[1:]
            first = 3 if segment == "MSH" else 1
            record: dict[str, object] = {}
            for number, raw in enumerate(fields, start=first):
                value = _field(raw, comp_sep, rep_sep, sub_sep)
                if value is not None:
                    record[f"{segment}-{number}"] = value
            groups.setdefault(segment, records_observation()).observe_item(record)

        notes: list[str] = []
        entities = []
        for segment, root in groups.items():
            node, inferred = infer(root, policy, f"$.{segment}")
            notes.extend(inferred)
            entities.append(Entity(segment, node))
        if skipped:
            notes.append(f"skipped {skipped} line(s) that aren't HL7 segments")
        return DataSchema(self.kind, tuple(entities), tuple(notes))


def _field(raw: str, comp_sep, rep_sep, sub_sep):
    """A field as plain values: text split into repetitions and components, or None when empty."""
    if not raw.strip():
        return None
    repetitions = raw.split(rep_sep) if rep_sep else [raw]
    values = [_repetition(item, comp_sep, sub_sep) for item in repetitions]
    return values[0] if len(values) == 1 else values


def _repetition(text: str, comp_sep, sub_sep):
    if not comp_sep or comp_sep not in text:
        return _scalar(text, sub_sep)
    return {f"c{n}": _scalar(part, sub_sep) for n, part in enumerate(text.split(comp_sep), start=1)}


def _scalar(text: str, sub_sep):
    if sub_sep and sub_sep in text:
        return [_scalar(part, None) for part in text.split(sub_sep)]
    if not text.strip():
        return None
    stamp = _TIMESTAMP.fullmatch(text)
    if stamp:  # HL7 timestamps are digits (YYYYMMDD[HHMM[SS]]), so give them their real type.
        year, month, day, hour, minute, second = stamp.groups()
        try:
            if hour is None:
                return date(int(year), int(month), int(day))
            return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
        except ValueError:
            pass
    return coerce_text(text)
