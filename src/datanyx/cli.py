"""Command line interface.

An agent may be the one running this, so everything it prints counts as visible
to the agent. Unexpected errors are therefore reported by type only: a traceback
or exception message could quote the data being processed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from . import extract, render
from .audit import read_log, summarize_log
from .check import LANGUAGES as CHECK_LANGUAGES
from .check import check_code, detect_language, format_diagnostics, load_tables
from .config import CONFIG_NAME, LOCAL_NAME, STARTER, STARTER_LOCAL, Config, describe, load_config
from .diff import diff_schemas, has_breaking, render_diff, snapshot
from .evaluation import evaluate, load_cases
from .evaluation import render_report as render_eval
from .fileperms import restrict_to_owner
from .privacy import KeyPolicy
from .pseudonymize import Options as PseudonymizeOptions
from .pseudonymize import Pseudonymizer, detect_kind, load_key, parse_overrides, pseudonymize_file, render_plan
from .renderers import RENDERERS
from .schema import Array, Object
from .scrub import ScrubPolicy, scrub, shape, visible_names
from .sources import SourceError, source_kinds
from .sources.files import read_bytes
from .synth import FORMATS as SYNTH_FORMATS
from .synth import MAX_ROWS, file_name, render_table, synthesize, write_sqlite
from .text import (
    DEFAULT_MODEL,
    ENTITY_KINDS,
    SECRET,
    Anonymizer,
    Detector,
    PatternDetector,
    SecretDetector,
    SpacyDetector,
    build_detectors,
    kinds_of,
    load_detector_file,
    restore,
    unknown_placeholders,
)
from .text.backends import BACKEND_KINDS, build_backend
from .text.backends import availability as backend_availability
from .text.batch import BatchResult, anonymize_folder, anonymize_stream, restore_folder
from .text.documents import anonymize_bytes, can_handle, restore_bytes
from .text.mapping import MappingFile, read_mapping
from .text.packs import PACKS, load_packs


class _Parser(argparse.ArgumentParser):
    """No abbreviated options: `--rev` meaning `--reveal` would slip past the guard's list of options."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    """The options that say what to read and what to hide, shared by `schema` and `synth`."""
    parser.add_argument(
        "input",
        help=(
            'file or folder to describe, "-" for stdin, a database URL, or env:NAME to read the '
            "URL from an environment variable (keeps passwords off the command line)"
        ),
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="when the input is a folder, also read its subfolders"
    )
    parser.add_argument("--schema", help="database schema to describe (default: the database's default)")
    parser.add_argument("--source", choices=source_kinds(), help="input type (default: detect)")
    _add_delimiter(parser)
    parser.add_argument(
        "--scale-hints", action="store_true",
        help="also give each table's size as an order of magnitude (\"10^3 to 10^4 rows\"), never a number",
    )
    parser.add_argument(
        "--infer-keys", action="store_true",
        help="also flag key-looking columns that are unique, and joins between tables, as yes/no guesses (CSV, TSV, JSON Lines, JSON and Excel)",
    )
    parser.add_argument("--views", action="store_true", help="also describe database views (names and columns only)")
    parser.add_argument(
        "--include", action="append", default=[], metavar="PATTERN",
        help="describe only tables (or files) whose names match this glob, such as 'sales_*' (repeatable)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help="leave out tables (or files) whose names match this glob (repeatable)",
    )
    parser.add_argument(
        "--max-tables", type=int, metavar="N",
        help="describe at most N tables and say how many were left out; use --format summary to list them all",
    )
    parser.add_argument(
        "--map-path",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            'hide all keys of the object at PATH, e.g. "$.users" or "$[].tags", '
            'or all columns of a table, e.g. "$.users" (repeatable)'
        ),
    )
    parser.add_argument(
        "--keep-name",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "show this exact key, table or column name even if it looks like data "
            "(repeatable); --map-path still hides it"
        ),
    )


def _add_scrub_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--names-from",
        action="append",
        default=[],
        metavar="INPUT",
        help="keep quoted names that this file, folder or database's schema shows, such as column names (repeatable)",
    )
    parser.add_argument("--allow", action="append", default=[], metavar="NAME", help="keep this quoted name (repeatable)")
    parser.add_argument("--hide", action="append", default=[], metavar="TERM", help="always remove this term (repeatable)")
    parser.add_argument("--keep-numbers", action="store_true", help="don't replace long numbers and decimals")
    parser.add_argument(
        "--no-ner",
        action="store_true",
        help="don't use the name-finding model: faster, but names in unquoted text get through",
    )
    parser.add_argument("--ner", action="store_true", help="use the name-finding model even if the config turns it off")
    _add_model_and_detectors(parser)


