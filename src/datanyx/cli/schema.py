"""`datanyx schema`."""

from __future__ import annotations

import argparse
import sys

from .. import extract, render
from ..renderers import RENDERERS
from .common import (
    _add_input_arguments,
    _check_paths_differ,
    _policy,
    _write_text,
)


def _run_schema(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output)
    policy = _policy(args)
    schema = extract(args.input, source=args.source, policy=policy, namespace=args.schema, recursive=args.recursive)
    output = render(schema, args.format)

    for note in schema.notes:
        print(f"note: {note}", file=sys.stderr)

    if args.output:
        _write_text(args.output, output + "\n")
    else:
        print(output)
    return 0


def add_schema(commands: argparse._SubParsersAction) -> None:
    schema = commands.add_parser("schema", help="print a value-free schema of the input")
    _add_input_arguments(schema)
    schema.add_argument(
        "--format",
        choices=sorted(RENDERERS),
        default="json-schema",
        help="output format (default: %(default)s)",
    )
    schema.add_argument("-o", "--output", help="write to this file instead of stdout")
