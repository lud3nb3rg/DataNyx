"""DataNyx: describe data to an AI agent without showing it the data."""

from __future__ import annotations

from .privacy import KeyPolicy
from .renderers import RENDERERS
from .schema import DataSchema
from .sources import Source, SourceError, resolve_source

__version__ = "0.1.0"


def extract(
    target: str,
    source: str | None = None,
    policy: KeyPolicy | None = None,
    namespace: str | None = None,
) -> DataSchema:
    """Build a value-free schema for a file path, "-" for stdin, or a database URL.

    `namespace` picks a database schema; the database's default is used otherwise.
    """
    return resolve_source(target, source).extract(target, policy or KeyPolicy(), namespace)


def render(schema: DataSchema, output_format: str = "json-schema") -> str:
    """Render a schema in one of the formats listed in `RENDERERS`."""
    return RENDERERS[output_format](schema)


__all__ = ["DataSchema", "KeyPolicy", "Source", "SourceError", "extract", "render"]
