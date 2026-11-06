"""Read a table-shaped file, replace its values by the plan, and write the same kind of file.

CSV and TSV go through twice: a first look at the start of the file decides what each column
holds, then every row is rewritten. JSON and JSON Lines are treated by key name wherever the key
appears. A SQLite database is rebuilt table by table with its own schema, keys and indexes, in a
new file, so the original is never touched.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..formats import detect_format
from ..privacy import looks_like_data
from ..sources.base import SourceError
from ..sources.files import HEADERLESS_NOTE, first_row_is_data, positional_names
from .fakes import Pseudonymizer
from .plan import Treatment, build_plan

SAMPLE_ROWS = 2000
BATCH = 1000
_EXTENSIONS = {
    ".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite",
}


@dataclass
class Options:
    overrides: Mapping[str, str] = field(default_factory=dict)
    keep: frozenset[str] = frozenset()
    strict: bool = False
    plan_only: bool = False


@dataclass
class Report:
    plans: dict[str, dict[str, Treatment]] = field(default_factory=dict)
    rows: int = 0
    notes: list[str] = field(default_factory=list)


def detect_kind(path: str) -> str | None:
    return _EXTENSIONS.get(os.path.splitext(path)[1].lower())


def pseudonymize_file(source: str, target: str | None, kind: str, pseudo: Pseudonymizer, options: Options) -> Report:
    """Write a pseudonymized copy of `source` to `target` (None for a plan only)."""
    if target is not None and os.path.exists(target):
        raise SourceError("the output file already exists; choose a new name")
    report = Report()
    try:
        if kind in ("csv", "tsv"):
            _delimited(source, target, "," if kind == "csv" else "\t", pseudo, options, report)
        elif kind == "json":
            _json(source, target, pseudo, options, report)
        elif kind == "jsonl":
            _jsonl(source, target, pseudo, options, report)
        elif kind == "sqlite":
            _sqlite(source, target, pseudo, options, report)
        else:
            raise SourceError(f"can't pseudonymize {kind} files")
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None
    except ValueError as error:
        if "text anonymizer" in str(error):
            raise SourceError("free text columns need a text anonymizer to be passed in") from None
        raise
    except OSError as error:
        raise SourceError(f"cannot read or write a file: {error.strerror}") from None
    return report


# --- CSV and TSV ------------------------------------------------------------------------------------


def _delimited(source: str, target: str | None, delimiter: str, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    csv.field_size_limit(1 << 24)
    with open(source, encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        first = next(reader, None)
        if first is None:
            raise SourceError("the file is empty")
        headerless = first_row_is_data(first, options.keep)
        header = positional_names(len(first)) if headerless else first
        columns: dict[str, list[Any]] = {name: [] for name in header}
        names = list(columns)
        if headerless:
            report.notes.append(HEADERLESS_NOTE)
            for name, cell in zip(names, first):
                columns[name].append(cell)
        for count, row in enumerate(reader):
            if count >= SAMPLE_ROWS:
                break
            for name, cell in zip(names, row):
                columns[name].append(cell)
    plan = build_plan(columns, overrides=options.overrides, keep=options.keep, strict=options.strict)
    report.plans["file"] = plan
    if options.plan_only or target is None:
        return
    kinds = [plan[name].kind for name in header]
    with open(source, encoding="utf-8-sig", newline="") as stream, open(target, "x", encoding="utf-8", newline="") as out:
        reader = csv.reader(stream, delimiter=delimiter)
        writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
        extra_cells = False
        for index, row in enumerate(reader):
            if index == 0 and not headerless:
                writer.writerow(row)
                continue
            extra_cells = extra_cells or len(row) > len(kinds)
            # A cell past the last column has no name to judge it by, so it is replaced whatever it is.
            writer.writerow([pseudo.apply(kinds[i] if i < len(kinds) else "category", cell) for i, cell in enumerate(row)])
            report.rows += 1
        if extra_cells:
            report.notes.append("some rows had more cells than the header; the extra cells were replaced")


# --- JSON and JSON Lines -----------------------------------------------------------------------------


def _collect(node: Any, key: str, found: dict[str, list[Any]]) -> None:
    if isinstance(node, dict):
        for name, value in node.items():
            _collect(value, name, found)
    elif isinstance(node, list):
        for item in node:
            _collect(item, key, found)
    elif key and len(found[key]) < 500:
        found[key].append(node)


def _transform(node: Any, key: str, plan: Mapping[str, Treatment], pseudo: Pseudonymizer) -> Any:
    if isinstance(node, dict):
        return {_key(name, pseudo): _transform(value, name, plan, pseudo) for name, value in node.items()}
    if isinstance(node, list):
        return [_transform(item, key, plan, pseudo) for item in node]
    treatment = plan.get(key)
    if treatment is None:
        # Under a key that is itself data, nothing says what the text is, so it can't be kept.
        return pseudo.apply("category", node) if isinstance(node, str) and key and looks_like_data(key) else node
    if isinstance(node, (bool, float)) and treatment.kind not in ("date",):
        return node
    return pseudo.apply(treatment.kind, node)


def _key(name: str, pseudo: Pseudonymizer) -> str:
    """A key that is itself data (an email used as a key) is replaced too."""
    if not looks_like_data(name):
        return name
    return str(pseudo.apply("email" if detect_format(name) == "email" else "id", name))


def _plan_for(found: Mapping[str, list[Any]], options: Options, report: Report) -> dict[str, Treatment]:
    plan = build_plan({k: v for k, v in found.items() if not looks_like_data(k)}, overrides=options.overrides, keep=options.keep, strict=options.strict)
    report.plans["file"] = plan
    return plan


def _json(source: str, target: str | None, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    with open(source, encoding="utf-8-sig") as stream:
        text = stream.read()
    try:
        data = json.loads(text)
    except ValueError:
        raise SourceError("input isn't valid JSON") from None
    found: dict[str, list[Any]] = defaultdict(list)
    _collect(data, "", found)
    plan = _plan_for(found, options, report)
    if options.plan_only or target is None:
        return
    result = _transform(data, "", plan, pseudo)
    with open(target, "x", encoding="utf-8", newline="") as out:
        out.write(json.dumps(result, ensure_ascii=False, indent=2 if "\n" in text.strip() else None))
    report.rows = len(result) if isinstance(result, list) else 1


def _lines(source: str) -> Iterator[Any]:
    with open(source, encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, start=1):
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    raise SourceError(f"invalid JSON on line {number}") from None


def _jsonl(source: str, target: str | None, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    found: dict[str, list[Any]] = defaultdict(list)
    for count, record in enumerate(_lines(source)):
        if count >= SAMPLE_ROWS:
            break
        _collect(record, "", found)
    plan = _plan_for(found, options, report)
    if options.plan_only or target is None:
        return
    with open(target, "x", encoding="utf-8", newline="") as out:
        for record in _lines(source):
            out.write(json.dumps(_transform(record, "", plan, pseudo), ensure_ascii=False) + "\n")
            report.rows += 1


# --- SQLite ------------------------------------------------------------------------------------------


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _scoped(table: str, overrides: Mapping[str, str]) -> dict[str, str]:
    """Overrides for one table: `table.column` beats a bare `column`."""
    scoped = {k: v for k, v in overrides.items() if "." not in k}
    prefix = table.lower() + "."
    scoped.update({k[len(prefix):]: v for k, v in overrides.items() if k.lower().startswith(prefix)})
    return scoped


def _literal_end(sql: str, start: int) -> int:
    """The position after the quoted text that starts at `start`, where a doubled quote is an escape."""
    closer = "]" if sql[start] == "[" else sql[start]
    position = start + 1
    while position < len(sql):
        if sql[position] == closer:
            if closer != "]" and sql[position + 1 : position + 2] == closer:
                position += 2
                continue
            return position + 1
        position += 1
    return len(sql)


def _parenthesis_end(sql: str, start: int) -> int:
    depth, position = 0, start
    while position < len(sql):
        char = sql[position]
        if char in "'\"`[":
            position = _literal_end(sql, position)
            continue
        depth += char == "("
        depth -= char == ")"
        position += 1
        if depth == 0:
            return position
    return len(sql)


def _value_end(sql: str, start: int) -> int:
    """The position after a column's default value."""
    position = start
    while position < len(sql) and sql[position].isspace():
        position += 1
    if sql[position : position + 1] == "(":
        return _parenthesis_end(sql, position)
    if sql[position : position + 1] in ("+", "-"):
        position += 1
    while position < len(sql) and (sql[position].isalnum() or sql[position] in "_."):
        position += 1
    if sql[position : position + 1] == "'":
        position = _literal_end(sql, position)
    return position


