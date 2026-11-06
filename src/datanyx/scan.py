"""Find where personal data and secrets are in a folder, without showing any of it.

For checking a repository or an export folder before an agent is pointed at it. The
report says which files hold what kinds of sensitive text, how many, and on which
lines. It never contains a value.

File names can be sensitive too, so a name is shown only if it passes the same test as
a column name; others appear as `file_N`. Pass `show_paths` when you are reading the
report yourself.

What it finds is what the text detectors find: emails, phone numbers, card and IBAN
numbers, IP addresses, addresses and credentials, plus names, organisations and places
when a NER model is used. Binary files (spreadsheets, Parquet, databases) can't be read
as text, so they are listed as not scanned, and a report that left out such a file, one
too large, or one it couldn't open doesn't claim to be complete.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
from bisect import bisect_left
from codecs import BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .privacy import KeyPolicy
from .sources.base import SourceError
from .text import SECRET, Detector, PatternDetector, SecretDetector, Span
from .text.detectors import (
    ADDRESS,
    CREDIT_CARD,
    EMAIL,
    IBAN,
    IP_ADDRESS,
    LOCATION,
    MAC_ADDRESS,
    ORG,
    PERSON,
    PHONE,
)

#: Kinds reported by default. The others (dates, money, URLs, bare IDs) match too much
#: ordinary code to be useful in a scan, but can be asked for.
DEFAULT_KINDS = frozenset({EMAIL, PHONE, CREDIT_CARD, IBAN, IP_ADDRESS, MAC_ADDRESS, ADDRESS, SECRET})
NER_KINDS = frozenset({PERSON, ORG, LOCATION})
DEFAULT_EXCLUDES = (
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".tox", ".hypothesis", ".idea", ".vscode", "dist", "build", ".eggs", "*.egg-info",
)
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
MAX_LINES_LISTED = 20
# Containers that hold records but can't be read as text.
_DATA_EXTENSIONS = frozenset(
    {".xlsx", ".xlsm", ".xls", ".parquet", ".pq", ".feather", ".orc", ".avro", ".db", ".sqlite", ".sqlite3", ".mdb",
     ".accdb", ".sav", ".zsav", ".por", ".dta", ".sas7bdat", ".xpt", ".duckdb", ".ddb", ".pkl", ".pickle", ".npy", ".npz",
     ".h5", ".hdf5", ".zip", ".gz", ".7z", ".tar"}
)
_EXAMPLE_DOMAIN = re.compile(r"(?i)@(?:[\w.-]*\.)?(?:example\.(?:com|org|net)|example|test|invalid|localhost)$|@localhost$|noreply")
_DOCUMENTATION_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"))


@dataclass
class FileFinding:
    path: str
    display: str
    counts: dict[str, int]
    lines: dict[str, list[int]]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class ScanReport:
    scanned: int = 0
    findings: list[FileFinding] = field(default_factory=list)
    too_large: int = 0
    binary: int = 0
    unreadable: int = 0
    not_scanned_data: list[str] = field(default_factory=list)
    hidden_names: int = 0
    shows_paths: bool = False

    @property
    def totals(self) -> Counter:
        total: Counter = Counter()
        for finding in self.findings:
            total.update(finding.counts)
        return total

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def complete(self) -> bool:
        """Whether every file that could hold data was read. Images and the like don't count."""
        return not (self.too_large or self.unreadable or self.not_scanned_data)


