"""Command line interface.

An agent may be the one running this, so everything it prints counts as visible
to the agent. Unexpected errors are therefore reported by type only: a traceback
or exception message could quote the data being processed.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import extract, render
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .sources import SourceError, source_kinds
from .text import DEFAULT_MODEL, ENTITY_KINDS, PatternDetector, SpacyDetector, anonymize


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

    text = commands.add_parser(
        "anonymize",
        help="print text with names and other identifiers replaced by placeholders",
        description=(
            "Replace sensitive text with placeholders like <PERSON_1>. Detection can miss "
            "things, so review the result before sharing it."
        ),
    )
    text.add_argument("input", help='UTF-8 text file to anonymize, or "-" for stdin')
    text.add_argument("-o", "--output", help="write to this file instead of stdout")
    text.add_argument("--model", default=DEFAULT_MODEL, help="spaCy NER model (default: %(default)s)")
    text.add_argument(
        "--keep-entity",
        action="append",
        default=[],
        choices=ENTITY_KINDS,
        metavar="KIND",
        help=f"leave this kind of entity visible (repeatable): {', '.join(ENTITY_KINDS)}",
    )
    text.add_argument(
        "--hide",
        action="append",
        default=[],
        metavar="TERM",
        help="always hide this term, ignoring case (repeatable)",
    )
    text.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="TERM",
        help="leave this exact term visible even if detected (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run_anonymize(args) if args.command == "anonymize" else _run_schema(args)
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


def _run_anonymize(args: argparse.Namespace) -> int:
    if args.output and args.input != "-" and _same_file(args.input, args.output):
        # The original couldn't be recovered, and detection might have been imperfect.
        raise SourceError("the output file would overwrite the input")
    text = _read_text(args.input)
    detectors = [PatternDetector(), SpacyDetector(args.model)]
    output = anonymize(text, detectors, args.hide, args.keep, args.keep_entity)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as stream:
            stream.write(output)
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", newline="")
        sys.stdout.write(output)
    return 0


def _read_text(target: str) -> str:
    # Read as bytes so that line endings pass through unchanged.
    try:
        if target == "-":
            data = sys.stdin.buffer.read()
        else:
            with open(target, "rb") as stream:
                data = stream.read()
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None


def _same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
