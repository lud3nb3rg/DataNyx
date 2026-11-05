"""Excel workbooks (.xlsx): each sheet is a table whose first row names the columns."""

from __future__ import annotations

import warnings

from ..inference import infer
from ..privacy import KeyPolicy
from ..schema import DataSchema, Entity
from .base import Source, SourceError
from .files import (
    EMPTY_TABLE,
    column_names,
    extension,
    missing_extra,
    records_observation,
    reject_namespace,
    require_path,
)


class ExcelSource(Source):
    kind = "excel"

    def matches(self, target: str) -> bool:
        return extension(target) in (".xlsx", ".xlsm")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_path(target, "Excel")
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise missing_extra("Excel", "excel") from None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # openpyxl's warnings can name sheets and styles.
                # Read-only streams rows; data_only gives cached results, not formulas.
                workbook = load_workbook(target, read_only=True, data_only=True)
                try:
                    return _describe(workbook, policy, self.kind)
                finally:
                    workbook.close()
        except SourceError:
            raise
        except FileNotFoundError:
            raise SourceError("cannot read input: no such file") from None
        except Exception as error:
            raise SourceError(f"input isn't a readable Excel file ({type(error).__name__})") from None


def _describe(workbook, policy: KeyPolicy, kind: str) -> DataSchema:
    notes: list[str] = []
    entities: list[Entity] = []
    renamed = False
    for position, sheet in enumerate(workbook.worksheets, start=1):
        name = sheet.title
        if not policy.shows_name(name):
            name, renamed = f"sheet_{position}", True  # Position, not content, so it can't leak.
        root = records_observation()
        names: list[str] | None = None
        for row in sheet.iter_rows(values_only=True):
            if all(cell is None or cell == "" for cell in row):
                continue
            if names is None:
                names = column_names(list(row))
                continue
            root.observe_item({n: (None if v == "" else v) for n, v in zip(names, row)})
        if names is None:
            entities.append(Entity(name, EMPTY_TABLE))
            continue
        node, inferred = infer(root, policy, f"$.{name}")
        notes.extend(inferred)
        entities.append(Entity(name, node))
    if renamed:
        notes.append("renamed data-like sheet names to sheet_N (key shape)")
    return DataSchema(kind, tuple(entities), tuple(notes))
