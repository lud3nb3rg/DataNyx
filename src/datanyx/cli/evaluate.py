"""`datanyx eval`."""

from __future__ import annotations

import argparse
import sys

from ..evaluation import evaluate, load_cases
from ..evaluation import render_report as render_eval
from ..sources import SourceError
from .common import (
    _add_model_and_detectors,
    _anonymizer_factory,
)


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


def add_eval(commands: argparse._SubParsersAction) -> None:
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
