"""Measure how much of your own labelled text the anonymizer actually hides.

The README's accuracy table is for invented names in invented sentences. What matters is your
text, your names, your languages. Label a small sample (a few dozen documents is enough to see
the pattern), run `datanyx eval`, and you get recall by kind: how many of the strings you said
are sensitive come out hidden.

A labelled file holds documents, each with its text and what in it is sensitive, either as
strings or as character spans:

    [{"name": "ticket 12", "text": "Jessica Whitfield wrote from jw@acme.io",
      "sensitive": {"PERSON": ["Jessica Whitfield"], "EMAIL": ["jw@acme.io"]}},
     {"text": "Call Ana on 555-0100", "spans": [{"start": 5, "end": 8, "kind": "PERSON"}]}]

The same file can be compared across settings: change the model, add a `--backend`, switch on a
`--pack`, and run it again.

A string counts as **hidden** when none of its identifying words survives in the output, **partly
hidden** when some do ("Jessica" left behind a hidden "Whitfield"), and **missed** when all of it
does. Titles and joining words are not identifying. Replacements the labels don't account for are
counted too, as a hint at over-hiding or at gaps in the labels.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sources.base import SourceError
from .text.anonymizer import Anonymizer

_GENERIC = frozenset(
    ["dr", "dr.", "ms", "ms.", "mrs", "mrs.", "mr", "mr.", "st", "st.", "prof", "prof.", "and", "the", "of", "de", "la", "le", "les", "du", "des", "van", "von", "der", "den", "di", "da", "dos", "del", "el", "al", "bin", "sons", "bank", "hospital", "clinic", "inc", "inc.", "ltd", "ltd.", "llc", "corp", "corp.", "co", "co.", "&"]
)
_PLACEHOLDER = re.compile(r"<([A-Z_]+)_\d+>")


@dataclass
class Case:
    name: str
    text: str
    sensitive: dict[str, list[str]]


@dataclass
class KindResult:
    total: int = 0
    hidden: int = 0
    partial: int = 0
    missed: int = 0

    @property
    def recall(self) -> float:
        return self.hidden / self.total if self.total else 1.0


@dataclass
class EvalReport:
    documents: int = 0
    kinds: dict[str, KindResult] = field(default_factory=dict)
    #: (document, kind, string, "missed" or "partly hidden"), for the person who owns the labels.
    misses: list[tuple[str, str, str, str]] = field(default_factory=list)
    unlabelled: Counter[str] = field(default_factory=Counter)

    @property
    def overall(self) -> KindResult:
        total = KindResult()
        for result in self.kinds.values():
            total.total += result.total
            total.hidden += result.hidden
            total.partial += result.partial
            total.missed += result.missed
        return total

    def below(self, minimum: float) -> list[str]:
        """The kinds (or "overall") whose recall is under `minimum`."""
        failing = [kind for kind, result in sorted(self.kinds.items()) if result.recall < minimum]
        return (["overall"] if self.overall.recall < minimum else []) + failing


# --- loading ---------------------------------------------------------------------------------------------


def load_cases(path: str | Path) -> list[Case]:
    """Labelled documents from a JSON file (a list, or an object with `documents`) or JSON Lines."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as error:
        raise SourceError(f"cannot read the labelled file: {error.strerror}") from None
    except UnicodeDecodeError:
        raise SourceError("the labelled file isn't valid UTF-8") from None
    try:
        if str(path).lower().endswith((".jsonl", ".ndjson")):
            items: Any = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            items = json.loads(text)
    except ValueError:
        raise SourceError("the labelled file isn't valid JSON") from None
    if isinstance(items, dict):
        items = items.get("documents")
    if not isinstance(items, list) or not items:
        raise SourceError("the labelled file should hold a list of documents")
    return [_case(item, number) for number, item in enumerate(items, start=1)]


def _case(item: Any, number: int) -> Case:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        raise SourceError(f"document {number} needs a text")
    name = str(item.get("name") or f"document {number}")
    text = item["text"]
    sensitive: dict[str, list[str]] = {}
    if isinstance(item.get("sensitive"), dict):
        for kind, strings in item["sensitive"].items():
            if not isinstance(strings, list) or not all(isinstance(s, str) for s in strings):
                raise SourceError(f"{name}: the strings for {kind} should be a list of text")
            sensitive[str(kind)] = list(strings)
    if isinstance(item.get("spans"), list):
        for span in item["spans"]:
            try:
                start, end, kind = int(span["start"]), int(span["end"]), str(span["kind"])
            except (KeyError, TypeError, ValueError):
                raise SourceError(f"{name}: each span needs a start, an end and a kind") from None
            if not 0 <= start < end <= len(text):
                raise SourceError(f"{name}: a span lies outside the text")
            sensitive.setdefault(kind, []).append(text[start:end])
    if not sensitive:
        raise SourceError(f"{name}: nothing is labelled as sensitive")
    for kind, strings in sensitive.items():
        if any(string not in text for string in strings):
            raise SourceError(f"{name}: a string labelled {kind} isn't in the text")
    return Case(name, text, sensitive)