def _add_model_and_detectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        action="append",
        metavar="MODEL",
        help=(
            f"spaCy NER model (default: the config's, else {DEFAULT_MODEL}). Repeat it, or separate with commas, "
            "to use several, such as one per language"
        ),
    )
    parser.add_argument(
        "--pack",
        action="append",
        default=[],
        metavar="NAME",
        help=f"a pack of detectors for a field: {', '.join(sorted(PACKS))} (repeatable)",
    )
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="SPEC",
        help="another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable)",
    )
    parser.add_argument(
        "--detectors",
        action="append",
        default=[],
        metavar="FILE",
        help="a file of custom detectors for your own formats and names (repeatable)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="datanyx",
        description="Describe data to an AI agent without showing it the data.",
    )
    parser.add_argument("--config", dest="config_file", metavar="FILE", help="settings file to use instead of the nearest datanyx.toml")
    parser.add_argument("--no-config", action="store_true", help="ignore datanyx.toml")
    commands = parser.add_subparsers(dest="command", required=True)

    schema = commands.add_parser("schema", help="print a value-free schema of the input")
    _add_input_arguments(schema)
    schema.add_argument(
        "--format",
        choices=sorted(RENDERERS),
        default="json-schema",
        help="output format (default: %(default)s)",
    )
    schema.add_argument("-o", "--output", help="write to this file instead of stdout")

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

    evaluate_parser = commands.add_parser(
        "eval",
        help="measure how much of your own labelled text is hidden, by kind",
        description=(
            "Run the anonymizer, with the same models, packs, backends and settings `anonymize` would use, "
            "over documents you have labelled, and report recall by kind. Run it again with other settings "
            "to compare them. See the documentation (detection page) for the file format."
        ),
    )
    evaluate_parser.add_argument("cases", help="a JSON or JSON Lines file of labelled documents")
    evaluate_parser.add_argument("--kind", action="append", default=[], metavar="KIND", help="only measure this kind (repeatable)")
    evaluate_parser.add_argument("--no-ner", action="store_true", help="leave out the name-finding models and backends, to see what patterns alone find")
    evaluate_parser.add_argument("--show-misses", action="store_true", help="list the strings that weren't fully hidden (they come from your file: read them yourself)")
    evaluate_parser.add_argument("--min-recall", type=float, metavar="FRACTION", help="exit with 1 if recall, overall or for any kind, is below this (0 to 1)")
    evaluate_parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    evaluate_parser.add_argument("--hide", action="append", default=[], metavar="TERM", help="always hide this term (repeatable)")
    evaluate_parser.add_argument("--keep-entity", action="append", default=[], metavar="KIND", help="leave this kind visible (repeatable)")
    _add_model_and_detectors(evaluate_parser)

    scan_parser = commands.add_parser(
        "scan",
        help="find which files hold personal data or secrets, by kind and count, without showing any",
        description=(
            "Look through a folder for emails, phone numbers, card and IBAN numbers, IP addresses, "
            "addresses and credentials, and report where they are. No value is ever printed. Run it "
            "before pointing an agent at a folder. Names of people and companies need --ner."
        ),
    )
    scan_parser.add_argument("path", nargs="+", metavar="PATH", help="folders or files to scan")
    scan_parser.add_argument(
        "--kinds",
        action="append",
        default=[],
        metavar="KIND",
        help="what to look for (repeatable; default: EMAIL, PHONE, CREDIT_CARD, IBAN, IP_ADDRESS, MAC_ADDRESS, ADDRESS, SECRET and your custom kinds; 'all' for every built-in kind)",
    )
    scan_parser.add_argument("--ner", action="store_true", help="also find names, organisations and places with the NER model (slow)")
    _add_model_and_detectors(scan_parser)
    scan_parser.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip names matching this pattern (repeatable)")
    scan_parser.add_argument("--max-size", type=float, metavar="MB", help="skip files larger than this (default: the config's, else 5)")
    scan_parser.add_argument("--include-examples", action="store_true", help="also count example.com emails and loopback or documentation IPs")
    scan_parser.add_argument("--show-paths", action="store_true", help="show real file names, even ones that look like data (run it yourself)")
    scan_parser.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    scan_parser.add_argument("--fail-on-findings", action="store_true", help="exit with 1 if anything is found or a file that may hold data couldn't be read, for CI or a pre-commit hook")
    scan_parser.add_argument("--allow-unscanned", action="store_true", help="with --fail-on-findings, don't fail because a file was too large or couldn't be read")

    protect = commands.add_parser(
        "protect",
        help="keep an agent from reading data directly (Claude Code permissions, a guard hook, an MCP server)",
        description=(
            "Set up Claude Code so the agent can't read the named files and folders with its own "
            "tools, and can only work with them through DataNyx: schema, synth, check, scrub and "
            "run. By default this only shows what it would change; pass --write to apply it. "
            "It is a guardrail, not a sandbox: a script the agent runs can still read the data."
        ),
    )
    protect.add_argument("paths", nargs="*", metavar="PATH", help="files or folders to protect")
    protect.add_argument("--dir", default=".", metavar="PROJECT", help="the project folder (default: the current folder)")
    protect.add_argument(
        "--command",
        dest="launcher",  # Not "command": that is already the name of the subcommand.
        default="datanyx",
        help="how Claude Code starts datanyx, such as 'uv run datanyx' (default: datanyx)",
    )
    protect.add_argument("--local", action="store_true", help="use .claude/settings.local.json, which isn't shared, instead of settings.json")
    protect.add_argument("--allow-broad-search", action="store_true", help="don't refuse searches of a folder that has protected data inside it")
    protect.add_argument("--no-mcp", action="store_true", help="don't write .mcp.json")
    protect.add_argument("--remove", action="store_true", help="take protection out again")
    protect.add_argument("--write", action="store_true", help="apply the changes (the default is to show them)")

    hook = commands.add_parser(
        "hook",
        help="the Claude Code hook that enforces `protect` (reads a tool call on stdin)",
        description="Used by Claude Code, not by hand: reads a PreToolUse payload and refuses calls that touch protected data.",
    )
    hook.add_argument("--require-config", action="store_true", help="refuse everything if the protection settings are missing; `protect` installs the hook with this")

    check = commands.add_parser(
        "check",
        help="check SQL or pandas code against a schema before running it",
        description=(
            "Find misspelled columns, unknown tables, ambiguous joins and type mistakes in SQL "
            "or pandas code, using the schema of the data it works on. Nothing is run and no "
            "data is read by the code. Exits with 1 if there are errors."
        ),
    )
    check.add_argument("code", help='file with the code (.sql or .py), or "-" for stdin')
    check.add_argument(
        "--against",
        action="append",
        required=True,
        metavar="INPUT",
        help="what the code works on: a file, folder, database URL or env:NAME (repeatable)",
    )
    check.add_argument(
        "--table",
        action="append",
        default=[],
        metavar="NAME",
        help="the table name for the matching --against file, in order; a file has no name of its own in a schema",
    )
    check.add_argument("--language", choices=CHECK_LANGUAGES, help="sql or python (default: from the file extension)")
    check.add_argument("--dialect", help="SQL dialect, such as postgres, mysql, tsql, sqlite (default: guessed from --against)")
    check.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    check.add_argument("--strict", action="store_true", help="exit with 1 for warnings too")
    check.add_argument("-r", "--recursive", action="store_true", help="when an input is a folder, also read its subfolders")
    check.add_argument("--schema", help="database schema to use (default: the database's default)")
    check.add_argument("--source", choices=source_kinds(), help="input type for --against (default: detect)")
    _add_delimiter(check)
    check.add_argument("--views", action="store_true", help="also know the database's views (names and columns only)")
    check.add_argument("--map-path", action="append", default=[], metavar="PATH", help="hide all keys or columns at a path (repeatable)")
    check.add_argument("--keep-name", action="append", default=[], metavar="NAME", help="show this name even if it looks like data (repeatable)")

    scrub_parser = commands.add_parser(
        "scrub",
        help="remove data from error messages and logs, keeping what's needed to debug",
        description=(
            "Filter text so that values are gone but the diagnosis stays: exception types, line "
            "numbers and code are kept, quoted values, secrets, emails and long numbers are not. "
            "Best effort: review the result before sharing it."
        ),
    )
    scrub_parser.add_argument("input", nargs="?", default="-", help='text file to scrub (default: "-", stdin)')
    scrub_parser.add_argument("-o", "--output", help="write to this file instead of stdout")
    _add_scrub_arguments(scrub_parser)

    run = commands.add_parser(
        "run",
        help="run a command and show only a scrubbed view of what it printed",
        description=(
            "Run a command, usually a script that uses real data, and show scrubbed stderr and "
            "(by default) only the size of stdout. This doesn't sandbox the command: it can still "
            "read whatever you can."
        ),
        usage="datanyx run [options] -- COMMAND [ARGS...]",
    )
    _add_scrub_arguments(run)
    run.add_argument(
        "--stdout",
        choices=("shape", "scrubbed", "none"),
        default="shape",
        help="what to show of stdout: only its size (default), a scrubbed copy, or nothing",
    )
    run.add_argument("--timeout", type=float, metavar="SECONDS", help="stop the command after this long")
    run.add_argument("--save-stdout", metavar="FILE", help="also save the real stdout to this new file, for you to read")
    run.add_argument("--save-stderr", metavar="FILE", help="also save the real stderr to this new file, for you to read")
    run.add_argument("program", nargs=argparse.REMAINDER, metavar="COMMAND", help="the command to run, after --")

    text = commands.add_parser(
        "anonymize",
        help="print text with names and other identifiers replaced by placeholders",
        description=(
            "Replace sensitive text with placeholders like <PERSON_1>. Detection can miss "
            "things, so review the result before sharing it."
        ),
    )
    text.add_argument(
        "input",
        help=(
            "a file or a folder to anonymize, or \"-\" for stdin: text, Markdown, logs, HTML, email (.eml), "
            "Word (.docx), PDF, WhatsApp and Slack exports"
        ),
    )
    text.add_argument(
        "-o", "--output", help="write to this file instead of stdout; for a folder, the new folder to write to"
    )
    text.add_argument("-r", "--recursive", action="store_true", help="when the input is a folder, also read its subfolders")
    text.add_argument(
        "--keep-names",
        action="store_true",
        help="when the input is a folder, keep the file names; by default they become file_001, file_002...",
    )
    text.add_argument(
        "--stream",
        action="store_true",
        help='anonymize stdin a line at a time as it arrives, for `tail -f app.log | datanyx anonymize - --stream`',
    )
    _add_model_and_detectors(text)
    text.add_argument(
        "--keep-entity",
        action="append",
        default=[],
        metavar="KIND",
        help=f"leave this kind of entity visible (repeatable): {', '.join(ENTITY_KINDS)}, or a custom kind",
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

    init = commands.add_parser("init", help="create a datanyx.toml of commented settings in this folder")
    init.add_argument(
        "--local", action="store_true", help="create datanyx.local.toml instead, for terms to hide that shouldn't be committed"
    )
    diff = commands.add_parser(
        "diff",
        help="compare two schemas and say what changed and what could break",
        description=(
            "Compare two schemas, each a saved one (from `datanyx schema -o schema.json`) or any input "
            "DataNyx reads, and list what was added, removed or changed, sorted by whether code written "
            "against the old one could break. Exits with 1 if anything breaking changed."
        ),
    )
    diff.add_argument("old", help="the earlier schema: a saved JSON Schema file, or any input")
    diff.add_argument("new", help="the later schema, in the same forms")
    diff.add_argument(
        "--fail-on",
        choices=("breaking", "any", "never"),
        default="breaking",
        help="when to exit with 1 (default: %(default)s)",
    )
    diff.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    diff.add_argument("-r", "--recursive", action="store_true", help="when an input is a folder, also read its subfolders")
    diff.add_argument("--schema", help="database schema to use (default: the database's default)")
    diff.add_argument("--source", choices=source_kinds(), help="input type for live inputs (default: detect)")
    _add_delimiter(diff)
    diff.add_argument("--views", action="store_true", help="also compare the database's views (names and columns only)")
    diff.add_argument("--map-path", action="append", default=[], metavar="PATH", help="hide all keys or columns at a path (repeatable)")
    diff.add_argument("--keep-name", action="append", default=[], metavar="NAME", help="show this name even if it looks like data (repeatable)")

    audit = commands.add_parser(
        "audit",
        help="summarise the audit log an MCP server wrote: which tools were called, on what, and what failed",
    )
    audit.add_argument("log", help="the file given to `datanyx mcp --audit-log`")
    audit.add_argument("--since", metavar="TIME", help="only calls from then on, such as 2025-01-31 or 2025-01-31T09:30:00")
    audit.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    show = commands.add_parser("config", help="show the settings in effect and which files they come from")
    show.add_argument("--reveal", action="store_true", help="also show the terms to hide, which are otherwise only counted")

    doctor = commands.add_parser("doctor", help="check which optional packages and models are installed")
    doctor.add_argument("--model", default=DEFAULT_MODEL, help="spaCy NER model to look for (default: %(default)s)")

    serve = commands.add_parser(
        "mcp",
        help="run an MCP server that gives an agent schemas and anonymized text, not data",
        description=(
            "Serve `describe_data` and `anonymize_file` over MCP (stdio, or HTTP with --http). The agent can only "
            "read files under --root, and never receives values, a mapping, or a restore tool."
        ),
    )
    serve.add_argument(
        "--root", action="append", default=[], metavar="DIR", help="folder the agent may read (repeatable; default: current folder)"
    )
    serve.add_argument("--allow-urls", action="store_true", help="accept database URLs from the agent (they may contain passwords)")
    serve.add_argument(
        "--allow-env", action="append", default=[], metavar="NAME",
        help="let the agent use this environment variable as a database URL, as env:NAME (repeatable); no other variable can be used",
    )
    _add_model_and_detectors(serve)
    serve.add_argument(
        "--keep-entity", action="append", default=[], metavar="KIND",
        help="leave this kind of entity visible in anonymized text (repeatable)",
    )
    serve.add_argument("--scale-hints", action="store_true", help="give agents each table's size as an order of magnitude")
    serve.add_argument("--infer-keys", action="store_true", help="give agents yes/no guesses about unique columns and joins")
    serve.add_argument("--views", action="store_true", help="let agents see database views (names and columns only)")
    serve.add_argument(
        "--max-tables", type=int, default=200, metavar="N",
        help="most tables describe_data returns at once, so a huge database can't flood the agent (default: %(default)s)",
    )
    serve.add_argument(
        "--http", metavar="HOST:PORT",
        help="serve over HTTP at /mcp instead of stdio, so a team can share one server; needs a token (--token-env)",
    )
    serve.add_argument(
        "--token-env", metavar="NAME", default="DATANYX_MCP_TOKEN",
        help="environment variable holding the bearer token HTTP clients must send (default: %(default)s)",
    )
    serve.add_argument("--audit-log", metavar="FILE", help="append a record of every tool call, never values, to this file")
    return parser


def _use_utf8() -> None:
    """Names can be non-ASCII, which legacy Windows encodings can't print."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _keep_line_endings() -> None:
    """For text passed through: Windows would otherwise turn each line ending into two characters."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _use_utf8()
    try:
        commands = {"eval": _run_eval, "pseudonymize": _run_pseudonymize, "diff": _run_diff, "audit": _run_audit, "init": _run_init, "config": _run_config, "scan": _run_scan, "protect": _run_protect, "hook": _run_hook, "check": _run_check, "scrub": _run_scrub, "run": _run_command, "synth": _run_synth, "doctor": _run_doctor, "mcp": _run_mcp, "anonymize": _run_anonymize, "restore": _run_restore, "schema": _run_schema}
        return commands[args.command](args)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
    except RecursionError:
        print("error: input is too deeply nested", file=sys.stderr)
    except Exception as error:
        print(f"error: internal failure ({type(error).__name__})", file=sys.stderr)
    return 1


def _cfg(args: argparse.Namespace) -> Config:
    """The settings in effect, loaded once. `hook` never calls this: it must not depend on them."""
    if not hasattr(args, "_cfg"):
        args._cfg = Config() if args.no_config else load_config(args.config_file)
    return args._cfg


_DELIMITER_NAMES = {"comma": ",", "semicolon": ";", "tab": chr(9), "pipe": "|", "space": " "}


def _delimiter_arg(value: str) -> str:
    delimiter = _DELIMITER_NAMES.get(value.lower(), value)
    if len(delimiter) != 1 or delimiter in (chr(13), chr(10), chr(34)):
        raise argparse.ArgumentTypeError("use one character, such as ';' or '|', or a name: comma, semicolon, tab, pipe, space")
    return delimiter


def _add_delimiter(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--delimiter", type=_delimiter_arg, metavar="CHAR",
        help="the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV)",
    )


def _policy(args: argparse.Namespace) -> KeyPolicy:
    config = _cfg(args)
    return KeyPolicy(
        map_paths=frozenset([*config.map_paths, *getattr(args, "map_path", [])]),
        keep_names=frozenset([*config.keep_names, *getattr(args, "keep_name", [])]),
        scale_hints=bool(getattr(args, "scale_hints", False) or config.scale_hints),
        infer_keys=bool(getattr(args, "infer_keys", False) or config.infer_keys),
        delimiter=getattr(args, "delimiter", None),
        views=bool(getattr(args, "views", False) or config.views),
        include=tuple(getattr(args, "include", None) or ()),
        exclude=tuple(getattr(args, "exclude", None) or ()),
        max_tables=getattr(args, "max_tables", None),
    )


def _models(args: argparse.Namespace) -> list[str]:
    """The spaCy models to use: the command line's, else the config's, else the default."""
    given = [name.strip() for item in (getattr(args, "model", None) or []) for name in item.split(",") if name.strip()]
    config = _cfg(args)
    return given or list(config.models) or ([config.model] if config.model else [DEFAULT_MODEL])


def _backend_detectors(args: argparse.Namespace) -> list[Detector]:
    return [build_backend(spec) for spec in [*_cfg(args).backends, *getattr(args, "backend", [])]]


def _ner_detectors(args: argparse.Namespace) -> list[Detector]:
    """Every model and backend that looks for names, places and the like."""
    return [*(SpacyDetector(name) for name in _models(args)), *_backend_detectors(args)]


def _custom_detectors(args: argparse.Namespace) -> list[Detector]:
    config = _cfg(args)
    detectors = build_detectors(config.detector_entries)
    for path in [*config.detector_files, *map(Path, args.detectors)]:
        detectors.extend(load_detector_file(Path(path)))
    detectors.extend(load_packs([*config.packs, *getattr(args, "pack", [])]))
    return detectors


def _run_init(args: argparse.Namespace) -> int:
    name = LOCAL_NAME if args.local else CONFIG_NAME
    target = Path(name)
    if target.exists():
        raise SourceError(f"{name} already exists here")
    target.write_text(STARTER_LOCAL if args.local else STARTER, encoding="utf-8")
    print(f"Created {name}")
    if args.local:
        _keep_out_of_git(target.resolve())
    return 0


def _keep_out_of_git(path: Path) -> None:
    """Add a file to the .gitignore of the repository it is in, wherever in the repository that is."""
    root = next((folder for folder in path.parents if (folder / ".git").exists()), None)
    if root is None:
        return
    entry = path.relative_to(root).as_posix()
    ignore = root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if entry not in existing.splitlines():
        separator = "" if not existing or existing.endswith(chr(10)) else chr(10)
        ignore.write_text(existing + separator + entry + chr(10), encoding="utf-8")
        print(f"Added {entry} to .gitignore, so the terms in it stay private.")


def _run_diff(args: argparse.Namespace) -> int:
    policy = _policy(args)
    old = snapshot(args.old, policy, args.source, args.schema, args.recursive)
    new = snapshot(args.new, policy, args.source, args.schema, args.recursive)
    differences = diff_schemas(old, new)
    print(render_diff(differences, args.format))
    failed = {"never": False, "any": bool(differences), "breaking": has_breaking(differences)}[args.fail_on]
    return 1 if failed else 0


def _run_audit(args: argparse.Namespace) -> int:
    records, unreadable = read_log(args.log, args.since)
    print(summarize_log(records, unreadable, args.format))
    return 0


def _run_config(args: argparse.Namespace) -> int:
    print(describe(_cfg(args), args.reveal))
    return 0


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


def _run_scan(args: argparse.Namespace) -> int:
    from .scan import DEFAULT_KINDS, NER_KINDS, render_report, scan

    config = _cfg(args)
    max_size = args.max_size if args.max_size is not None else (config.scan_max_size or 5.0)
    if max_size <= 0:
        raise SourceError("--max-size must be above 0")
    custom = _custom_detectors(args)
    custom_kinds = kinds_of(custom)
    ner = args.ner or bool(config.scan_ner)
    detectors: list[Detector] = [*custom, PatternDetector(), SecretDetector()]  # Custom first: it wins ties.
    if ner:
        detectors.extend(SpacyDetector(name) for name in _models(args))
    backends = _backend_detectors(args)
    detectors.extend(backends)  # Named on purpose, so always used, and so is what they report.
    builtin = set(ENTITY_KINDS) | {SECRET} | (set(BACKEND_KINDS) if backends else set())
    requested = list(args.kinds) or list(config.scan_kinds)
    if requested:
        unknown = sorted(set(requested) - builtin - custom_kinds - {"all"})
        if unknown:
            raise SourceError(f"unknown kind {unknown[0]!r}; the choices are {sorted(builtin | custom_kinds)} or 'all'")
        kinds = builtin | custom_kinds if "all" in requested else set(requested)
    else:
        kinds = set(DEFAULT_KINDS) | custom_kinds | (set(NER_KINDS) if ner else set())
    report = scan(
        args.path,
        detectors=detectors,
        kinds=kinds,
        backend_kinds=BACKEND_KINDS if backends and not requested else (),
        excludes=[*config.scan_exclude, *args.exclude],
        max_bytes=int(max_size * 1024 * 1024),
        include_examples=args.include_examples or bool(config.scan_include_examples),
        show_paths=args.show_paths,
    )
    print(render_report(report, args.format))
    if not args.fail_on_findings:
        return 0
    return 1 if not report.clean or not (report.complete or args.allow_unscanned) else 0


def _run_hook(args: argparse.Namespace) -> int:
    from .protect import run_hook

    decision = run_hook(sys.stdin.buffer.read().decode("utf-8", errors="replace"), require_config=args.require_config)
    if decision:
        sys.stdout.write(decision)
    return 0


def _run_protect(args: argparse.Namespace) -> int:
    from .protect import apply, claude_code_warning, plan_protect, plan_remove, self_test, split_command

    project = Path(args.dir).resolve()
    command = split_command(args.launcher)
    targets: list[Path] = []
    if args.remove:
        if args.paths:
            raise SourceError("--remove takes no paths")
        changes = plan_remove(project)
    else:
        if not args.paths:
            raise SourceError("name at least one file or folder to protect")
        targets = [Path(path).resolve() for path in args.paths]
        for target in targets:
            if not target.exists():
                print(f"note: {target} doesn't exist yet; it is protected once it does", file=sys.stderr)
        changes = plan_protect(project, targets, command, args.local, not args.allow_broad_search, not args.no_mcp)

    if not changes:
        print("Already up to date.")
        return 0
    for change in changes:
        action = "delete" if change.after is None else ("create" if not change.before else "update")
        shown = os.path.relpath(change.path, project)
        print(f"{action} {shown}" if args.write else f"would {action} {shown}")
        if not args.write:
            print(change.diff())
            print()
    if not args.write:
        print("Nothing was changed. Run it again with --write to apply this.")
        return 0

    apply(changes)
    if args.remove:
        print("Protection removed.")
        return 0
    problem = self_test(project, command, targets[0])
    print("Checked: the guard hook refuses protected files." if problem is None else f"warning: {problem}")
    version_warning = claude_code_warning()
    if version_warning:
        print(f"warning: {version_warning}", file=sys.stderr)
    return 0 if problem is None else 1


def _run_check(args: argparse.Namespace) -> int:
    language = args.language or (detect_language(args.code) if args.code != "-" else None)
    if language is None:
        raise SourceError("can't tell the language of the code; pass --language sql or --language python")
    if args.code == "-" and "-" in args.against:
        raise SourceError("the code and the data can't both come from stdin")
    code = _read_text(args.code)
    policy = _policy(args)
    tables, guessed = load_tables(args.against, args.table, args.source, policy, args.schema, args.recursive)
    findings = check_code(code, tables, language, args.dialect or guessed)

    print(format_diagnostics(findings, "<stdin>" if args.code == "-" else args.code, args.format))
    failed = any(item.severity == "error" for item in findings) or (args.strict and bool(findings))
    return 1 if failed else 0


def _scrub_policy(args: argparse.Namespace) -> ScrubPolicy:
    config = _cfg(args)
    policy = _policy(args)
    allowed = {*config.scrub_allow, *args.allow}
    for target in [*map(str, config.scrub_names_from), *args.names_from]:
        allowed |= visible_names(extract(target, policy=policy))
    if args.no_ner:
        use_ner = False
    elif args.ner:
        use_ner = True
    else:
        use_ner = True if config.scrub_ner is None else config.scrub_ner
    detectors: list[Detector] = [*_custom_detectors(args), PatternDetector(), SecretDetector()]  # Custom first: it wins ties.
    if use_ner:
        detectors.extend(SpacyDetector(name) for name in _models(args))
    detectors.extend(_backend_detectors(args))  # Named on purpose, so always used.
    keep_numbers = args.keep_numbers or bool(config.scrub_keep_numbers)
    return ScrubPolicy(frozenset(allowed), (*config.hide, *args.hide), tuple(detectors), not keep_numbers)


def _run_scrub(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output)
    policy = _scrub_policy(args)
    cleaned = scrub(_read_text(args.input), policy)
    if args.output:
        _write_text(args.output, cleaned)
    else:
        _keep_line_endings()
        sys.stdout.write(cleaned)
    return 0


def _run_command(args: argparse.Namespace) -> int:
    command = args.program[1:] if args.program[:1] == ["--"] else list(args.program)
    if not command:
        raise SourceError("give a command to run, after --")
    for path in (args.save_stdout, args.save_stderr):
        if path and os.path.exists(path):
            raise SourceError("the file to save to already exists; choose a new name")
    policy = _scrub_policy(args)  # Before running: a missing model shouldn't cost a long run.

    environment = dict(os.environ)
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("PYTHONUTF8", "1")
    timed_out = False
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, timeout=args.timeout, env=environment, check=False
        )
        out, err, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as error:
        out, err, code, timed_out = error.stdout or b"", error.stderr or b"", 124, True
    except FileNotFoundError:
        raise SourceError("cannot run the command: not found") from None
    except OSError as error:
        raise SourceError(f"cannot run the command: {error.strerror}") from None

    raw_out, raw_err = out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")
    if args.save_stdout:
        _write_new(args.save_stdout, raw_out, private=True)
    if args.save_stderr:
        _write_new(args.save_stderr, raw_err, private=True)

    if raw_err:
        sys.stderr.write(scrub(raw_err, policy))
        if not raw_err.endswith(chr(10)):
            sys.stderr.write(chr(10))
    if args.stdout == "scrubbed" and raw_out:
        sys.stdout.write(scrub(raw_out, policy))
    shown = {"shape": "hidden", "scrubbed": "scrubbed", "none": "hidden"}[args.stdout]
    summary = f"datanyx: exit code {code}; stdout {shown} ({shape(raw_out)})"
    print(("datanyx: timed out; " if timed_out else "") + summary, file=sys.stderr)
    return code if 0 <= code <= 255 else 1


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


