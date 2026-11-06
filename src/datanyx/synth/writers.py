"""Write generated tables as JSON, JSON Lines, CSV or a SQLite database."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from typing import Any

from ..renderers.sql_ddl import render_sql_ddl
from ..schema import Array, DataSchema, Object
from ..sources.base import SourceError
from .generator import Table

FORMATS = ("json", "jsonl", "csv", "sqlite")
_EXTENSIONS = {"json": ".json", "jsonl": ".jsonl", "csv": ".csv", "sqlite": ".db"}


def render_table(table: Table, output_format: str) -> str:
    """One table as text. Only `json`, `jsonl` and `csv` are text formats."""
    if output_format == "json":
        document = table.rows[0] if table.document else table.rows
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if output_format == "jsonl":
        return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in table.rows)
    if output_format == "csv":
        return _csv(table)
    raise SourceError(f"{output_format} isn't a text format")


def _csv(table: Table) -> str:
    columns: list[str] = []
    for row in table.rows:
        if isinstance(row, dict):
            columns.extend(key for key in row if key not in columns)
    # Rows that aren't objects (a list mixing records and nulls) go in a column of their own.
    if not columns or any(not isinstance(row, dict) for row in table.rows):
        columns.append("value")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=chr(10))
    writer.writerow(columns)
    for row in table.rows:
        if isinstance(row, dict):
            cells = [row.get(column) for column in columns]
        else:
            cells = [row if column == "value" else None for column in columns]
        writer.writerow([_cell(cell) for cell in cells])
    return buffer.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def file_name(table: Table, output_format: str, used: set[str]) -> str:
    """A safe, unused file name for a table: `orders.csv` becomes `orders_csv.csv`."""
    stem = re.sub(r"[^\w.-]+", "_", table.name or "root").strip("._") or "table"
    stem = stem.replace(".", "_")
    name, number = stem, 2
    while name in used:
        name, number = f"{stem}_{number}", number + 1
    used.add(name)
    return name + _EXTENSIONS[output_format]


def write_sqlite(tables: list[Table], path: str) -> None:
    """Create a database with one table per entity. The file must not exist."""
    if os.path.exists(path):
        raise SourceError("the output file already exists; choose a new name")
    schema = DataSchema("synthetic", tuple(table.entity for table in tables))
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error:
        raise SourceError("cannot create the database file") from None
    try:
        connection.set_authorizer(_refuse_other_databases)  # The script only ever needs this one.
        connection.executescript(render_sql_ddl(schema))
        for table in tables:
            _insert(connection, table)
        connection.commit()
    except sqlite3.Error as error:
        raise SourceError(f"this schema couldn't be written as SQLite ({type(error).__name__})") from None
    finally:
        connection.close()


def _refuse_other_databases(action: int, *_rest: object) -> int:
    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK


def _insert(connection: sqlite3.Connection, table: Table) -> None:
    node = table.entity.node.items if isinstance(table.entity.node, Array) else table.entity.node
    columns = list(node.fields) if isinstance(node, Object) and node.fields else ["value"]
    whole_row = columns == ["value"]
    quoted = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    name = '"' + (table.name or "root").replace('"', '""') + '"'
    statement = f"INSERT INTO {name} ({quoted}) VALUES ({', '.join('?' * len(columns))})"
    rows = []
    for row in table.rows:
        if whole_row:
            rows.append([_sql_value(row)])
        else:
            rows.append([_sql_value(row.get(column)) for column in columns])
    connection.executemany(statement, rows)


def _sql_value(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