def clean_ddl(sql: str) -> str:
    """A CREATE statement without its comments, column defaults and CHECK constraints.

    All of those are text from whoever made the database, and they can hold real values, as in
    DEFAULT 'owner@example.com'. The rows are copied with values for every column, so nothing
    the copy needs depends on them.
    """
    out: list[str] = []
    position = 0
    while position < len(sql):
        char = sql[position]
        if sql.startswith("--", position):
            end = sql.find("\n", position)
            position = len(sql) if end == -1 else end
        elif sql.startswith("/*", position):
            end = sql.find("*/", position + 2)
            position = len(sql) if end == -1 else end + 2
        elif char in "'\"`[":
            end = _literal_end(sql, position)
            out.append(sql[position:end])
            position = end
        elif char.isalpha() or char == "_":
            end = position
            while end < len(sql) and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            word = sql[position:end].upper()
            following = end
            while following < len(sql) and sql[following].isspace():
                following += 1
            if word == "DEFAULT":
                position = _value_end(sql, end)
            elif word == "CHECK" and sql[following : following + 1] == "(":
                position = _parenthesis_end(sql, following)
                kept = re.sub(r"(?i)\bCONSTRAINT\s+\S+\s*$", "", "".join(out)).rstrip()
                out = [kept[:-1] if kept.endswith(",") else kept]  # A constraint of its own takes its comma with it.
            else:
                out.append(sql[position:end])
                position = end
        else:
            out.append(char)
            position += 1
    return "".join(out)