# --- measuring --------------------------------------------------------------------------------------------


def evaluate(cases: list[Case], make_anonymizer: Callable[[], Anonymizer], kinds: set[str] | None = None) -> EvalReport:
    """Anonymize each document on its own, as `datanyx anonymize` does, and check the labels."""
    report = EvalReport(documents=len(cases))
    for case in cases:
        anonymizer = make_anonymizer()
        output = anonymizer.anonymize(case.text)
        labelled = [s.casefold() for strings in case.sensitive.values() for s in strings]
        for kind, strings in case.sensitive.items():
            if kinds is not None and kind not in kinds:
                continue
            result = report.kinds.setdefault(kind, KindResult())
            for string in strings:
                result.total += 1
                outcome = _outcome(string, output)
                if outcome == "hidden":
                    result.hidden += 1
                elif outcome == "partly hidden":
                    result.partial += 1
                else:
                    result.missed += 1
                if outcome != "hidden":
                    report.misses.append((case.name, kind, string, outcome))
        for placeholder, value in anonymizer.mapping.items():
            match = _PLACEHOLDER.fullmatch(placeholder)
            low = value.casefold()
            if match and not any(low in label or label in low for label in labelled):
                report.unlabelled[match.group(1)] += 1
    return report


def _outcome(string: str, output: str) -> str:
    words = [w for w in (word.strip(".,;:()'\"") for word in string.split()) if w and w.casefold() not in _GENERIC]
    words = words or [string]
    # Exact case: a label is spelled as it is in the text, and "Will Turner" isn't leaked by "will".
    survivors = [w for w in words if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", output)]
    if not survivors:
        return "hidden"
    return "missed" if len(survivors) == len(words) else "partly hidden"


# --- output -------------------------------------------------------------------------------------------------


def render_report(report: EvalReport, show_misses: bool = False, output_format: str = "text") -> str:
    overall = report.overall
    if output_format == "json":
        def row(result: KindResult) -> dict[str, Any]:
            return {"total": result.total, "hidden": result.hidden, "partly_hidden": result.partial, "missed": result.missed, "recall": round(result.recall, 4)}

        document: dict[str, Any] = {
            "documents": report.documents,
            "overall": row(overall),
            "kinds": {kind: row(result) for kind, result in sorted(report.kinds.items())},
            "replaced_but_not_labelled": dict(report.unlabelled),
        }
        if show_misses:
            document["misses"] = [{"document": d, "kind": k, "string": s, "outcome": o} for d, k, s, o in report.misses]
        return json.dumps(document, indent=2, ensure_ascii=False)

    lines = [f"Evaluated {report.documents:,} document{'s' if report.documents != 1 else ''}, {overall.total:,} labelled strings.", ""]
    header = f"{'Kind':<16}{'Strings':>8}{'Hidden':>8}{'Partly':>8}{'Missed':>8}{'Recall':>9}"
    lines.append(header)
    for kind, result in sorted(report.kinds.items()):
        lines.append(_row(kind, result))
    lines.append(_row("Overall", overall))
    if report.unlabelled:
        extra = ", ".join(f"{kind} {count}" for kind, count in report.unlabelled.most_common())
        lines += [
            "",
            f"Replaced but not in your labels: {sum(report.unlabelled.values())} ({extra}). "
            "That is over-hiding, or something the labels left out.",
        ]
    if show_misses and report.misses:
        lines += ["", "Not fully hidden:"]
        lines += [f"  {outcome:<13} {kind:<12} {string!r}  ({document})" for document, kind, string, outcome in report.misses]
    elif report.misses:
        lines += ["", f"{len(report.misses)} string(s) weren't fully hidden. --show-misses lists them, for you to read."]
    return chr(10).join(lines)


def _row(label: str, result: KindResult) -> str:
    return f"{label:<16}{result.total:>8}{result.hidden:>8}{result.partial:>8}{result.missed:>8}{result.recall * 100:>8.1f}%"
