"""`datanyx pseudonymize`."""

from __future__ import annotations

import argparse
import os
import sys

from ..pseudonymize import Options as PseudonymizeOptions
from ..pseudonymize import Pseudonymizer, detect_kind, load_key, parse_overrides, pseudonymize_file, render_plan
from ..sources import SourceError
from ..text.mapping import MappingFile
from .common import (
    _add_delimiter,
    _add_model_and_detectors,
    _anonymizer,
    _check_paths_differ,
)


def _run_pseudonymize(args: argparse.Namespace) -> int:
    kind = args.source or detect_kind(args.input)
    if kind is None:
        raise SourceError("can't tell the input type; pass --source csv, tsv, json, jsonl or sqlite")
    if not args.plan and not args.output:
        raise SourceError("name the new file with -o OUTPUT, or use --plan to only see what would change")
    if not os.path.exists(args.input):
        raise SourceError("cannot read input: no such file")
    _check_paths_differ(args.input, args.output, args.mapping, args.key_file)
    overrides = parse_overrides(args.column)
    key = load_key(None if args.plan else args.key_file)
    pseudo = Pseudonymizer(key, text=lambda: _anonymizer(args), record=bool(args.mapping) and not args.plan)
    mapping_file = MappingFile(args.mapping) if args.mapping and not args.plan else None
    options = PseudonymizeOptions(overrides, frozenset(args.keep_column), args.strict, args.plan, args.delimiter)
    try:
        report = pseudonymize_file(args.input, None if args.plan else args.output, kind, pseudo, options)
        if mapping_file is not None:
            mapping_file.write_document(pseudo.pairs)
    finally:
        if mapping_file is not None:
            mapping_file.close()

    for table, plan in report.plans.items():
        print(render_plan("Plan" if table == "file" else f"Plan for table {table}", plan), file=sys.stderr)
        print(file=sys.stderr)
    kept = [name for plan in report.plans.values() for name, t in plan.items() if t.review]
    if kept:
        print(f"Kept as they are, and worth a look: {', '.join(kept)}. Check them, and use --column NAME=KIND or --strict.", file=sys.stderr)
    if args.plan:
        print("Nothing was written. Run it again with -o OUTPUT to write the copy.", file=sys.stderr)
        return 0
    done = ", ".join(f"{count:,} {name}" for name, count in sorted(pseudo.counts.items(), key=lambda pair: -pair[1]))
    print(f"Wrote {report.rows:,} rows to {args.output}. Replaced: {done or 'nothing'}.", file=sys.stderr)
    for note in report.notes:
        print(f"note: {note}", file=sys.stderr)
    if pseudo.unparsed:
        print(f"note: {pseudo.unparsed.get('date', 0)} date value(s) weren't in YYYY-MM-DD form and were kept", file=sys.stderr)
    if not args.key_file:
        print("note: no --key-file, so these fakes won't match the ones from another run", file=sys.stderr)
    elif pseudo.redraws:
        print(f"note: {pseudo.redraws} fake(s) clashed and were drawn again, so they may not match another run's", file=sys.stderr)
    print("note: numbers, categories and dates that were kept can still identify people in combination; see the documentation", file=sys.stderr)
    return 0


def add_pseudonymize(commands: argparse._SubParsersAction) -> None:
    pseudo = commands.add_parser(
        "pseudonymize",
        help="replace real values in a table with consistent fake ones, so it can be shared and joined",
        description=(
            "Write a copy of a CSV, TSV, JSON, JSON Lines or SQLite file in which names, emails, phone "
            "numbers, addresses, ids and free text are replaced by fake values. The same real value is "
            "the same fake value everywhere, so joins keep working, and emails still look like emails. "
            "This reads the real values, so run it yourself, not through an agent. Pseudonymized is not "
            "anonymous: check the plan, and see the documentation (pseudonymization page) for what it can't do."
        ),
    )
    pseudo.add_argument("input", help="the file to copy: .csv, .tsv, .json, .jsonl or a SQLite database")
    pseudo.add_argument("-o", "--output", help="the new file to write (never overwritten)")
    pseudo.add_argument("--source", choices=("csv", "tsv", "json", "jsonl", "sqlite"), help="input type (default: from the extension)")
    _add_delimiter(pseudo)
    pseudo.add_argument(
        "--key-file",
        metavar="FILE",
        help="a secret key, created if missing: with it the same value gets the same fake in every run. Keep it with the real data",
    )
    pseudo.add_argument(
        "--mapping",
        metavar="FILE",
        help="also save which fake stands for which real value, to a new file. It holds real data: keep it away from agents",
    )
    pseudo.add_argument(
        "--column",
        action="append",
        default=[],
        metavar="NAME=KIND",
        help="treat a column or key as this kind, overriding the guess (repeatable; TABLE.COLUMN for one table of a database)",
    )
    pseudo.add_argument("--keep-column", action="append", default=[], metavar="NAME", help="leave this column as it is (repeatable)")
    pseudo.add_argument("--strict", action="store_true", help="also replace text columns that weren't recognised, instead of keeping them")
    pseudo.add_argument("--plan", action="store_true", help="show what each column would become, and stop without writing anything")
    pseudo.add_argument("--hide", action="append", default=[], metavar="TERM", help="for free text: always hide this term (repeatable)")
    _add_model_and_detectors(pseudo)
