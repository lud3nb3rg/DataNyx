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
import sqlite3
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..formats import detect_format
from ..privacy import looks_like_data
from ..sources.base import SourceError
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
        header = next(reader, None)
        if header is None:
            raise SourceError("the file is empty")
        columns: dict[str, list[Any]] = {name: [] for name in header}
        names = list(columns)
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
        for index, row in enumerate(reader):
            if index == 0:
                writer.writerow(row)
                continue
            writer.writerow([pseudo.apply(kinds[i], cell) if i < len(kinds) else cell for i, cell in enumerate(row)])
            report.rows += 1


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
    if treatment is None or (isinstance(node, (bool, float)) and treatment.kind not in ("date",)):
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


def _sqlite(source: str, target: str | None, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    try:
        reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
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
                writer.execute(sql)
        for name, sql in tables:
            if sql.upper().startswith("CREATE VIRTUAL"):
                continue
            _table(reader, writer, name, pseudo, options, report)
        if writer is not None:
            for kind, name, sql in entries:
                if kind == "trigger":
                    report.notes.append(f"left out the trigger {name}, which could hold data or change values")
                elif kind in ("index", "view"):
                    try:
                        writer.execute(sql)
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
    marks = ", ".join("?" * len(columns))
    cursor = reader.execute(f"select * from {_quote(table)}")
    while True:
        rows = cursor.fetchmany(BATCH)
        if not rows:
            break
        writer.executemany(
            f"insert into {_quote(table)} values ({marks})",
            [tuple(_cell(pseudo, kinds[i], value) for i, value in enumerate(row)) for row in rows],
        )
        report.rows += len(rows)


def _cell(pseudo: Pseudonymizer, kind: str, value: Any) -> Any:
    return value if isinstance(value, bytes) else pseudo.apply(kind, value)


__all__ = ["Options", "Report", "detect_kind", "pseudonymize_file"]

