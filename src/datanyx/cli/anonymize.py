"""`datanyx anonymize` and `datanyx restore`."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import IO

from ..sources import SourceError
from ..sources.files import read_bytes
from ..text import (
    ENTITY_KINDS,
    restore,
    unknown_placeholders,
)
from ..text.batch import BatchResult, anonymize_folder, anonymize_stream, restore_folder
from ..text.documents import anonymize_bytes, can_handle, restore_bytes
from ..text.mapping import MappingFile, read_mapping
from .common import (
    _add_model_and_detectors,
    _anonymizer,
    _check_paths_differ,
    _keep_line_endings,
    _read_text,
    _write_bytes,
    _write_mapping,
    _write_text,
)


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


def add_anonymize(commands: argparse._SubParsersAction) -> None:
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


def add_restore(commands: argparse._SubParsersAction) -> None:
    back = commands.add_parser(
        "restore",
        help="put real values back in place of placeholders, using a saved mapping",
        description="Put real values back. The result is sensitive, so don't run this through an agent.",
    )
    back.add_argument("input", help='UTF-8 text containing placeholders, or "-" for stdin')
    back.add_argument("--mapping", required=True, metavar="FILE", help="file saved by `anonymize --mapping`")
    # Required so that real values can't land in an agent's view of stdout by default.
    back.add_argument("-o", "--output", required=True, help="file to write the restored text to")
