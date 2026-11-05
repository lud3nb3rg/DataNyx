"""Command line interface.

An agent may be the one running this, so everything it prints counts as visible
to the agent. Unexpected errors are therefore reported by type only: a traceback
or exception message could quote the data being processed.
"""

from __future__ import annotations

import argparse
import sys

from . import extract, render
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .sources import SourceError, source_kinds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datanyx",
        description="Describe data to an AI agent without showing it the data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    schema = commands.add_parser("schema", help="print a value-free schema of the input")
    schema.add_argument("input", help=(
            'file to describe, "-" for stdin, a database URL, or env:NAME to read the '
            "URL from an environment variable (keeps passwords off the command line)"
        ),
    )
    schema.add_argument("--schema", help="database schema to describe (default: the database's default)")
    schema.add_argument("--source", choices=source_kinds(), help="input type (default: detect)")
    schema.add_argument(
        "--format",
        choices=sorted(RENDERERS),
        default="json-schema",
        help="output format (default: %(default)s)",
    )
    schema.add_argument("-o", "--output", help="write to this file instead of stdout")
    schema.add_argument(
        "--map-path",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            'hide all keys of the object at PATH, e.g. "$.users" or "$[].tags", '
            'or all columns of a table, e.g. "$.users" (repeatable)'
        ),
    )
    schema.add_argument(
        "--keep-name",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "show this exact key, table or column name even if it looks like data "
            "(repeatable); --map-path still hides it"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run_schema(args)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
    except RecursionError:
        print("error: input is too deeply nested", file=sys.stderr)
    except Exception as error:
        print(f"error: internal failure ({type(error).__name__})", file=sys.stderr)
    return 1


def _run_schema(args: argparse.Namespace) -> int:
    policy = KeyPolicy(map_paths=frozenset(args.map_path), keep_names=frozenset(args.keep_name))
    schema = extract(args.input, source=args.source, policy=policy, namespace=args.schema)
    output = render(schema, args.format)

    for note in schema.notes:
        print(f"note: {note}", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(output + "\n")
    else:
        # Field names can be non-ASCII, which legacy Windows encodings can't print.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(output)
    return 0
