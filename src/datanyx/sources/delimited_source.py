"""CSV and TSV files: the first row names the columns, every other row is a record."""

from __future__ import annotations

import csv

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import DataSchema
from .base import Source, SourceError
from .files import (
    EMPTY_TABLE,
    HEADERLESS_NOTE,
    coerce_text,
    column_names,
    extension,
    first_row_is_data,
    items_seen,
    open_text,
    positional_names,
    records_observation,
    reject_namespace,
    unnamed,
)

_MAX_FIELD_CHARS = 1 << 24


class DelimitedSource(Source):
    def __init__(self, kind: str, delimiter: str, extensions: tuple[str, ...]) -> None:
        self.kind = kind
        self._delimiter = delimiter
        self._extensions = extensions

    def matches(self, target: str) -> bool:
        return extension(target) in self._extensions

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        csv.field_size_limit(_MAX_FIELD_CHARS)
        root = records_observation()
        notes: list[str] = []
        evidence = policy.evidence.table(None) if policy.evidence is not None else None
        with open_text(target) as stream:
            try:
                names = _read_rows(csv.reader(stream, delimiter=self._delimiter), root, notes, evidence, policy.keep_names)
            except csv.Error:
                raise SourceError(f"input isn't valid {self.kind.upper()}") from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
        if names is None:
            return unnamed(self.kind, EMPTY_TABLE, tuple(notes))
        node, inferred = infer(root, policy)
        return unnamed(self.kind, node, tuple(notes) + inferred, scale_for(policy, items_seen(root)))


def _read_rows(reader, root: Observation, notes: list[str], evidence=None, keep_names=frozenset()) -> list[str] | None:
    names: list[str] | None = None
    wide = False
    for row in reader:
        if names is None:
            if not any(cell.strip() for cell in row):
                continue
            if not first_row_is_data(row, keep_names):
                names = column_names(row)
                continue
            names = positional_names(len(row))
            notes.append(HEADERLESS_NOTE)
        if not row:
            continue
        wide = wide or len(row) > len(names)
        # Short rows leave their last columns out, which makes those columns optional.
        record = {name: coerce_text(cell) for name, cell in zip(names, row)}
        root.observe_item(record)
        if evidence is not None:
            evidence.add(record)
    if wide:
        notes.append("some rows had more cells than the header; the extra cells were ignored")
    return names
