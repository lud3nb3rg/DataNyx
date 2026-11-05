"""Source registry. To support a new kind of input, implement `Source` and list it here."""

from __future__ import annotations

from .base import Source, SourceError
from .delimited_source import DelimitedSource
from .excel_source import ExcelSource
from .json_source import JsonLinesSource, JsonSource
from .parquet_source import ParquetSource
from .sql_source import SqlSource
from .xml_source import XmlSource
from .yaml_source import YamlSource

_SOURCES: dict[str, Source] = {
    source.kind: source
    for source in (
        SqlSource(),
        JsonSource(),
        JsonLinesSource(),
        DelimitedSource("csv", ",", (".csv",)),
        DelimitedSource("tsv", "	", (".tsv", ".tab")),
        ParquetSource(),
        ExcelSource(),
        YamlSource(),
        XmlSource(),
    )
}


def source_kinds() -> list[str]:
    return sorted(_SOURCES)


def resolve_source(target: str, kind: str | None = None) -> Source:
    """Return the named source, or the first one that recognises `target`."""
    if kind is not None:
        try:
            return _SOURCES[kind]
        except KeyError:
            raise SourceError(f"unknown source {kind!r}; choose from {source_kinds()}") from None
    for source in _SOURCES.values():
        if source.matches(target):
            return source
    raise SourceError(f"can't tell the input type; pass --source, one of {source_kinds()}")


__all__ = ["Source", "SourceError", "resolve_source", "source_kinds"]