def scan(
    target: str | Sequence[str],
    *,
    detectors: Sequence[Detector] | None = None,
    kinds: Iterable[str] | None = None,
    backend_kinds: Iterable[str] = (),
    excludes: Iterable[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
    include_examples: bool = False,
    show_paths: bool = False,
    policy: KeyPolicy | None = None,
) -> ScanReport:
    """Scan files and folders. `detectors` default to patterns plus the secret detector.

    `backend_kinds` are also reported when a model-based detector finds them, but not when a pattern
    does: a backend the user named is there for its SSNs, while pattern-matched dates are noise."""
    targets = [target] if isinstance(target, str) else list(target)
    if not targets or not all(os.path.exists(item) for item in targets):
        raise SourceError("cannot read input: no such file or folder")
    policy = policy or KeyPolicy()
    chosen = frozenset(kinds) if kinds is not None else DEFAULT_KINDS
    finders: list[Detector] = list(detectors) if detectors is not None else [PatternDetector(), SecretDetector()]
    skip = (*DEFAULT_EXCLUDES, *excludes)

    report = ScanReport(shows_paths=show_paths)
    files = [found for item in targets for found in _list_files(item, skip, many=len(targets) > 1)]
    for position, (path, relative) in enumerate(files, start=1):
        display = _display(relative, position, policy, show_paths)
        if display != relative:
            report.hidden_names += 1
        _scan_file(path, display, finders, chosen, frozenset(backend_kinds), max_bytes, include_examples, report)
    report.findings.sort(key=lambda item: (-item.total, item.display))
    return report


def _list_files(target: str, skip: Sequence[str], many: bool = False) -> list[tuple[str, str]]:
    if os.path.isfile(target):
        if _file_is_excluded(target, skip):
            return []
        # Several files can share a name, so a list keeps the path the caller gave (a pre-commit hook passes them).
        return [(target, target.replace(os.sep, "/") if many else os.path.basename(target))]
    found: list[tuple[str, str]] = []
    for folder, folders, names in os.walk(target, followlinks=False):
        folders[:] = sorted(name for name in folders if not _excluded(name, skip))
        for name in sorted(names):
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, target).replace(os.sep, "/")
            if not os.path.islink(path) and not _excluded(relative, skip) and not _excluded(name, skip):
                found.append((path, relative))
    return found


def _file_is_excluded(path: str, patterns: Sequence[str]) -> bool:
    """The rules a folder scan would apply to this file, as a pre-commit hook passes files one by one.

    Only the part below the working folder counts: where a checkout lives is no reason to skip it."""
    try:
        relative = os.path.relpath(path).replace(os.sep, "/")
    except ValueError:  # Another drive.
        relative = os.path.basename(path)
    parts = [os.path.basename(path)] if relative.startswith("../") else relative.split("/")
    return _excluded("/".join(parts), patterns) or any(_excluded(part, patterns) for part in parts)


def _excluded(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _display(relative: str, position: int, policy: KeyPolicy, show_paths: bool) -> str:
    if show_paths or all(policy.shows_name(part) for part in relative.split("/")):
        return relative
    return f"file_{position}"


def _scan_file(
    path: str,
    display: str,
    detectors: Sequence[Detector],
    kinds: frozenset[str],
    backend_kinds: frozenset[str],
    max_bytes: int,
    include_examples: bool,
    report: ScanReport,
) -> None:
    is_data = os.path.splitext(path)[1].lower() in _DATA_EXTENSIONS
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            if is_data:
                report.not_scanned_data.append(display)
            report.too_large += 1
            return
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError:
        report.unreadable += 1
        return
    text = _as_text(data)
    if text is None:
        report.binary += 1
        if is_data:
            report.not_scanned_data.append(display)
        return

    report.scanned += 1
    found = [
        span
        for detector in detectors
        for span in detector.find(text)
        if span.kind in kinds or (span.kind in backend_kinds and getattr(detector, "uses_model", False))
    ]
    spans = _resolve(found, text, include_examples)
    if not spans:
        return
    newlines = [match.start() for match in re.finditer("\n", text)]
    counts: Counter = Counter()
    lines: dict[str, list[int]] = {}
    for span in spans:
        counts[span.kind] += 1
        line = bisect_left(newlines, span.start) + 1
        listed = lines.setdefault(span.kind, [])
        if line not in listed and len(listed) < MAX_LINES_LISTED:
            listed.append(line)
    report.findings.append(FileFinding(path, display, dict(counts), lines))


def _as_text(data: bytes) -> str | None:
    """A file's text, or None if it is binary. Windows tools write UTF-16, which is full of NUL bytes."""
    if data.startswith((BOM_UTF32_LE, BOM_UTF32_BE)):  # Before UTF-16: one starts with the other's mark.
        return data.decode("utf-32", errors="replace")
    if data.startswith((BOM_UTF16_LE, BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    return None if b"\x00" in data[:8192] else data.decode("utf-8", errors="replace")


def _resolve(spans: list[Span], text: str, include_examples: bool) -> list[Span]:
    """One finding per stretch of text, with obvious non-findings removed."""
    keep: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
        if keep and span.start < keep[-1].end:
            continue
        if not include_examples and _is_benign(span, text):
            continue
        keep.append(span)
    return keep


_DATE_LIKE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ].*)?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}")
_DOTTED_NUMBERS = re.compile(r"\d+(?:\.\d+){2,}")
_DOTTED_PHONE = re.compile(r"\d{3}\.\d{3}\.\d{4}")


