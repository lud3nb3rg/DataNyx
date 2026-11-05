"""Check code against a schema before it runs: do the tables and columns it uses exist?

A schema lists the names that are visible, so this can catch a misspelled column, a
wrong table, an ambiguous join or a comparison between incompatible types without any
data being read by the code or by the checker. The code being checked is the agent's own,
so quoting it back in a message reveals nothing.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence

from ..privacy import KeyPolicy
from ..sources.base import SourceError
from .model import Column, Diagnostic, Table, tables_from_schema
from .python_check import check_python
from .sql_check import check_sql

LANGUAGES = ("sql", "python")
_EXTENSIONS = {".sql": "sql", ".py": "python"}
_DIALECTS = {
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "mssql": "tsql",
    "oracle": "oracle",
    "sqlite": "sqlite",
}


def detect_language(path: str) -> str | None:
    return _EXTENSIONS.get(os.path.splitext(path)[1].lower())


def check_code(
    code: str, tables: Sequence[Table], language: str, dialect: str | None = None
) -> list[Diagnostic]:
    """Findings for `code`, in line order. `language` is `sql` or `python`."""
    if language == "sql":
        found = check_sql(code, tables, dialect)
    elif language == "python":
        found = check_python(code, tables, dialect)
    else:
        raise SourceError(f"language must be one of {list(LANGUAGES)}")
    return sorted(found, key=lambda item: (item.line, item.column or 0))


def load_tables(
    targets: Sequence[str],
    table_names: Sequence[str] = (),
    source: str | None = None,
    policy: KeyPolicy | None = None,
    namespace: str | None = None,
    recursive: bool = False,
) -> tuple[list[Table], str | None]:
    """The tables the targets describe, and the SQL dialect they suggest (if they agree).

    A single file has no table name of its own (the schema leaves file names out), so it
    is called `table_names[i]`, or its file name if that is safe to show, or `data`. Its
    real file name still works when code refers to it.
    """
    from .. import extract  # Imported here: the package imports this module.

    policy = policy or KeyPolicy()
    tables: list[Table] = []
    dialects: set[str | None] = set()
    for position, target in enumerate(targets):
        schema = extract(target, source, policy, namespace, recursive)
        dialects.add(_dialect_for(target))
        is_file = os.path.isfile(target)
        stem = os.path.splitext(os.path.basename(target))[0] if is_file else ""
        given = table_names[position] if position < len(table_names) else None
        name = given or (stem if stem and policy.shows_name(stem) else None)
        found = tables_from_schema(schema, name)
        if is_file:
            base = os.path.basename(target)
            for table in found:
                table.aliases = table.aliases | {base, stem}
        tables.extend(found)
    return tables, (dialects.pop() if len(dialects) == 1 else None)


def _dialect_for(target: str) -> str | None:
    value = os.environ.get(target[4:], "") if target.startswith("env:") else target
    match = re.match(r"([A-Za-z][A-Za-z0-9+.-]*)://", value)
    if match:
        return _DIALECTS.get(match.group(1).split("+")[0].lower())
    if os.path.splitext(value)[1].lower() in (".db", ".sqlite", ".sqlite3"):
        return "sqlite"
    return None


def format_diagnostics(diagnostics: Sequence[Diagnostic], path: str = "<stdin>", output_format: str = "text") -> str:
    """Findings as text, one per line with an optional hint, or as JSON."""
    if output_format == "json":
        return json.dumps([item.as_dict() for item in diagnostics], indent=2)
    if not diagnostics:
        return "No problems found."
    lines = []
    for item in diagnostics:
        where = f"{path}:{item.line}" + (f":{item.column}" if item.column else "")
        lines.append(f"{where}: {item.severity}: {item.message}")
        if item.hint:
            lines.append(f"    hint: {item.hint}")
    errors = sum(item.severity == "error" for item in diagnostics)
    warnings = len(diagnostics) - errors
    lines.append(f"{errors} error{'s' if errors != 1 else ''}, {warnings} warning{'s' if warnings != 1 else ''}")
    return chr(10).join(lines)


__all__ = [
    "LANGUAGES",
    "Column",
    "Diagnostic",
    "Table",
    "check_code",
    "detect_language",
    "format_diagnostics",
    "load_tables",
    "tables_from_schema",
]
