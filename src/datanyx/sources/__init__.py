"""Source registry. To support a new kind of input, implement `Source` and list it here."""

from __future__ import annotations

from ..privacy import KeyPolicy
from ..schema import DataSchema
from .avro_source import AvroSource
from .base import Source, SourceError
from .delimited_source import DelimitedSource
from .directory import describe_directory
from .duckdb_source import DuckDbSource
from .excel_source import ExcelSource
from .fhir_source import FhirSource
from .hl7_source import Hl7Source
from .json_source import JsonLinesSource, JsonSource
from .mongodb_source import MongoDbSource
from .orc_source import OrcSource
from .parquet_source import ParquetSource
from .sql_source import SqlSource
from .stats_source import SasSource, SpssSource, StataSource
from .xml_source import XmlSource
from .yaml_source import YamlSource

_SOURCES: dict[str, Source] = {
    source.kind: source
    for source in (
        SqlSource(),
        FhirSource(),  # Before JSON: `.fhir.json` is a JSON file with a more specific reader.
        JsonSource(),
        JsonLinesSource(),
        DelimitedSource("csv", ",", (".csv",)),
        DelimitedSource("tsv", "	", (".tsv", ".tab")),
        ParquetSource(),
        ExcelSource(),
        YamlSource(),
        XmlSource(),
        AvroSource(),
        OrcSource(),
        DuckDbSource(),
        MongoDbSource(),
        StataSource(),
        SpssSource(),
        SasSource(),
        Hl7Source(),
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


def describe_folder(path: str, kind: str | None, policy: KeyPolicy, recursive: bool = False) -> DataSchema:
    """Describe every file in a folder that some source can read."""
    if kind is not None and kind not in _SOURCES:
        raise SourceError(f"unknown source {kind!r}; choose from {source_kinds()}")
    return describe_directory(path, _SOURCES, kind, policy, recursive)


__all__ = ["Source", "SourceError", "describe_folder", "resolve_source", "source_kinds"]
