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
from pathlib import Path

from . import extract, render
from .audit import read_log, summarize_log
from .check import LANGUAGES as CHECK_LANGUAGES
from .check import check_code, detect_language, format_diagnostics, load_tables
from .config import CONFIG_NAME, LOCAL_NAME, STARTER, STARTER_LOCAL, Config, describe, load_config
from .diff import diff_schemas, has_breaking, render_diff, snapshot
from .fileperms import restrict_to_owner
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .schema import Array, Object
from .scrub import ScrubPolicy, scrub, shape, visible_names
from .sources import SourceError, source_kinds
from .synth import FORMATS as SYNTH_FORMATS
from .synth import MAX_ROWS, file_name, render_table, synthesize, write_sqlite
from .text import (
    DEFAULT_MODEL,
    ENTITY_KINDS,
    SECRET,
    Detector,
    PatternDetector,
    SecretDetector,
    SpacyDetector,
    anonymize_with_mapping,
    build_detectors,
    kinds_of,
    load_detector_file,
    restore,
    unknown_placeholders,
)


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
    parser.add_argument("--model", help=f"spaCy NER model (default: the config's, else {DEFAULT_MODEL})")
    parser.add_argument(
        "--detectors",
        action="append",
        default=[],
        metavar="FILE",
        help="a file of custom detectors for your own formats and names (repeatable)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
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

    scan_parser = commands.add_parser(
        "scan",
        help="find which files hold personal data or secrets, by kind and count, without showing any",
        description=(
            "Look through a folder for emails, phone numbers, card and IBAN numbers, IP addresses, "
            "addresses and credentials, and report where they are. No value is ever printed. Run it "
            "before pointing an agent at a folder. Names of people and companies need --ner."
        ),
    )
    scan_parser.add_argument("path", help="folder or file to scan")
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
    scan_parser.add_argument("--fail-on-findings", action="store_true", help="exit with 1 if anything is found, for CI or a pre-commit hook")

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

    commands.add_parser(
        "hook",
        help="the Claude Code hook that enforces `protect` (reads a tool call on stdin)",
        description="Used by Claude Code, not by hand: reads a PreToolUse payload and refuses calls that touch protected data.",
    )

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
    text.add_argument("input", help='UTF-8 text file to anonymize, or "-" for stdin')
    text.add_argument("-o", "--output", help="write to this file instead of stdout")
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
            "Serve `describe_data` and `anonymize_file` over MCP (stdio). The agent can only "
            "read files under --root, and never receives values, a mapping, or a restore tool."
        ),
    )
    serve.add_argument(
        "--root", action="append", default=[], metavar="DIR", help="folder the agent may read (repeatable; default: current folder)"
    )
    serve.add_argument("--allow-urls", action="store_true", help="accept database URLs from the agent (they may contain passwords)")
    _add_model_and_detectors(serve)
    serve.add_argument(
        "--keep-entity", action="append", default=[], metavar="KIND",
        help="leave this kind of entity visible in anonymized text (repeatable)",
    )
    serve.add_argument("--audit-log", metavar="FILE", help="append a record of every tool call, never values, to this file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        commands = {"diff": _run_diff, "audit": _run_audit, "init": _run_init, "config": _run_config, "scan": _run_scan, "protect": _run_protect, "hook": _run_hook, "check": _run_check, "scrub": _run_scrub, "run": _run_command, "synth": _run_synth, "doctor": _run_doctor, "mcp": _run_mcp, "anonymize": _run_anonymize, "restore": _run_restore, "schema": _run_schema}
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


def _policy(args: argparse.Namespace) -> KeyPolicy:
    config = _cfg(args)
    return KeyPolicy(
        map_paths=frozenset([*config.map_paths, *getattr(args, "map_path", [])]),
        keep_names=frozenset([*config.keep_names, *getattr(args, "keep_name", [])]),
    )


def _model(args: argparse.Namespace) -> str:
    return args.model or _cfg(args).model or DEFAULT_MODEL


def _custom_detectors(args: argparse.Namespace) -> list[Detector]:
    config = _cfg(args)
    detectors = build_detectors(config.detector_entries)
    for path in [*config.detector_files, *map(Path, args.detectors)]:
        detectors.extend(load_detector_file(Path(path)))
    return detectors


def _run_init(args: argparse.Namespace) -> int:
    name = LOCAL_NAME if args.local else CONFIG_NAME
    target = Path(name)
    if target.exists():
        raise SourceError(f"{name} already exists here")
    target.write_text(STARTER_LOCAL if args.local else STARTER, encoding="utf-8")
    print(f"Created {name}")
    if args.local:
        ignore = Path(".gitignore")
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        if Path(".git").exists() and name not in existing.splitlines():
            separator = "" if not existing or existing.endswith(chr(10)) else chr(10)
            ignore.write_text(existing + separator + name + chr(10), encoding="utf-8")
            print(f"Added {name} to .gitignore, so the terms in it stay private.")
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    policy = _policy(args)
    old = snapshot(args.old, policy, args.source, args.schema, args.recursive)
    new = snapshot(args.new, policy, args.source, args.schema, args.recursive)
    differences = diff_schemas(old, new)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(render_diff(differences, args.format))
    failed = {"never": False, "any": bool(differences), "breaking": has_breaking(differences)}[args.fail_on]
    return 1 if failed else 0


def _run_audit(args: argparse.Namespace) -> int:
    records, unreadable = read_log(args.log, args.since)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(summarize_log(records, unreadable, args.format))
    return 0


def _run_config(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(describe(_cfg(args), args.reveal))
    return 0


def _run_schema(args: argparse.Namespace) -> int:
    policy = _policy(args)
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
        detectors.append(SpacyDetector(_model(args)))
    builtin = set(ENTITY_KINDS) | {SECRET}
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
        excludes=[*config.scan_exclude, *args.exclude],
        max_bytes=int(max_size * 1024 * 1024),
        include_examples=args.include_examples or bool(config.scan_include_examples),
        show_paths=args.show_paths,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(render_report(report, args.format))
    return 1 if args.fail_on_findings and not report.clean else 0


def _run_hook(args: argparse.Namespace) -> int:
    from .protect import run_hook

    decision = run_hook(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    if decision:
        sys.stdout.write(decision)
    return 0


def _run_protect(args: argparse.Namespace) -> int:
    from .protect import apply, plan_protect, plan_remove, self_test, split_command

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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
    detectors: list[Detector] = [*_custom_detectors(args), PatternDetector()]  # Custom first: it wins ties.
    if use_ner:
        detectors.append(SpacyDetector(_model(args)))
    keep_numbers = args.keep_numbers or bool(config.scrub_keep_numbers)
    return ScrubPolicy(frozenset(allowed), (*config.hide, *args.hide), tuple(detectors), not keep_numbers)


def _run_scrub(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output)
    policy = _scrub_policy(args)
    cleaned = scrub(_read_text(args.input), policy)
    if args.output:
        _write_text(args.output, cleaned)
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", newline="")
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
        _write_new(args.save_stdout, raw_out)
    if args.save_stderr:
        _write_new(args.save_stderr, raw_err)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
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
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", newline="")
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


def _write_new(path: str, text: str) -> None:
    """Write a file that must not exist yet, so real data next to it can't be overwritten."""
    try:
        with open(path, "x", encoding="utf-8", newline="") as stream:
            stream.write(text)
    except FileExistsError:
        raise SourceError("the output file already exists; choose a new name") from None
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


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

    config = _cfg(args)
    audit = args.audit_log or (str(config.audit_log) if config.audit_log else None)
    run(
        args.root or [str(root) for root in config.mcp_roots] or ["."],
        args.allow_urls or bool(config.mcp_allow_urls),
        _model(args),
        [*config.keep_entities, *args.keep_entity],
        hide=config.hide,
        detectors=_custom_detectors(args),
        audit_log=audit,
    )
    return 0


def _run_anonymize(args: argparse.Namespace) -> int:
    _check_paths_differ(args.input, args.output, args.mapping)
    text = _read_text(args.input)
    config = _cfg(args)
    custom = _custom_detectors(args)
    keep_entity = [*config.keep_entities, *args.keep_entity]
    allowed = set(ENTITY_KINDS) | {SECRET} | kinds_of(custom)
    unknown = [kind for kind in keep_entity if kind not in allowed]
    if unknown:
        raise SourceError(f"unknown entity kind {unknown[0]!r}; the choices are {sorted(allowed)}")
    detectors: list[Detector] = [*custom, PatternDetector(), SpacyDetector(_model(args))]  # Custom wins ties.
    output, mapping = anonymize_with_mapping(
        text, detectors, [*config.hide, *args.hide], [*config.keep, *args.keep], keep_entity
    )

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
    if sys.platform == "win32" and not restrict_to_owner(path):
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