def _is_phone_like(value: str) -> bool:
    """The pattern for phones also matches dates, versions and IPs; a scan can't afford that."""
    if _DATE_LIKE.fullmatch(value) or (_DOTTED_NUMBERS.fullmatch(value) and not _DOTTED_PHONE.fullmatch(value)):
        return False
    return 8 <= sum(char.isdigit() for char in value) <= 15


def _is_benign(span: Span, text: str) -> bool:
    value = text[span.start : span.end]
    if span.kind == PHONE:
        return not _is_phone_like(value)
    if span.kind == EMAIL:
        return bool(_EXAMPLE_DOMAIN.search(value))
    if span.kind == IP_ADDRESS:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return address.is_loopback or address.is_unspecified or address.is_multicast or any(
            address in network for network in _DOCUMENTATION_NETWORKS if address.version == network.version
        )
    return False


# --- rendering -------------------------------------------------------------------------


def render_report(report: ScanReport, output_format: str = "text") -> str:
    if output_format == "json":
        return _json(report)
    return _text(report)


def _text(report: ScanReport) -> str:
    skipped = []
    if report.too_large:
        skipped.append(f"{report.too_large} too large")
    if report.binary:
        skipped.append(f"{report.binary} binary")
    if report.unreadable:
        skipped.append(f"{report.unreadable} unreadable")
    head = f"Scanned {report.scanned:,} file{'s' if report.scanned != 1 else ''}"
    lines = [head + (f" ({', '.join(skipped)} skipped)." if skipped else ".")]

    if report.clean and report.complete:
        lines.append("No personal data or secrets found by the patterns used.")
    elif report.clean:
        lines.append("Nothing found in the files that were read, but some weren't, so this doesn't show the folder is clean.")
    else:
        lines += ["", f"{len(report.findings):,} file{'s' if len(report.findings) != 1 else ''} with findings:"]
        width = min(max(len(item.display) for item in report.findings), 60)
        for item in report.findings:
            kinds = " · ".join(f"{kind} {count:,}" for kind, count in sorted(item.counts.items(), key=lambda pair: -pair[1]))
            lines.append(f"  {item.display.ljust(width)}  {kinds}")
            for kind in sorted(item.lines):
                if kind == SECRET or item.counts[kind] <= 3:
                    lines.append(f"  {' ' * width}  {kind} on line{'s' if len(item.lines[kind]) > 1 else ''} {_numbers(item.lines[kind])}")
        totals = " · ".join(f"{kind} {count:,}" for kind, count in report.totals.most_common())
        lines += ["", f"Total: {totals}"]

    if report.not_scanned_data:
        shown = ", ".join(report.not_scanned_data[:12]) + (" …" if len(report.not_scanned_data) > 12 else "")
        lines += ["", f"Not scanned, but these binary data files may hold personal data: {shown}"]
    if report.hidden_names:
        lines += [
            "",
            f"note: {report.hidden_names} file name{'s' if report.hidden_names != 1 else ''} looked like data, so "
            "they are shown as file_N. Use --show-paths to see real paths, and run it yourself rather than through an agent.",
        ]
    lines += ["", "Names of people, companies and places are only found with --ner."]
    return chr(10).join(lines)


def _numbers(values: list[int]) -> str:
    shown = ", ".join(str(value) for value in values[:6])
    return shown + (", …" if len(values) > 6 else "")


def _json(report: ScanReport) -> str:
    import json

    return json.dumps(
        {
            "scanned": report.scanned,
            "skipped": {"too_large": report.too_large, "binary": report.binary, "unreadable": report.unreadable},
            "complete": report.complete,
            "totals": dict(report.totals),
            "files": [{"path": item.display, "counts": item.counts, "lines": item.lines} for item in report.findings],
            "not_scanned_data_files": report.not_scanned_data,
            "hidden_file_names": report.hidden_names,
        },
        indent=2,
    )


__all__ = ["DEFAULT_KINDS", "NER_KINDS", "FileFinding", "ScanReport", "render_report", "scan"]

