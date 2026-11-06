"""SAS, Stata and SPSS files, described from their metadata: no row is read.

`pyreadstat` is asked for the metadata only. Its value labels and variable labels are never
touched: value labels are the data's own category names, and a variable label is free text.
Row counts are not shown either.
"""

from __future__ import annotations

import re

from ..privacy import KeyPolicy
from ..schema import Array, DataSchema, Integer, Node, Null, Number, String, Union
from .base import Source, SourceError
from .catalog import build_object
from .files import extension, missing_extra, reject_namespace, require_local, unnamed

_SAFE_FORMAT = re.compile(r"[%$A-Za-z0-9.\-]{1,20}")


class _StatsSource(Source):
    """One statistics package's files. Subclasses name the reader and the dates in its formats."""

    extensions: tuple[str, ...]
    label: str

    def reader_for(self, target: str) -> str:
        """The pyreadstat function for this file."""
        raise NotImplementedError

    def matches(self, target: str) -> bool:
        return extension(target) in self.extensions

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_local(target, self.label)
        try:
            import pyreadstat
        except ImportError:
            raise missing_extra(self.label, "stats") from None
        try:
            _, meta = getattr(pyreadstat, self.reader_for(target))(target, metadataonly=True)
        except FileNotFoundError:
            raise SourceError("cannot read input: no such file") from None
        except Exception as error:
            if not _exists(target):
                raise SourceError("cannot read input: no such file") from None
            raise SourceError(f"input isn't a readable {self.label} file ({type(error).__name__})") from None
        entries: list[tuple[str, Node, str | None]] = []
        types = meta.readstat_variable_types or {}
        formats = meta.original_variable_types or {}
        for name in meta.column_names:
            declared = "" if formats.get(name) is None else str(formats[name])
            node = self._node(str(types.get(name, "double")), declared)
            entries.append((str(name), Union((node, Null())), declared if _SAFE_FORMAT.fullmatch(declared) else None))
        notes: list[str] = []
        record = build_object(entries, "$[]", policy, notes)
        return unnamed(self.kind, Array(record), tuple(notes))

    def _node(self, storage: str, declared: str) -> Node:
        if storage == "string":
            return String()
        temporal = self.temporal(declared)
        if temporal is not None:
            return String(temporal)
        return Integer() if storage.startswith("int") else Number()

    @staticmethod
    def temporal(declared: str) -> str | None:
        raise NotImplementedError


def _exists(target: str) -> bool:
    import os

    return os.path.exists(target)


class StataSource(_StatsSource):
    kind = "stata"
    extensions = (".dta",)
    label = "Stata"

    def reader_for(self, target: str) -> str:
        return "read_dta"

    @staticmethod
    def temporal(declared: str) -> str | None:
        text = declared.lower()
        if text.startswith("%tc"):  # %tc and %tC: milliseconds since 1960.
            return "date-time"
        return "date" if text.startswith(("%td", "%d")) else None


class SpssSource(_StatsSource):
    kind = "spss"
    extensions = (".sav", ".zsav", ".por")
    label = "SPSS"

    def reader_for(self, target: str) -> str:
        return "read_por" if extension(target) == ".por" else "read_sav"

    @staticmethod
    def temporal(declared: str) -> str | None:
        text = declared.upper()
        if text.startswith("DATETIME") or text.startswith("YMDHMS"):
            return "date-time"
        if text.startswith(("DATE", "ADATE", "EDATE", "JDATE", "SDATE")):
            return "date"
        if text.startswith(("TIME", "DTIME")):
            return "time"
        return None


class SasSource(_StatsSource):
    kind = "sas"
    extensions = (".sas7bdat", ".xpt")
    label = "SAS"

    def reader_for(self, target: str) -> str:
        return "read_xport" if extension(target) == ".xpt" else "read_sas7bdat"

    @staticmethod
    def temporal(declared: str) -> str | None:
        text = declared.upper()
        if "DATETIME" in text:
            return "date-time"
        if any(word in text for word in ("DATE", "MMDDYY", "DDMMYY", "YYMMDD")):
            return "date"
        if text.startswith("TIME"):
            return "time"
        return None
