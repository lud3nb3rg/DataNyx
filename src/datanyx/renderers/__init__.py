"""Renderer registry. To add an output format, write a function and list it here."""

from __future__ import annotations

from typing import Callable

from ..schema import DataSchema
from .json_schema import render_json_schema
from .text import render_text

RENDERERS: dict[str, Callable[[DataSchema], str]] = {
    "json-schema": render_json_schema,
    "text": render_text,
}

__all__ = ["RENDERERS"]
