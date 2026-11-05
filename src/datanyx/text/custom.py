"""Detectors you describe in a file, for formats and names that are yours alone.

A detector file (or the `[[pattern]]` and `[[dictionary]]` entries of `datanyx.toml`):

    [[pattern]]
    kind = "EMPLOYEE_ID"          # becomes the placeholder <EMPLOYEE_ID_1>
    regex = 'EMP-\\d{6}'
    flags = "i"                   # optional: any of i, m, s
    group = 1                     # optional: mark only this part of the match

    [[dictionary]]
    kind = "CLIENT"
    terms = ["Acme", "Northwind"]
    terms_file = "clients.txt"    # optional: one term per line, # for comments
    ignore_case = true            # the default
    word_boundaries = true        # the default: "Acme" doesn't match inside "Acmeville"

Kinds are capital letters and underscores. Terms may be sensitive, so errors never repeat
them, and a dictionary belongs in a file that isn't committed if its terms shouldn't be.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..sources.base import SourceError
from .detectors import Detector, Span

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

_KIND = re.compile(r"[A-Z][A-Z_]*")
_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
_PATTERN_KEYS = {"kind", "regex", "flags", "group"}
_DICTIONARY_KEYS = {"kind", "terms", "terms_file", "ignore_case", "word_boundaries"}


class RegexDetector:
    """Everything a regular expression matches."""

    def __init__(self, kind: str, pattern: re.Pattern[str], group: int = 0) -> None:
        self.kind = kind
        self._pattern = pattern
        self._group = group

    def find(self, text: str) -> Iterator[Span]:
        for match in self._pattern.finditer(text):
            start, end = match.span(self._group)
            if start >= 0 and end > start:
                yield Span(start, end, self.kind)


class DictionaryDetector:
    """A list of terms, matched as whole words by default."""

    def __init__(self, kind: str, terms: Sequence[str], ignore_case: bool = True, word_boundaries: bool = True) -> None:
        self.kind = kind
        # Longest first, so "Acme Labs" is found before "Acme".
        ordered = sorted({term for term in terms if term.strip()}, key=len, reverse=True)
        alternatives = "|".join(re.escape(term) for term in ordered)
        body = f"(?<!\\w)(?:{alternatives})(?!\\w)" if word_boundaries else f"(?:{alternatives})"
        self._pattern = re.compile(body, re.IGNORECASE if ignore_case else 0) if ordered else None

    def find(self, text: str) -> Iterator[Span]:
        if self._pattern is not None:
            for match in self._pattern.finditer(text):
                yield Span(match.start(), match.end(), self.kind)


def load_detector_file(path: Path) -> list[Detector]:
    """The detectors in one file, which holds only `[[pattern]]` and `[[dictionary]]` entries."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise SourceError(f"cannot read the detector file {path.name}") from None
    except UnicodeDecodeError:
        raise SourceError(f"the detector file {path.name} isn't valid UTF-8") from None
    except tomllib.TOMLDecodeError as error:
        raise SourceError(f"the detector file {path.name} isn't valid TOML: {error}") from None
    unknown = set(raw) - {"pattern", "dictionary"}
    if unknown:
        raise SourceError(f"{path.name}: only [[pattern]] and [[dictionary]] belong in a detector file, not [{sorted(unknown)[0]}]")
    entries = [{**item, "_type": name, "_dir": str(path.parent)} for name in ("pattern", "dictionary") for item in raw.get(name, [])]
    return build_detectors(entries, path.name)


def build_detectors(entries: Sequence[Mapping[str, Any]], origin: str = "datanyx.toml") -> list[Detector]:
    detectors: list[Detector] = []
    for entry in entries:
        kind = _kind(entry, origin)
        if entry["_type"] == "pattern":
            detectors.append(_pattern(entry, kind, origin))
        else:
            detectors.append(_dictionary(entry, kind, origin))
    return detectors


def kinds_of(detectors: Sequence[Detector]) -> set[str]:
    return {detector.kind for detector in detectors if isinstance(detector, (RegexDetector, DictionaryDetector))}


def _kind(entry: Mapping[str, Any], origin: str) -> str:
    kind = entry.get("kind")
    if not isinstance(kind, str) or not _KIND.fullmatch(kind):
        raise SourceError(f"{origin}: every detector needs a kind of capital letters and underscores, such as EMPLOYEE_ID")
    return kind


def _check_keys(entry: Mapping[str, Any], allowed: set[str], origin: str, kind: str) -> None:
    unknown = sorted(key for key in entry if not key.startswith("_") and key not in allowed)
    if unknown:
        raise SourceError(f"{origin}: unknown setting '{unknown[0]}' for {kind}; the choices are {', '.join(sorted(allowed))}")


def _pattern(entry: Mapping[str, Any], kind: str, origin: str) -> RegexDetector:
    _check_keys(entry, _PATTERN_KEYS, origin, kind)
    regex, flags, group = entry.get("regex"), entry.get("flags", ""), entry.get("group", 0)
    if not isinstance(regex, str) or not regex:
        raise SourceError(f"{origin}: the pattern for {kind} needs a regex")
    if not isinstance(flags, str) or set(flags) - set(_FLAGS):
        raise SourceError(f"{origin}: flags for {kind} can only be i, m and s")
    if isinstance(group, bool) or not isinstance(group, int) or group < 0:
        raise SourceError(f"{origin}: group for {kind} must be a number from 0")
    compiled_flags = 0
    for flag in flags:
        compiled_flags |= _FLAGS[flag]
    try:
        compiled = re.compile(regex, compiled_flags)
    except re.error as error:
        raise SourceError(f"{origin}: the regex for {kind} is invalid ({error.msg})") from None
    if group > compiled.groups:
        raise SourceError(f"{origin}: the regex for {kind} has no group {group}")
    return RegexDetector(kind, compiled, group)


def _dictionary(entry: Mapping[str, Any], kind: str, origin: str) -> DictionaryDetector:
    _check_keys(entry, _DICTIONARY_KEYS, origin, kind)
    terms = entry.get("terms", [])
    if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
        raise SourceError(f"{origin}: terms for {kind} must be a list of text")
    terms = list(terms)
    terms_file = entry.get("terms_file")
    if terms_file is not None:
        terms.extend(_read_terms(entry, str(terms_file), origin, kind))
    if not terms:
        raise SourceError(f"{origin}: the dictionary for {kind} has no terms")
    for option in ("ignore_case", "word_boundaries"):
        if not isinstance(entry.get(option, True), bool):
            raise SourceError(f"{origin}: {option} for {kind} must be true or false")
    return DictionaryDetector(kind, terms, entry.get("ignore_case", True), entry.get("word_boundaries", True))


def _read_terms(entry: Mapping[str, Any], name: str, origin: str, kind: str) -> list[str]:
    base = Path(entry.get("_dir") or ".")
    path = Path(name) if Path(name).is_absolute() else base / name
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise SourceError(f"{origin}: cannot read the terms file for {kind}") from None
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
