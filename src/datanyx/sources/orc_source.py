"""ORC files, described from the footer alone: no stripe is ever read."""

from __future__ import annotations

from ..privacy import KeyPolicy
from ..schema import DataSchema
from .base import Source, SourceError
from .files import extension, missing_extra, reject_namespace, require_path, unnamed
from .parquet_source import arrow_table_node


class OrcSource(Source):
    kind = "orc"

    def matches(self, target: str) -> bool:
        return extension(target) == ".orc"

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_path(target, "ORC")
        try:
            import pyarrow as pa
            import pyarrow.orc as orc
        except ImportError:
            raise missing_extra("ORC", "orc") from None
        try:
            arrow_schema = orc.ORCFile(target).schema  # From the file footer; no row is decoded.
        except FileNotFoundError:
            raise SourceError("cannot read input: no such file") from None
        except Exception as error:
            raise SourceError(f"input isn't a readable ORC file ({type(error).__name__})") from None
        notes: list[str] = []
        return unnamed(self.kind, arrow_table_node(arrow_schema, policy, notes, pa), tuple(notes))
