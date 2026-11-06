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
from ..sources.files import FIRST_ROW_IS_DATA, column_names, positional_names, row_is_data, sqlite_read_only_uri
from .fakes import Pseudonymizer
from .plan import Treatment, build_plan, classify
from .sql_text import clean_table_sql, holds_text, without_comments

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
    except csv.Error:
        raise SourceError(f"input isn't valid {kind.upper()}") from None
    except ValueError as error:
        if "text anonymizer" in str(error):
            raise SourceError("free text columns need a text anonymizer to be passed in") from None
        raise
    except OSError as error:
        raise SourceError(f"cannot read or write a file: {error.strerror}") from None
    return report


# --- CSV and TSV ------------------------------------------------------------------------------------


def _has_cells(row: list[str]) -> bool:
    return any(cell.strip() for cell in row)


def _delimited(source: str, target: str | None, delimiter: str, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    csv.field_size_limit(1 << 24)
    with open(source, encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        first = next((row for row in reader if _has_cells(row)), None)
        if first is None:
            raise SourceError("the file is empty")
        headerless = row_is_data(first)
        names = positional_names(first) if headerless else column_names(first)
        columns: dict[str, list[Any]] = {name: [] for name in names}
        for row in ([first] if headerless else []):
            for name, cell in zip(names, row):
                columns[name].append(cell)
        for count, row in enumerate(reader):
            if count >= SAMPLE_ROWS:
                break
            for name, cell in zip(names, row):  # By position: two columns can share a name in the file.
                columns[name].append(cell)
    if headerless:
        report.notes.append(FIRST_ROW_IS_DATA)
    overrides, keep = _for_renamed_columns(names, first, options)
    plan = build_plan(columns, overrides=overrides, keep=keep, strict=options.strict)
    report.plans["file"] = plan
    if options.plan_only or target is None:
        return
    kinds = [plan[name].kind for name in names]
    width = len(kinds)
    beyond = 0
    at_header = not headerless
    started = False
    with open(source, encoding="utf-8-sig", newline="") as stream, open(target, "x", encoding="utf-8", newline="") as out:
        reader = csv.reader(stream, delimiter=delimiter)
        writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
        for row in reader:
            started = started or _has_cells(row)
            if not started or (at_header and started):
                writer.writerow(row)  # Blank lines before the header, and the header itself.
                at_header = at_header and not started
                continue
            # A cell past the header has no column to say what it holds, so it can't be kept.
            extra = max(len(row) - width, 0)
            beyond += extra
            writer.writerow([pseudo.apply(kinds[i], cell) for i, cell in enumerate(row[:width])] + [""] * extra)
            report.rows += 1
    if beyond:
        report.notes.append(f"blanked {beyond} cell(s) past the end of the header: nothing says what they hold")


def _for_renamed_columns(names: list[str], header: list[str], options: Options) -> tuple[dict[str, str], frozenset[str]]:
    """What was asked for a column also applies under the name it has here: a second `email` is `email_2`."""
    overrides = {name.lower(): kind for name, kind in options.overrides.items()}
    keep = {name.lower() for name in options.keep}
    for name, original in zip(names, header):
        if name != original:
            if original.lower() in overrides:
                overrides[name.lower()] = overrides[original.lower()]
            if original.lower() in keep:
                keep.add(name.lower())
    return overrides, frozenset(keep)


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


_KEEPS_FLOATS = frozenset({"date", "phone", "postal"})


def _transform(node: Any, key: str, plan: dict[str, Treatment], pseudo: Pseudonymizer, options: Options, report: Report) -> Any:
    if isinstance(node, dict):
        return {_key(name, pseudo): _transform(value, name, plan, pseudo, options, report) for name, value in node.items()}
    if isinstance(node, list):
        return [_transform(item, key, plan, pseudo, options, report) for item in node]
    treatment = _treatment_for(node, key, plan, options, report)
    if isinstance(node, bool) or (isinstance(node, float) and treatment.kind not in _KEEPS_FLOATS):
        return node
    return pseudo.apply(treatment.kind, node)


def _treatment_for(node: Any, key: str, plan: dict[str, Treatment], options: Options, report: Report) -> Treatment:
    if key in plan:
        return plan[key]
    if key and not looks_like_data(key):  # First seen after the sample: decided now, and shown in the plan.
        plan[key] = build_plan({key: [node]}, overrides=options.overrides, keep=options.keep, strict=options.strict)[key]
        return plan[key]
    # No key at all, or one that is itself data, says nothing: only the value can.
    if not isinstance(node, str):
        return Treatment("keep", "a number or empty value with no name")
    found = classify("", [node])
    if found.kind == "keep" and not found.review:
        return found  # A date, or a number written as text.
    if UNNAMED_NOTE not in report.notes:
        report.notes.append(UNNAMED_NOTE)
    return found if found.kind not in ("keep", "text") else Treatment("category", UNNAMED_NOTE)


UNNAMED_NOTE = "text with no key, or under a key that is itself data, was replaced going by what it looks like"


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
    result = _transform(data, "", plan, pseudo, options, report)
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
            out.write(json.dumps(_transform(record, "", plan, pseudo, options, report), ensure_ascii=False) + "\n")
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


def _shadow_tables(reader: sqlite3.Connection) -> set[str]:
    """The tables a virtual table keeps its contents in, such as a full-text index's copy of the text."""
    try:
        return {row[0] for row in reader.execute("select name from pragma_table_list where type = 'shadow'")}
    except sqlite3.Error:  # SQLite before 3.37.
        return set()


def _create_table(writer: sqlite3.Connection, name: str, sql: str, report: Report) -> None:
    clean = clean_table_sql(sql)
    writer.execute(clean.sql)
    if clean.removed_checks:
        report.notes.append(f"removed the CHECK constraints from {name}: their values could be data, and fakes wouldn't pass them")
    if clean.blanked_text:
        report.notes.append(f"blanked the text in the definition of {name}, such as defaults, which could be data")


def _recreate(writer: sqlite3.Connection, kind: str, name: str, sql: str, report: Report) -> None:
    if holds_text(sql):
        report.notes.append(f"left out the {kind} {name}: its definition holds text, which could be data")
        return
    try:
        writer.execute(without_comments(sql))
    except sqlite3.Error:
        report.notes.append(f"could not recreate the {kind} {name}")


def _sqlite(source: str, target: str | None, pseudo: Pseudonymizer, options: Options, report: Report) -> None:
    try:
        reader = sqlite3.connect(sqlite_read_only_uri(source), uri=True)
    except sqlite3.Error:
        raise SourceError("cannot open the database") from None
    writer: sqlite3.Connection | None = None
    try:
        entries = reader.execute(
            r"select type, name, sql from sqlite_master where sql is not null and name not like 'sqlite\_%' escape '\' order by rowid"
        ).fetchall()
        shadows = _shadow_tables(reader)
        tables = []
        for kind, name, sql in entries:
            if kind != "table":
                continue
            if sql.upper().startswith("CREATE VIRTUAL") or name in shadows:
                report.notes.append(f"skipped the virtual table {name}")
            else:
                tables.append((name, sql))
        if target is not None and not options.plan_only:
            writer = sqlite3.connect(target)
            for name, sql in tables:
                _create_table(writer, name, sql, report)
        for name, _sql in tables:
            _table(reader, writer, name, pseudo, options, report)
        if writer is not None:
            for kind, name, sql in entries:
                if kind == "trigger":
                    report.notes.append(f"left out the trigger {name}, which could hold data or change values")
                elif kind in ("index", "view"):
                    _recreate(writer, kind, name, sql, report)
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
    before = pseudo.counts.get(_BLANKED, 0)
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
    blanked = pseudo.counts.get(_BLANKED, 0) - before
    if blanked:
        report.notes.append(f"blanked {blanked} binary value(s) in {table}: a fake can't be made for them")


_BLANKED = "binary value blanked"


def _cell(pseudo: Pseudonymizer, kind: str, value: Any) -> Any:
    if not isinstance(value, bytes):
        return pseudo.apply(kind, value)
    if kind == "keep":
        return value
    if kind == "drop":
        return pseudo.apply(kind, value)
    try:
        return str(pseudo.apply(kind, value.decode("utf-8"))).encode("utf-8")
    except UnicodeDecodeError:
        pseudo.counts[_BLANKED] = pseudo.counts.get(_BLANKED, 0) + 1
        return None


__all__ = ["Options", "Report", "detect_kind", "pseudonymize_file"]

