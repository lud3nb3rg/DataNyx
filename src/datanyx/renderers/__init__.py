"""Renderer registry. To add an output format, write a function and list it here."""

from __future__ import annotations

from collections.abc import Callable

from ..schema import DataSchema
from .json_schema import render_json_schema
from .pydantic import render_pydantic
from .sql_ddl import render_sql_ddl
from .summary import render_summary
from .text import render_text
from .typescript import render_typescript

RENDERERS: dict[str, Callable[[DataSchema], str]] = {
    "json-schema": render_json_schema,
    "text": render_text,
    "typescript": render_typescript,
    "pydantic": render_pydantic,
    "sql": render_sql_ddl,
    "summary": render_summary,
}

__all__ = ["RENDERERS"]
