"""Arrow IPC and Feather (v2) files, described from the schema message: no record batch is decoded."""

from __future__ import annotations

from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import DataSchema
from .base import Source, SourceError
from .files import extension, missing_extra, path_or_stream, reject_namespace, require_path, unnamed
from .parquet_source import arrow_table_node


class ArrowSource(Source):
    kind = "arrow"

    def matches(self, target: str) -> bool:
        return extension(target) in (".arrow", ".feather", ".ipc")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        require_path(target, "Arrow")
        try:
            import pyarrow as pa
            import pyarrow.ipc as ipc
        except ImportError:
            raise missing_extra("Arrow and Feather", "parquet") from None
        try:
            with path_or_stream(target) as source:
                arrow_schema, rows = _read_schema(ipc, pa, source)
        except FileNotFoundError:
            raise SourceError("cannot read input: no such file") from None
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"input isn't a readable Arrow or Feather v2 file ({type(error).__name__})") from None
        notes: list[str] = []
        return unnamed(self.kind, arrow_table_node(arrow_schema, policy, notes, pa), tuple(notes), scale_for(policy, rows))


def _read_schema(ipc, pa, source):
    """The schema, and the row count when the file kind can give it from metadata (the stream kind can't)."""
    try:
        reader = ipc.open_file(source)
    except pa.ArrowInvalid:
        # Not the random-access file kind: try the streaming kind, whose row count would need a full read.
        if hasattr(source, "seek"):
            source.seek(0)
        else:
            source = pa.OSFile(source)
        return ipc.open_stream(source).schema, None
    try:
        rows = reader.count_rows()  # From each batch's metadata; no values are read.
    except (AttributeError, pa.ArrowException):
        rows = None
    return reader.schema, rows
