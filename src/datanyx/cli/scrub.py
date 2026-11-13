"""`datanyx scrub` and `datanyx run`."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from dataclasses import replace

from .. import extract, render
from ..scrub import ScrubPolicy, scrub, shape, visible_names
from ..sources import SourceError
from ..text import (
    Detector,
    PatternDetector,
    SecretDetector,
    SpacyDetector,
)
from .common import (
    _add_model_and_detectors,
    _backend_detectors,
    _cfg,
    _check_paths_differ,
    _custom_detectors,
    _keep_line_endings,
    _models,
    _policy,
    _read_text,
    _write_new,
    _write_text,
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
    before = {path: _stamp(path) for path in args.describe_output}
    out, err, code, timed_out = _run_to_the_end(command, environment, args.timeout)

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
    for path in args.describe_output:
        _describe_output(path, before[path], args)
    return code if 0 <= code <= 255 else 1


def _run_to_the_end(command: list[str], environment: dict[str, str], timeout: float | None) -> tuple[bytes, bytes, int, bool]:
    """Run the command and return its stdout, stderr, exit code and whether it ran out of time.

    A command is often a launcher (`uv run python report.py`), whose child does the work and holds the output
    open. Stopping the launcher alone would leave us waiting for the child, so the whole tree is stopped.
    """
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            start_new_session=os.name != "nt",  # Its own process group, so the group can be stopped as one.
        )
    except FileNotFoundError:
        raise SourceError("cannot run the command: not found") from None
    except OSError as error:
        raise SourceError(f"cannot run the command: {error.strerror}") from None
    try:
        out, err = process.communicate(timeout=timeout)
        return out, err, process.returncode, False
    except subprocess.TimeoutExpired:
        _stop_tree(process)
        try:
            out, err = process.communicate(timeout=_AFTER_STOP_SECONDS)
        except subprocess.TimeoutExpired as late:  # Something outside the tree still holds the output open.
            out, err = late.stdout or b"", late.stderr or b""
        return out or b"", err or b"", 124, True
    except BaseException:  # Ctrl+C reaches us but not a command in its own group, so it is stopped here.
        _stop_tree(process)
        raise


_AFTER_STOP_SECONDS = 5


def _stop_tree(process: subprocess.Popen) -> None:
    """Stop the command and everything it started."""
    try:
        if sys.platform == "win32":  # sys.platform, not os.name, so type checkers know which branch can run.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=_AFTER_STOP_SECONDS,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()  # Whatever the above did, the command itself must not outlive us.
    except OSError:
        pass


def _stamp(path: str) -> tuple[int, int] | None:
    """What tells one version of a file from the next, or None if it isn't there."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def _describe_output(path: str, before: tuple[int, int] | None, args: argparse.Namespace) -> None:
    """Say what the command wrote to `path` as a schema: its shape, never its values.

    A file the command didn't touch is said to be that, and not described: an old result read as a new one
    is how a wrong answer goes unnoticed."""
    after = _stamp(path)
    if after is None:
        print(f"datanyx: output {path}: not written", file=sys.stderr)
        return
    if after == before:
        print(f"datanyx: output {path}: not changed by the command, so not described", file=sys.stderr)
        return
    try:
        # A result is small next to its source, and its size is the first thing that shows a wrong join or filter.
        schema = extract(path, policy=replace(_policy(args), scale_hints=True))
    except SourceError as error:
        print(f"datanyx: output {path}: cannot describe it: {error}", file=sys.stderr)
        return
    except Exception as error:
        print(f"datanyx: output {path}: cannot describe it ({type(error).__name__})", file=sys.stderr)
        return
    print(f"datanyx: output {path}:")
    print(render(schema, "text"))
    for note in schema.notes:
        print(f"note: {note}", file=sys.stderr)


def add_scrub(commands: argparse._SubParsersAction) -> None:
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


def add_run(commands: argparse._SubParsersAction) -> None:
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
    run.add_argument(
        "--describe-output", action="append", default=[], metavar="FILE",
        help=(
            "after the command ends, print the schema of this file it wrote (a CSV, JSON or Parquet result, say): field names, "
            "types and a size bucket, never values, so a result that came out empty, all null or the wrong shape shows "
            "(repeatable). A file the command didn't write or change is reported as such"
        ),
    )
    run.add_argument("--save-stdout", metavar="FILE", help="also save the real stdout to this new file, for you to read")
    run.add_argument("--save-stderr", metavar="FILE", help="also save the real stderr to this new file, for you to read")
    run.add_argument("program", nargs=argparse.REMAINDER, metavar="COMMAND", help="the command to run, after --")