def _write_new(path: str, text: str, private: bool = False) -> None:
    """Write a file that must not exist yet, so real data next to it can't be overwritten.

    `private` is for real data: the file is readable by its owner alone from the start."""
    try:
        with open(path, "x", encoding="utf-8", newline="", opener=_owner_only if private else None) as stream:
            if private and os.name == "nt" and not restrict_to_owner(path):
                raise SourceError("cannot make the output file private")
            stream.write(text)
    except FileExistsError:
        raise SourceError("the output file already exists; choose a new name") from None
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


def _owner_only(file: str, flags: int) -> int:
    return os.open(file, flags, 0o600)


_OPTIONAL = (
    ("CSV, TSV, JSON, XML, SQLite", "sqlalchemy", "built in"),
    ("YAML", "yaml", "datanyx[yaml]"),
    ("Excel", "openpyxl", "datanyx[excel]"),
    ("Parquet and ORC", "pyarrow", "datanyx[parquet] or datanyx[orc]"),
    ("Avro", "fastavro", "datanyx[avro]"),
    ("DuckDB", "duckdb", "datanyx[duckdb]"),
    ("SAS, Stata and SPSS", "pyreadstat", "datanyx[stats]"),
    ("MongoDB", "pymongo", "datanyx[mongodb]"),
    ("Large JSON streaming", "ijson", "datanyx[streaming]"),
    ("MCP server", "mcp", "datanyx[mcp]"),
    ("PostgreSQL", "psycopg", "datanyx[postgres]"),
    ("MySQL / MariaDB", "pymysql", "datanyx[mysql]"),
    ("SQL Server", "pymssql", "datanyx[mssql]"),
    ("Oracle", "oracledb", "datanyx[oracle]"),
    ("Text anonymization", "spacy", "datanyx[text]"),
)


