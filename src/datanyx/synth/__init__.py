"""Synthetic data that matches a schema, for testing code without real data."""

from __future__ import annotations

from .generator import MAX_ROWS, Table, synthesize
from .writers import FORMATS, file_name, render_table, write_sqlite

__all__ = ["FORMATS", "MAX_ROWS", "Table", "file_name", "render_table", "synthesize", "write_sqlite"]
