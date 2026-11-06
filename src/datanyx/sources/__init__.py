"""Source registry. To support a new kind of input, implement `Source` and list it here."""

from __future__ import annotations

from ..privacy import KeyPolicy
from ..schema import DataSchema
from .arrow_source import ArrowSource
from .avro_source import AvroSource
from .base import Source, SourceError
from .ddl_source import DdlSource
from .delimited_source import DelimitedSource
from .delta_source import DeltaSource
from .directory import describe_directory
from .duckdb_source import DuckDbSource
from .excel_source import ExcelSource
from .fhir_source import FhirSource
from .hl7_source import Hl7Source
from .iceberg_source import IcebergSource
from .json_source import JsonLinesSource, JsonSource
from .mongodb_source import MongoDbSource
from .openapi_source import OpenApiSource
from .orc_source import OrcSource
from .parquet_source import ParquetSource
from .proto_source import ProtoSource
from .sql_source import SqlSource
from .stats_source import SasSource, SpssSource, StataSource
from .xml_source import XmlSource
from .yaml_source import YamlSource

_SOURCES: dict[str, Source] = {
    source.kind: source
    for source in (
        SqlSource(),
        FhirSource(),  # Before JSON: `.fhir.json` is a JSON file with a more specific reader.
        OpenApiSource(),  # Likewise: an API description is JSON or YAML, found by what its first lines say.
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
        ArrowSource(),
        DuckDbSource(),
        MongoDbSource(),
        StataSource(),
        SpssSource(),
        SasSource(),
        Hl7Source(),
        DeltaSource(),
        IcebergSource(),
        ProtoSource(),
        DdlSource(),
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


#: Sources whose table is a folder, so a folder is asked about them before it is read as a pile of files.
_TABLE_FOLDERS = ("delta", "iceberg")


def table_folder_source(target: str, kind: str | None = None) -> Source | None:
    """The source for a folder that is itself one table (Delta Lake, Iceberg), or None for an ordinary folder."""
    if kind is not None:
        return _SOURCES[kind] if kind in _TABLE_FOLDERS else None
    return next((_SOURCES[name] for name in _TABLE_FOLDERS if _SOURCES[name].matches(target)), None)


def describe_folder(path: str, kind: str | None, policy: KeyPolicy, recursive: bool = False) -> DataSchema:
    """Describe every file in a folder that some source can read."""
    if kind is not None and kind not in _SOURCES:
        raise SourceError(f"unknown source {kind!r}; choose from {source_kinds()}")
    return describe_directory(path, _SOURCES, kind, policy, recursive)


__all__ = ["Source", "SourceError", "describe_folder", "resolve_source", "source_kinds", "table_folder_source"]