def _run_doctor(args: argparse.Namespace) -> int:
    import importlib.util

    for label, module, install in _OPTIONAL:
        found = importlib.util.find_spec(module) is not None
        print(f"{'ok     ' if found else 'missing'}  {label}" + ("" if found else f'  ->  pip install "{install}"'))
    if importlib.util.find_spec("spacy") is not None:
        import spacy.util

        found = spacy.util.is_package(args.model)
        print(f"{'ok     ' if found else 'missing'}  NER model {args.model}" + ("" if found else f"  ->  python -m spacy download {args.model}"))
    for name, installed in backend_availability().items():
        hint = "" if installed else ('  ->  pip install "datanyx[presidio]"' if name == "presidio" else "  ->  pip install gliner")
        print(f"{'ok     ' if installed else 'missing'}  backend {name}{hint}")
    print(f"        packs: {', '.join(sorted(PACKS))}")
    return 0


def _run_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run

    config = _cfg(args)
    audit = args.audit_log or (str(config.audit_log) if config.audit_log else None)
    http = _host_and_port(args.http) if args.http else None
    token = os.environ.get(args.token_env, "") if http else None
    if http and not token:
        raise SourceError(f"set the {args.token_env} environment variable to the token clients must send")
    run(
        args.root or [str(root) for root in config.mcp_roots] or ["."],
        args.allow_urls or bool(config.mcp_allow_urls),
        _models(args),
        [*config.keep_entities, *args.keep_entity],
        hide=config.hide,
        detectors=[*_custom_detectors(args), *_backend_detectors(args)],
        audit_log=audit,
        http=http,
        token=token,
        scale_hints=bool(args.scale_hints or config.scale_hints),
        infer_keys=bool(args.infer_keys or config.infer_keys),
        views=bool(args.views or config.views),
        max_tables=args.max_tables,
        allow_env=[*config.mcp_allow_env, *args.allow_env],
        scan_settings={
            "excludes": list(config.scan_exclude),
            "max_bytes": int((config.scan_max_size or 5.0) * 1024 * 1024),
            "include_examples": bool(config.scan_include_examples),
        },
    )
    return 0