def _sqlite(source: str, target: str | None, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    try:
        reader = sqlite3.connect(f"{Path(source).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        raise SourceError("cannot open the database") from None
    writer: sqlite3.Connection | None = None
    try:
        entries = reader.execute(
            "select type, name, sql from sqlite_master where sql is not null and name not like 'sqlite_%' order by rowid"
        ).fetchall()
        tables = [(name, sql) for kind, name, sql in entries if kind == "table"]
        if target is not None and not options.plan_only:
            writer = sqlite3.connect(target)
            for name, sql in tables:
                if sql.upper().startswith("CREATE VIRTUAL"):
                    report.notes.append(f"skipped the virtual table {name}")
                    continue
                cleaned = clean_ddl(sql)
                if cleaned != sql:
                    report.notes.append(f"left comments, column defaults and CHECK constraints out of the table {name}, as they can hold real values")
                writer.execute(cleaned)
        for name, sql in tables:
            if sql.upper().startswith("CREATE VIRTUAL"):
                continue
            _table(reader, writer, name, pseudo, options, report)
        if writer is not None:
            for kind, name, sql in entries:
                if kind == "trigger":
                    report.notes.append(f"left out the trigger {name}, which could hold data or change values")
                elif kind in ("index", "view"):
                    cleaned = clean_ddl(sql)
                    if "'" in cleaned:
                        report.notes.append(f"left out the {kind} {name}, whose text holds values")
                        continue
                    try:
                        writer.execute(cleaned)
                    except sqlite3.Error:
                        report.notes.append(f"could not recreate the {kind} {name}")
            writer.commit()
    except sqlite3.Error as error:
        raise SourceError(f"the database could not be processed ({type(error).__name__})") from None
    finally:
        reader.close()
        if writer is not None:
            writer.close()


def _table(
    reader: sqlite3.Connection,
    writer: sqlite3.Connection | None,
    table: str,
    pseudo: Pseudonymizer,
    options: Options,
    report: Report,
) -> None:
    columns = [row[1] for row in reader.execute(f"pragma table_info({_quote(table)})")]
    keys = {row[1] for row in reader.execute(f"pragma table_info({_quote(table)})") if row[5]}
    keys |= {row[3] for row in reader.execute(f"pragma foreign_key_list({_quote(table)})")}
    sample = reader.execute(f"select * from {_quote(table)} limit {SAMPLE_ROWS}").fetchall()
    values = {name: [row[i] for row in sample] for i, name in enumerate(columns)}
    plan = build_plan(values, frozenset(keys), _scoped(table, options.overrides), options.keep, options.strict)
    report.plans[table] = plan
    if writer is None:
        return
    kinds = [plan[name].kind for name in columns]
    kept_blobs = [name.lower() in {kept.lower() for kept in options.keep} for name in columns]
    marks = ", ".join("?" * len(columns))
    emptied = False
    cursor = reader.execute(f"select * from {_quote(table)}")
    while True:
        rows = cursor.fetchmany(BATCH)
        if not rows:
            break
        emptied = emptied or any(isinstance(value, bytes) and value and not kept_blobs[i] for row in rows for i, value in enumerate(row))
        writer.executemany(
            f"insert into {_quote(table)} values ({marks})",
            [tuple(_cell(pseudo, kinds[i], value, kept_blobs[i]) for i, value in enumerate(row)) for row in rows],
        )
        report.rows += len(rows)
    if emptied:
        report.notes.append(f"emptied binary values in the table {table}, as they can't be checked; use --keep-column to keep them")


def _cell(pseudo: Pseudonymizer, kind: str, value: Any, keep_blob: bool) -> Any:
    if isinstance(value, bytes):
        return value if keep_blob else b""  # Empty rather than NULL: the column may not allow NULL.
    return pseudo.apply(kind, value)


__all__ = ["Options", "Report", "detect_kind", "pseudonymize_file"]

