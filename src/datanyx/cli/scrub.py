"""`datanyx scrub` and `datanyx run`."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .. import extract
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
    run.add_argument("--save-stdout", metavar="FILE", help="also save the real stdout to this new file, for you to read")
    run.add_argument("--save-stderr", metavar="FILE", help="also save the real stderr to this new file, for you to read")
    run.add_argument("program", nargs=argparse.REMAINDER, metavar="COMMAND", help="the command to run, after --")