def _host_and_port(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host.strip("[]") or not port.isdigit() or not 0 < int(port) < 65536:
        raise SourceError("--http takes HOST:PORT, for example 127.0.0.1:8808")
    return host.strip("[]"), int(port)


def _anonymizer(args: argparse.Namespace) -> Anonymizer:
    return _anonymizer_factory(args)()


def _anonymizer_factory(args: argparse.Namespace, ner: bool = True) -> Callable[[], Anonymizer]:
    """Builds the detectors once, and returns a function that makes a fresh Anonymizer from them."""
    config = _cfg(args)
    custom = _custom_detectors(args)
    keep_entity = [*config.keep_entities, *getattr(args, "keep_entity", [])]
    allowed = set(ENTITY_KINDS) | {SECRET} | kinds_of(custom)
    unknown = [kind for kind in keep_entity if kind not in allowed]
    if unknown:
        raise SourceError(f"unknown entity kind {unknown[0]!r}; the choices are {sorted(allowed)}")
    detectors: list[Detector] = [*custom, PatternDetector(), SecretDetector(), *(_ner_detectors(args) if ner else [])]  # Custom wins ties.
    hide, keep = [*config.hide, *args.hide], [*config.keep, *getattr(args, "keep", [])]
    return lambda: Anonymizer(detectors, hide, keep, keep_entity)


def _run_eval(args: argparse.Namespace) -> int:
    if args.min_recall is not None and not 0 <= args.min_recall <= 1:
        raise SourceError("--min-recall is a fraction from 0 to 1")
    cases = load_cases(args.cases)
    labelled = {kind for case in cases for kind in case.sensitive}
    unknown = sorted(set(args.kind) - labelled)
    if unknown:
        raise SourceError(f"nothing in the file is labelled {unknown[0]}; it has {', '.join(sorted(labelled))}")
    make = _anonymizer_factory(args, ner=not args.no_ner)
    report = evaluate(cases, make, set(args.kind) or None)
    print(render_eval(report, args.show_misses, args.format))
    if args.min_recall is not None:
        failing = report.below(args.min_recall)
        if failing:
            print(f"recall is below {args.min_recall:.0%} for: {', '.join(failing)}", file=sys.stderr)
            return 1
    return 0


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


def _run_anonymize(args: argparse.Namespace) -> int:
    if args.stream:
        return _run_stream(args)
    if args.input != "-" and os.path.isdir(args.input):
        return _run_anonymize_folder(args)
    if args.recursive or args.keep_names:
        raise SourceError("--recursive and --keep-names are for folders")
    _check_paths_differ(args.input, args.output, args.mapping)
    if args.input == "-":
        data, suffix = sys.stdin.buffer.read(), ".txt"
    else:
        data = read_bytes(args.input)
        suffix = Path(args.input).suffix.lower()
        if not can_handle(args.input):
            suffix = ".txt"  # Anything else is read as plain text, as it always has been.
    anonymizer = _anonymizer(args)
    output = anonymize_bytes(data, suffix, anonymizer)
    if not args.output and output.suffix == ".docx":
        raise SourceError("the result is a Word file; use -o FILE")
    for note in output.notes:
        print(f"note: {note}", file=sys.stderr)

    # Saved first: anonymized text that can't be restored is worth less than none.
    if args.mapping:
        _write_mapping(args.mapping, anonymizer.mapping)
    if args.output:
        _write_bytes(args.output, output.data)
    else:
        _keep_line_endings()
        sys.stdout.write(output.data.decode("utf-8"))
    return 0


def _run_anonymize_folder(args: argparse.Namespace) -> int:
    if not args.output:
        raise SourceError("a folder needs -o NEW_FOLDER to write the results to")
    _check_paths_differ(args.input, args.output, args.mapping)
    anonymizer = _anonymizer(args)
    mapping_file = MappingFile(args.mapping) if args.mapping else None

    def save(result: BatchResult) -> None:
        if mapping_file is not None:
            mapping_file.write(anonymizer.mapping, result.files)

    try:
        result = anonymize_folder(args.input, args.output, anonymizer, args.recursive, args.keep_names, save)
    finally:
        if mapping_file is not None:
            mapping_file.close()

    print(f"Anonymized {len(result.files)} file(s) into {args.output}.", file=sys.stderr)
    if not args.keep_names and result.files:
        print("note: files are named file_001, file_002... because names can be sensitive too; --mapping records them", file=sys.stderr)
    for note in result.notes:
        print(f"note: {note}", file=sys.stderr)
    if result.skipped:
        kinds = ", ".join(f"{kind} ({count})" for kind, count in sorted(result.skipped.items()))
        print(f"note: skipped files DataNyx can't anonymize: {kinds}", file=sys.stderr)
    for failure in result.failed:
        print(f"error: could not anonymize {failure}", file=sys.stderr)
    return 1 if result.failed else 0


def _run_stream(args: argparse.Namespace) -> int:
    if args.input != "-":
        raise SourceError("--stream reads stdin: use - as the input")
    if args.recursive or args.keep_names:
        raise SourceError("--recursive and --keep-names are for folders")
    _check_paths_differ(args.output, args.mapping)
    anonymizer = _anonymizer(args)
    mapping_file = MappingFile(args.mapping) if args.mapping else None
    state = {"written": -1, "at": 0.0}

    def sync(force: bool = False) -> None:
        # Rewritten as the mapping grows, at most once a second, so it survives a stopped stream.
        if mapping_file is None or anonymizer.changes == state["written"]:
            return
        now = time.monotonic()
        if force or now - state["at"] >= 1.0:
            mapping_file.write(anonymizer.mapping)
            state["written"], state["at"] = anonymizer.changes, now

    out: IO[str]
    if args.output:
        out = open(args.output, "w", encoding="utf-8", newline="")
    else:
        _keep_line_endings()
        out = sys.stdout
    try:
        anonymize_stream(sys.stdin.buffer, anonymizer, out.write, out.flush, sync)
    except KeyboardInterrupt:
        pass
    finally:
        sync(force=True)
        if mapping_file is not None:
            mapping_file.close()
        if args.output:
            out.close()
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output, args.mapping)
    placeholders, files = read_mapping(args.mapping)
    if args.input != "-" and os.path.isdir(args.input):
        count, unknown = restore_folder(args.input, args.output, placeholders, files)
        if unknown:
            print(f"note: not in the mapping, left as they are: {', '.join(unknown)}", file=sys.stderr)
        print(f"Restored {count} file(s) into {args.output}.", file=sys.stderr)
        return 0
    suffix = Path(args.input).suffix.lower() if args.input != "-" else ".txt"
    if suffix in (".docx", ".html", ".htm"):
        _write_bytes(args.output, restore_bytes(read_bytes(args.input), suffix, placeholders))
        return 0
    text = _read_text(args.input)
    unknown_names = unknown_placeholders(text, placeholders)
    if unknown_names:
        print(f"note: not in the mapping, left as they are: {', '.join(unknown_names)}", file=sys.stderr)
    _write_text(args.output, restore(text, placeholders))
    return 0


def _check_paths_differ(*paths: str | None) -> None:
    """Refuse to overwrite an original, which couldn't be recovered afterwards."""
    real = [path for path in paths if path and path != "-"]
    if any(_same_file(a, b) for index, a in enumerate(real) for b in real[index + 1 :]):
        raise SourceError("the input, output and mapping must be different files")


def _write_text(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


def _write_mapping(path: str, mapping: dict[str, str]) -> None:
    mapping_file = MappingFile(path)
    try:
        mapping_file.write(mapping)
    finally:
        mapping_file.close()


def _write_bytes(path: str, data: bytes) -> None:
    try:
        with open(path, "wb") as stream:
            stream.write(data)
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


def _read_text(target: str) -> str:
    # Read as bytes so that line endings pass through unchanged.
    data = read_bytes(target)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None


def _same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
