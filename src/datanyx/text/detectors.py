"""Find sensitive spans in text: patterns for fixed formats, NER for everything else.

Detectors favour hiding, as the rest of DataNyx does. A false positive costs the
agent a word, while a miss exposes data.
"""

from __future__ import annotations

import ipaddress
import re
import warnings
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

from ..sources.base import SourceError

PERSON = "PERSON"
ORG = "ORG"
LOCATION = "LOCATION"
GROUP = "GROUP"
DATE = "DATE"
MONEY = "MONEY"
MISC = "MISC"
EMAIL = "EMAIL"
URL = "URL"
IP_ADDRESS = "IP_ADDRESS"
MAC_ADDRESS = "MAC_ADDRESS"
CREDIT_CARD = "CREDIT_CARD"
IBAN = "IBAN"
PHONE = "PHONE"
ADDRESS = "ADDRESS"
ID = "ID"
TERM = "TERM"

ENTITY_KINDS = (
    PERSON, ORG, LOCATION, GROUP, DATE, MONEY, MISC, EMAIL, URL, IP_ADDRESS,
    MAC_ADDRESS, CREDIT_CARD, IBAN, PHONE, ADDRESS, ID,
)

# Model labels differ by language and training data; these cover spaCy's standard
# models. Labels left out, such as CARDINAL or PERCENT, don't identify anyone.
_NER_KINDS = {
    "PERSON": PERSON,
    "PER": PERSON,
    "ORG": ORG,
    "GPE": LOCATION,
    "LOC": LOCATION,
    "FAC": LOCATION,
    "NORP": GROUP,
    "DATE": DATE,
    "TIME": DATE,
    "MONEY": MONEY,
    "MISC": MISC,
    "PRODUCT": MISC,
    "EVENT": MISC,
    "WORK_OF_ART": MISC,
    "LAW": MISC,
}

_NAME_KINDS = {PERSON, ORG, LOCATION}
_WORD = re.compile(r"[^\W\d_]+")

DEFAULT_MODEL = "en_core_web_lg"
# spaCy rejects longer texts, so input is split at line breaks where possible.
_CHUNK_CHARS = 100_000
_NON_NER_PIPES = ("parser", "tagger", "lemmatizer", "attribute_ruler", "morphologizer", "senter")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str


class Detector(Protocol):
    def find(self, text: str) -> Iterable[Span]:
        """Return the sensitive spans in `text`."""


