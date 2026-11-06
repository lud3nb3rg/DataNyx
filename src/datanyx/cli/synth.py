"""`datanyx synth`."""

from __future__ import annotations

import argparse
import os
import sys

from .. import extract
from ..schema import Array, Object
from ..sources import SourceError
from ..synth import FORMATS as SYNTH_FORMATS
from ..synth import MAX_ROWS, file_name, render_table, synthesize, write_sqlite
from .common import (
    _add_input_arguments,
    _check_paths_differ,
    _keep_line_endings,
    _policy,
    _write_new,
)


def _run_synth(args: argparse.Namespace) -> int:
    policy = _policy(args)
    schema = extract(args.input, source=args.source, policy=policy, namespace=args.schema, recursive=args.recursive)
    if not 0 <= args.rows <= MAX_ROWS:
        raise SourceError(f"--rows must be between 0 and {MAX_ROWS}")
    tables = synthesize(schema, args.rows, args.seed)
    if not tables:
        for note in schema.notes:
            print(f"note: {note}", file=sys.stderr)
        raise SourceError("the input has no tables to make fake data for")

    for note in schema.notes:
        print(f"note: {note}", file=sys.stderr)
    if args.format in ("csv", "sqlite") and any(_has_hidden_columns(table) for table in tables):
        print("note: hidden columns have no counterpart in this format; they're left out", file=sys.stderr)

    if args.format == "sqlite":
        if not args.output:
            raise SourceError("sqlite output needs -o FILE")
        _check_paths_differ(args.input, args.output)
        write_sqlite(tables, args.output)
        return 0

    folder = args.output and (os.path.isdir(args.output) or len(tables) > 1)
    if not args.output:
        if len(tables) > 1:
            raise SourceError("this input has several tables; pass -o FOLDER, or use --format sqlite")
        _keep_line_endings()
        sys.stdout.write(render_table(tables[0], args.format))
    elif folder:
        os.makedirs(args.output, exist_ok=True)
        used: set[str] = set()
        for table in tables:
            path = os.path.join(args.output, file_name(table, args.format, used))
            _check_paths_differ(args.input, path)
            _write_new(path, render_table(table, args.format))
    else:
        _check_paths_differ(args.input, args.output)
        _write_new(args.output, render_table(tables[0], args.format))
    return 0


def _has_hidden_columns(table) -> bool:
    node = table.entity.node.items if isinstance(table.entity.node, Array) else table.entity.node
    return isinstance(node, Object) and node.extra is not None


def add_synth(commands: argparse._SubParsersAction) -> None:
    synth = commands.add_parser(
        "synth",
        help="generate fake data that matches the input's schema",
        description=(
            "Write synthetic rows that fit the schema of the input, so code can be tried out "
            "without real data. Only the schema is used, so nothing real is copied. Foreign keys "
            "point at rows that exist."
        ),
    )
    _add_input_arguments(synth)
    synth.add_argument("--rows", type=int, default=100, help="rows per table (default: %(default)s)")
    synth.add_argument("--seed", type=int, default=0, help="makes the output repeatable (default: %(default)s)")
    synth.add_argument(
        "--format",
        choices=SYNTH_FORMATS,
        default="json",
        help="output format (default: %(default)s)",
    )
    synth.add_argument(
        "-o",
        "--output",
        help=(
            "file to write, or a folder when the input has several tables (one file each); "
            "required for sqlite. Existing files are never overwritten"
        ),
    )
