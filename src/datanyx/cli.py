"""Command line interface.

An agent may be the one running this, so everything it prints counts as visible
to the agent. Unexpected errors are therefore reported by type only: a traceback
or exception message could quote the data being processed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import extract, render
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .sources import SourceError, source_kinds
from .text import (
    DEFAULT_MODEL,
    ENTITY_KINDS,
    Detector,
    PatternDetector,
    SpacyDetector,
    anonymize_with_mapping,
    restore,
    unknown_placeholders,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datanyx",
        description="Describe data to an AI agent without showing it the data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    schema = commands.add_parser("schema", help="print a value-free schema of the input")
    schema.add_argument("input", help=(
            'file or folder to describe, "-" for stdin, a database URL, or env:NAME to read the '
            "URL from an environment variable (keeps passwords off the command line)"
        ),
    )
    schema.add_argument(
        "-r", "--recursive", action="store_true", help="when the input is a folder, also read its subfolders"
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
    text.add_argument(
        "--mapping",
        metavar="FILE",
        help=(
            "also save each placeholder's real value to this new JSON file, for use with "
            "`datanyx restore`. It holds the sensitive data: keep it away from agents"
        ),
    )

    back = commands.add_parser(
        "restore",
        help="put real values back in place of placeholders, using a saved mapping",
        description="Put real values back. The result is sensitive, so don't run this through an agent.",
    )
    back.add_argument("input", help='UTF-8 text containing placeholders, or "-" for stdin')
    back.add_argument("--mapping", required=True, metavar="FILE", help="file saved by `anonymize --mapping`")
    # Required so that real values can't land in an agent's view of stdout by default.
    back.add_argument("-o", "--output", required=True, help="file to write the restored text to")

    doctor = commands.add_parser("doctor", help="check which optional packages and models are installed")
    doctor.add_argument("--model", default=DEFAULT_MODEL, help="spaCy NER model to look for (default: %(default)s)")

    serve = commands.add_parser(
        "mcp",
        help="run an MCP server that gives an agent schemas and anonymized text, not data",
        description=(
            "Serve `describe_data` and `anonymize_file` over MCP (stdio). The agent can only "
            "read files under --root, and never receives values, a mapping, or a restore tool."
        ),
    )
    serve.add_argument(
        "--root", action="append", default=[], metavar="DIR", help="folder the agent may read (repeatable; default: current folder)"
    )
    serve.add_argument("--allow-urls", action="store_true", help="accept database URLs from the agent (they may contain passwords)")
    serve.add_argument("--model", default=DEFAULT_MODEL, help="spaCy NER model (default: %(default)s)")
    serve.add_argument(
        "--keep-entity", action="append", default=[], choices=ENTITY_KINDS, metavar="KIND",
        help="leave this kind of entity visible in anonymized text (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        commands = {"doctor": _run_doctor, "mcp": _run_mcp, "anonymize": _run_anonymize, "restore": _run_restore, "schema": _run_schema}
        return commands[args.command](args)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
    except RecursionError:
        print("error: input is too deeply nested", file=sys.stderr)
    except Exception as error:
        print(f"error: internal failure ({type(error).__name__})", file=sys.stderr)
    return 1


def _run_schema(args: argparse.Namespace) -> int:
    policy = KeyPolicy(map_paths=frozenset(args.map_path), keep_names=frozenset(args.keep_name))
    schema = extract(args.input, source=args.source, policy=policy, namespace=args.schema, recursive=args.recursive)
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


_OPTIONAL = (
    ("CSV, TSV, JSON, XML, SQLite", "sqlalchemy", "built in", None),
    ("YAML", "yaml", "datanyx[yaml]", None),
    ("Excel", "openpyxl", "datanyx[excel]", None),
    ("Parquet", "pyarrow", "datanyx[parquet]", None),
    ("Large JSON streaming", "ijson", "datanyx[streaming]", None),
    ("MCP server", "mcp", "datanyx[mcp]", None),
    ("PostgreSQL", "psycopg", "datanyx[postgres]", None),
    ("MySQL / MariaDB", "pymysql", "datanyx[mysql]", None),
    ("SQL Server", "pymssql", "datanyx[mssql]", None),
    ("Oracle", "oracledb", "datanyx[oracle]", None),
    ("Text anonymization", "spacy", "datanyx[text]", None),
)


def _run_doctor(args: argparse.Namespace) -> int:
    import importlib.util

    for label, module, install, _ in _OPTIONAL:
        found = importlib.util.find_spec(module) is not None
        print(f"{'ok     ' if found else 'missing'}  {label}" + ("" if found else f'  ->  pip install "{install}"'))
    if importlib.util.find_spec("spacy") is not None:
        import spacy.util

        found = spacy.util.is_package(args.model)
        print(f"{'ok     ' if found else 'missing'}  NER model {args.model}" + ("" if found else f"  ->  python -m spacy download {args.model}"))
    return 0


def _run_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run

    run(args.root or ["."], args.allow_urls, args.model, args.keep_entity)
    return 0


def _run_anonymize(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output, args.mapping)
    text = _read_text(args.input)
    detectors: list[Detector] = [PatternDetector(), SpacyDetector(args.model)]
    output, mapping = anonymize_with_mapping(text, detectors, args.hide, args.keep, args.keep_entity)

    # Saved first: anonymized text that can't be restored is worth less than none.
    if args.mapping:
        _write_mapping(args.mapping, mapping)
    if args.output:
        _write_text(args.output, output)
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", newline="")
        sys.stdout.write(output)
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output, args.mapping)
    text = _read_text(args.input)
    mapping = _read_mapping(args.mapping)
    unknown = unknown_placeholders(text, mapping)
    if unknown:
        print(f"note: not in the mapping, left as they are: {', '.join(unknown)}", file=sys.stderr)
    _write_text(args.output, restore(text, mapping))
    return 0


def _check_paths_differ(*paths: str | None) -> None:
    """Refuse to overwrite an original, which couldn't be recovered afterwards."""
    real = [path for path in paths if path and path != "-"]
    if any(_same_file(a, b) for index, a in enumerate(real) for b in real[index + 1 :]):
        raise SourceError("the input, output and mapping must be different files")


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def _write_mapping(path: str, mapping: dict[str, str]) -> None:
    # Placeholders are only meaningful for one run, so replacing an existing mapping
    # would make earlier output impossible to restore. The file is created owner-only
    # (mode 600 on POSIX), and restricted again below on Windows, before any data is written.
    def owner_only(file: str, flags: int) -> int:
        return os.open(file, flags, 0o600)

    try:
        with open(path, "x", encoding="utf-8", opener=owner_only) as stream:
            _restrict_on_windows(path)
            json.dump(mapping, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise SourceError("the mapping file already exists; choose a new name") from None
    except OSError as error:
        raise SourceError(f"cannot write the mapping: {error.strerror}") from None


def _restrict_on_windows(path: str) -> None:
    """Leave only the current user with access, since `os.open` modes do nothing on Windows."""
    if sys.platform != "win32":
        return
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    account = rf"{domain}\{user}" if domain and user else user
    try:
        result = subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{account}:(R,W,D)"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        restricted = result.returncode == 0 and bool(user)
    except (OSError, subprocess.SubprocessError):
        restricted = False
    if not restricted:
        print("note: couldn't restrict the mapping file's permissions; keep it somewhere private", file=sys.stderr)


def _read_mapping(path: str) -> dict[str, str]:
    # Every failure message is fixed, since a mapping's content is sensitive by nature.
    try:
        with open(path, encoding="utf-8") as stream:
            mapping = json.load(stream)
    except OSError as error:
        raise SourceError(f"cannot read the mapping: {error.strerror}") from None
    except ValueError:
        raise SourceError("the mapping file isn't valid JSON") from None
    if not isinstance(mapping, dict) or not all(isinstance(v, str) for v in mapping.values()):
        raise SourceError("the mapping file isn't in the expected format")
    return mapping


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
