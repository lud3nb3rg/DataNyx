"""`datanyx scan`."""

from __future__ import annotations

import argparse

from ..sources import SourceError
from ..text import (
    ENTITY_KINDS,
    SECRET,
    Detector,
    PatternDetector,
    SecretDetector,
    SpacyDetector,
    kinds_of,
)
from ..text.backends import BACKEND_KINDS
from .common import (
    _add_model_and_detectors,
    _backend_detectors,
    _cfg,
    _custom_detectors,
    _models,
)


def _run_scan(args: argparse.Namespace) -> int:
    from ..scan import DEFAULT_KINDS, NER_KINDS, render_report, scan

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


def add_scan(commands: argparse._SubParsersAction) -> None:
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