def luhn_valid(number: str) -> bool:
    """The check digit of card numbers and others. Anything but digits is ignored."""
    digits = [int(char) for char in number if char.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit *= 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return total % 10 == 0


def _is_card(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    return 13 <= len(digits) <= 19 and luhn_valid(digits)


def _is_iban(match: str) -> bool:
    compact = re.sub(r"[\s-]", "", match).upper()
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def _is_ip(match: str) -> bool:
    try:
        ipaddress.ip_address(match)
    except ValueError:
        return False
    return True


def _is_phone(match: str) -> bool:
    # A bare run of digits is more likely a reference number, which ID hides anyway.
    has_punctuation = any(not char.isdigit() for char in match)
    return has_punctuation and sum(char.isdigit() for char in match) >= 7


def _looks_like_id(match: str) -> bool:
    """Reference numbers and keys: long digit runs, or letters mixed with digits."""
    digits = sum(char.isdigit() for char in match)
    letters = any(char.isalpha() for char in match)
    return digits >= 5 or (letters and digits >= 3)


_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec|"
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre|"
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|"
    "januar|februar|märz|maerz|juni|juli|oktober|dezember|"
    "gennaio|febbraio|aprile|maggio|giugno|luglio|settembre|ottobre|dicembre|"
    "janeiro|fevereiro|março|maio|junho|julho|setembro|outubro|novembro|dezembro"
)
_CURRENCY_CODES = "USD|EUR|GBP|JPY|CHF|CAD|AUD|CNY|INR"
_STREET_WORDS = (
    "street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|place|pl|"
    "square|sq|terrace|way|highway|hwy|parkway|pkwy"
)
_FOREIGN_STREET_WORDS = (
    "rue|avenue|boulevard|impasse|allée|allee|chemin|place|quai|cours|calle|"
    "strasse|straße|via|viale|rua"
)
_NAME_WORD = r"[^\W\d_][\w'’.-]*"
_CAPITALIZED_WORD = r"[A-ZÀ-Þ][\w'’-]*"
_STREET_NAME_LINK = r"(?:de|du|des|la|le|les|l['’]|d['’]|del|di|da|dos)"

# First match wins ties, so the most specific patterns come first.
_PATTERNS: tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...] = (
    # The bounds and the absence of a look-ahead on ID below are what keep these linear: unbounded, a
    # 100 KB line of "1 1 1 1" or "a-a-a-a" took minutes, because each start scanned the rest of the line.
    (EMAIL, re.compile(r"[\w.+%'-]{1,128}@[\w-]+(?:\.[\w-]+)+"), None),
    (URL, re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]{0,31}://|www\.)[^\s<>\"']*[^\s<>\"'.,;:!?)\]]"), None),
    (ID, re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"), None),
    (MAC_ADDRESS, re.compile(r"(?i)\b[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}\b"), None),
    (IP_ADDRESS, re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), _is_ip),
    (IP_ADDRESS, re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])"), _is_ip),
    (IBAN, re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{2,4}){3,8}\b"), _is_iban),
    (CREDIT_CARD, re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), _is_card),
    (DATE, re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"), None),
    (DATE, re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"), None),
    (DATE, re.compile(
        rf"(?i)\b\d{{1,2}}(?:st|nd|rd|th|er|º|\.)?\s+(?:de\s+)?(?:{_MONTHS})\b\.?(?:,?\s+(?:de\s+)?\d{{2,4}}\b)?"
    ), None),
    (DATE, re.compile(
        rf"(?i)\b(?:{_MONTHS})\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}}\b)?"
    ), None),
    (MONEY, re.compile(r"[$€£¥]\s?\d[\d,. \u00a0]*\d|[$€£¥]\s?\d"), None),
    (MONEY, re.compile(
        rf"\b\d[\d,. \u00a0]{{0,64}}\s?(?:[$€£¥]|(?:{_CURRENCY_CODES})\b)"
    ), None),
    (PHONE, re.compile(r"(?<![\w.])\+?\(?\d[\d \t().-]{5,}\d(?![\w])"), _is_phone),
    (ADDRESS, re.compile(
        rf"(?i)\b\d{{1,6}}[a-z]?,?\s+(?:{_NAME_WORD}\s+){{1,5}}?(?:{_STREET_WORDS})\b\.?"
    ), None),
    (ADDRESS, re.compile(
        rf"\b\d{{1,6}}(?:\s?(?i:bis|ter))?,?\s+(?i:{_FOREIGN_STREET_WORDS})\s+"
        rf"(?:{_STREET_NAME_LINK}\s*)*{_CAPITALIZED_WORD}"
        rf"(?:\s+(?:{_STREET_NAME_LINK}\s*)*{_CAPITALIZED_WORD})*"
    ), None),
    (ID, re.compile(r"\b[^\W_]+(?:[-_][^\W_]+)*\b"), _looks_like_id),
)


class PatternDetector:
    """Formats a pattern can recognise reliably, needing no model."""

    def find(self, text: str) -> Iterator[Span]:
        for kind, pattern, is_valid in _PATTERNS:
            for match in pattern.finditer(text):
                if is_valid is None or is_valid(match.group()):
                    end = match.start() + len(match.group().rstrip())
                    yield Span(match.start(), end, kind)


class SpacyDetector:
    """Names, organisations, places and dates, found by a spaCy NER model."""

    uses_model = True

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._nlp = _load_model(model)

    def find(self, text: str) -> Iterator[Span]:
        yield from self._entities(text)
        # NER models lean heavily on capitals, so names in ALL CAPS or all-lowercase
        # text are often missed. Rereading a same-length recased copy finds many of them.
        recased = recase_for_ner(text)
        if recased != text:
            for span in self._entities(recased):
                if span.kind in _NAME_KINDS:
                    yield span

    def _entities(self, text: str) -> Iterator[Span]:
        chunks = list(_split_chunks(text))
        with warnings.catch_warnings():
            # spaCy's warnings go to stderr and can quote the text being processed.
            warnings.simplefilter("ignore")
            documents = self._nlp.pipe(chunk for _, chunk in chunks)
            for (offset, _), document in zip(chunks, documents):
                for entity in document.ents:
                    kind = _NER_KINDS.get(entity.label_)
                    if kind is not None:
                        yield Span(offset + entity.start_char, offset + entity.end_char, kind)


def recase_for_ner(text: str) -> str:
    """Title-case ALL CAPS words and all-lowercase lines, keeping every offset the same."""
    return "".join(_recase_line(line) for line in text.splitlines(keepends=True))


def _recase_line(line: str) -> str:
    all_lowercase = line.lower() == line and line.upper() != line
    return _WORD.sub(lambda match: _recase_word(match.group(), all_lowercase), line)


def _recase_word(word: str, all_lowercase: bool) -> str:
    if not all_lowercase and (word.upper() != word or len(word) < 2):
        return word
    recased = word[0].upper() + word[1:].lower()
    # A few characters change length when recased, such as "ß", which would shift offsets.
    return recased if len(recased) == len(word) else word


def _load_model(model: str):
    try:
        import spacy
    except ImportError:
        raise SourceError('text anonymization needs spaCy; run: pip install "datanyx[text]"') from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return spacy.load(model, exclude=list(_NON_NER_PIPES))
    except OSError:
        raise SourceError(f"NER model not found; run: python -m spacy download {model}") from None


def _split_chunks(text: str) -> Iterator[tuple[int, str]]:
    """Split text into (offset, chunk), avoiding breaks in the middle of a name."""
    start = 0
    while start < len(text):
        end = min(start + _CHUNK_CHARS, len(text))
        if end < len(text):
            newline = text.rfind("\n", start + 1, end)
            space = text.rfind(" ", start + 1, end)
            end = (newline if newline != -1 else space if space != -1 else end - 1) + 1
        yield start, text[start:end]
        start = end
