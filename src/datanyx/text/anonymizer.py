"""Replace sensitive spans in text with consistent placeholders.

Unlike a schema, this output is derived from the data itself, so the safety of the
result depends on detection, and detection isn't perfect. Two steps here improve on
the raw detectors: a name found once is hidden everywhere it occurs, and overlapping
spans are merged, so no part of a detected span can survive.
"""

from __future__ import annotations

import re
from typing import Iterable

from .detectors import PERSON, TERM, Detector, Span

_TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam", "jr", "sr", "m", "mme", "mlle"}
# Name particles are ordinary words elsewhere: "de" must not be hidden everywhere
# because of "Charles de Gaulle".
_PARTICLES = {
    "de", "du", "des", "la", "le", "les", "van", "von", "der", "den", "di", "da", "dos",
    "del", "el", "al", "bin", "ibn", "the",
}


_PLACEHOLDER = re.compile(r"<[A-Z_]+_\d+>")


def anonymize(
    text: str,
    detectors: Iterable[Detector],
    hide_terms: Iterable[str] = (),
    keep_terms: Iterable[str] = (),
    keep_kinds: Iterable[str] = (),
) -> str:
    """Return `text` with sensitive spans replaced by placeholders like <PERSON_1>.

    hide_terms: strings to always hide, ignoring case.
    keep_terms: exact strings to leave visible, even if detected.
    keep_kinds: entity kinds to leave visible, such as "DATE".
    """
    return anonymize_with_mapping(text, detectors, hide_terms, keep_terms, keep_kinds)[0]


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put real values back in place of placeholders; unknown placeholders stay as they are."""
    return _PLACEHOLDER.sub(lambda match: mapping.get(match.group(), match.group()), text)


def unknown_placeholders(text: str, mapping: dict[str, str]) -> list[str]:
    """Placeholders in `text` with no value in `mapping`, such as ones an agent invented."""
    return sorted(set(_PLACEHOLDER.findall(text)) - set(mapping))


def anonymize_with_mapping(
    text: str,
    detectors: Iterable[Detector],
    hide_terms: Iterable[str] = (),
    keep_terms: Iterable[str] = (),
    keep_kinds: Iterable[str] = (),
) -> tuple[str, dict[str, str]]:
    """Like `anonymize`, also returning each placeholder's real value.

    The mapping holds the very data being hidden, so it must stay out of an agent's reach.
    A placeholder used for several forms of a name maps to the longest, so "Ms. <PERSON_1>"
    restores as "Ms. Jessica Whitfield" even if the original said "Ms. Whitfield".
    """
    keep_terms = set(keep_terms)
    keep_kinds = set(keep_kinds)
    detected = [span for detector in detectors for span in detector.find(text)]
    kept = [
        span
        for span in detected
        if span.kind in keep_kinds or text[span.start : span.end] in keep_terms
    ]
    # Also drop what's inside a kept span: "2024-01-02" matches the ID pattern too, and
    # that shouldn't hide a date the user asked to keep.
    spans = [
        span
        for span in detected
        if not any(k.start <= span.start and span.end <= k.end for k in kept)
    ]
    spans.extend(_literal_spans(text, hide_terms))
    spans.extend(_other_occurrences(text, spans, keep_terms))
    return _replace(text, _merge(spans))


def _literal_spans(text: str, terms: Iterable[str]) -> Iterable[Span]:
    for term in terms:
        if term.strip():
            for match in re.finditer(_term_pattern(term), text, re.IGNORECASE):
                yield Span(match.start(), match.end(), TERM)


def _term_pattern(term: str) -> str:
    """Match `term` as whole words, allowing any whitespace between them."""
    words = (re.escape(word) for word in term.split())
    return r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)"


def _other_occurrences(text: str, spans: list[Span], keep_terms: set[str]) -> list[Span]:
    """Find each detected name wherever else it occurs, including parts of a person's name.

    NER judges each mention separately, so it often finds "Jane Doe" once and misses
    her later, or misses a lone "Doe".
    """
    found: list[Span] = []
    seen: set[tuple[str, str]] = set()
    for span in spans:
        surface = text[span.start : span.end]
        key = (span.kind, surface)
        if key in seen:
            continue
        seen.add(key)
        found.extend(_find_surface(text, surface, span.kind))
        if span.kind == PERSON:
            for part in _name_parts(surface):
                if part not in keep_terms:
                    found.extend(_find_surface(text, part, PERSON))
    return found


def _find_surface(text: str, surface: str, kind: str) -> Iterable[Span]:
    if not surface.strip():
        return []
    # Multi-word names rarely collide with ordinary words, so case can be ignored.
    # "May", "Bill" and "Apple" can, so a single word only matches as written or in
    # capitals.
    if len(surface.split()) > 1:
        return [Span(m.start(), m.end(), kind) for m in re.finditer(_term_pattern(surface), text, re.IGNORECASE)]
    spans = [Span(m.start(), m.end(), kind) for m in re.finditer(_term_pattern(surface), text)]
    if surface.upper() != surface:
        pattern = _term_pattern(surface.upper())
        spans.extend(Span(m.start(), m.end(), kind) for m in re.finditer(pattern, text))
    return spans


def _name_parts(surface: str) -> list[str]:
    parts = []
    for word in surface.split():
        word = re.sub(r"['’]s$", "", word).strip(".,;:'’\"()")
        if len(word) >= 2 and word.lower() not in _TITLES and word.lower() not in _PARTICLES:
            parts.append(word)
    return parts if len(parts) > 1 else []


def _merge(spans: list[Span]) -> list[Span]:
    """Union overlapping spans, so a partial overlap can't leave a fragment visible.

    The longest span names the result. Among equals a person wins, because an NER model
    can call a lone "Bell" an organisation though "Mark Bell" was found elsewhere, and
    then the earliest found does.
    """
    ordered = sorted(spans, key=lambda span: (span.start, span.kind != PERSON))
    merged: list[Span] = []
    best_length = 0
    for span in ordered:
        length = span.end - span.start
        if merged and span.start < merged[-1].end:
            last = merged[-1]
            kind = span.kind if length > best_length else last.kind
            best_length = max(best_length, length)
            merged[-1] = Span(last.start, max(last.end, span.end), kind)
        else:
            merged.append(span)
            best_length = length
    return merged


def _replace(text: str, spans: list[Span]) -> tuple[str, dict[str, str]]:
    identities = _person_identities(text, spans)
    placeholders: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    mapping: dict[str, str] = {}
    output: list[str] = []
    position = 0
    for span in spans:
        surface = _normalize(text[span.start : span.end])
        key = (span.kind, identities.get(surface, surface) if span.kind == PERSON else surface)
        if key not in placeholders:
            placeholders[key] = _next_placeholder(span.kind, counters, text)
        placeholder = placeholders[key]
        value = " ".join(text[span.start : span.end].split())
        if len(value) > len(mapping.get(placeholder, "")):
            mapping[placeholder] = value
        output.append(text[position : span.start])
        output.append(placeholder)
        position = span.end
    output.append(text[position:])
    return "".join(output), mapping


def _next_placeholder(kind: str, counters: dict[str, int], text: str) -> str:
    # Skip any number the text already uses literally, or restoring would replace it.
    while True:
        counters[kind] = counters.get(kind, 0) + 1
        placeholder = f"<{kind}_{counters[kind]}>"
        if placeholder not in text:
            return placeholder


def _person_identities(text: str, spans: list[Span]) -> dict[str, str]:
    """Map a lone "Doe" to "jane doe", unless it could also be "John Doe"."""
    surfaces = {text[span.start : span.end] for span in spans if span.kind == PERSON}
    owners: dict[str, set[str]] = {}
    for surface in surfaces:
        for part in _name_parts(surface):
            owners.setdefault(_normalize(part), set()).add(_normalize(surface))
    return {part: names.pop() for part, names in owners.items() if len(names) == 1}


def _normalize(surface: str) -> str:
    return " ".join(surface.split()).casefold()


__all__ = ["anonymize", "anonymize_with_mapping", "restore", "unknown_placeholders"]
