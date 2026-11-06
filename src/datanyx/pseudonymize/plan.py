"""Decide what each column or key holds, and so how its values should be replaced.

The decision is shown before anything is written, because it is a judgement: a column called
`notes` is almost certainly free text and one called `customer_email` almost certainly emails, but
a column called `region` might be harmless or might not. Whatever is recognised is replaced;
anything not recognised is kept and listed, so you can look at it and add `--column NAME=KIND`.

In order, a column's kind comes from: your override, `--keep-column`, being a key in a database,
its name, and what its values look like.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..formats import detect_format
from ..sources.base import SourceError
from ..text import PatternDetector
from ..text.detectors import CREDIT_CARD, IBAN
from .fakes import KINDS

_SPLIT = re.compile(r"[a-z]+")
_CAMEL = re.compile(r"([a-z])([A-Z])")
# A phone number has a separator or a leading +; a bare run of digits is a number, not a phone.
_PHONE_VALUE = re.compile(r"(?=.*[+()\s.-])\+?\(?\d[\d\s().-]{6,}\d")
_DATE_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?")
_SAMPLE = 500
_PERSON_WORDS = frozenset(
    {"customer", "client", "contact", "author", "owner", "employee", "patient", "person", "fullname", "recipient", "sender"}
)


@dataclass(frozen=True)
class Treatment:
    kind: str
    reason: str
    #: True for a column of text that was kept, which is worth a look.
    review: bool = False


_WEAK = "the name says a person"


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", ""))
    except ValueError:
        return False
    return True


def column_words(name: str) -> list[str]:
    """`customerEmail` and `customer_email` both give ['customer', 'email']."""
    return _SPLIT.findall(_CAMEL.sub(r"\1 \2", name).lower())


def _by_name(words: list[str]) -> tuple[str, str] | None:
    w = set(words)
    if w & {"password", "passwd", "secret", "token", "apikey", "credential", "credentials"} or {"api", "key"} <= w:
        return "token", "the name says it is a secret"
    if words and words[-1] in {"id", "uuid", "guid"}:
        return "id", "the name ends in id"
    if w & {"email", "mail"}:
        return "email", "the name says email"
    if ("name" in w and w & {"first", "given"}) or "forename" in w:
        return "first_name", "the name says first name"
    if ("name" in w and w & {"last", "family"}) or "surname" in w:
        return "last_name", "the name says last name"
    if w & {"phone", "tel", "telephone", "mobile", "fax", "cell"}:
        return "phone", "the name says phone"
    if w & {"zip", "postal", "postcode"}:
        return "postal", "the name says postal code"
    if "ip" in w or "ipaddress" in w:
        return "ip", "the name says IP address"
    if w & {"address", "street"}:
        return "address", "the name says address"
    if w & {"city", "town"}:
        return "city", "the name says city"
    if w & {"company", "employer", "organization", "organisation", "org"}:
        return "company", "the name says company"
    if w & {"url", "website", "link", "uri", "homepage"}:
        return "url", "the name says URL"
    if w & {"dob", "birth", "birthday", "birthdate"}:
        return "date", "the name says birth date"
    if w & {"ssn", "passport", "iban", "card", "account", "tax", "national", "licence", "license"}:
        return "id", "the name says an official number"
    if w & {"username", "handle"} or w == {"login"} or ("user" in w and "name" in w):
        return "id", "the name says a login"
    if "name" in w and not w & {"file", "table", "column", "host", "path", "domain", "type", "key", "product"}:
        return "name", "the name says name"
    if w and w <= _PERSON_WORDS | {"name"}:
        return "name", _WEAK
    if w & {"notes", "note", "comment", "comments", "description", "message", "body", "text", "summary", "remarks", "bio", "content", "feedback"}:
        return "text", "the name says free text"
    return None


def _by_values(values: list[str]) -> tuple[str, str] | None:
    if not values:
        return None
    formats = {detect_format(value) for value in values[:_SAMPLE]}
    if all(_DATE_VALUE.fullmatch(value.strip()) for value in values[:_SAMPLE]):
        return "keep", "dates are kept, except birth dates"
    if formats == {"email"}:
        return "email", "every value is an email"
    if formats == {"uuid"}:
        return "id", "every value is a UUID"
    if formats <= {"ipv4", "ipv6"} and formats:
        return "ip", "every value is an IP address"
    if formats == {"uri"}:
        return "url", "every value is a URL"
    sample = values[:_SAMPLE]
    if sum(bool(_PHONE_VALUE.fullmatch(v.strip())) for v in sample) >= 0.8 * len(sample):
        return "phone", "the values look like phone numbers"
    finder = PatternDetector()
    whole = 0
    for value in sample:
        text = value.strip()
        if any(s.kind in (IBAN, CREDIT_CARD) and s.start == 0 and s.end == len(text) for s in finder.find(text)):
            whole += 1
    if whole >= 0.8 * len(sample):
        return "id", "the values look like account or card numbers"
    if sum(len(v) for v in sample) / len(sample) >= 40 and sum(" " in v for v in sample) >= 0.6 * len(sample):
        return "text", "the values are long free text"
    return None


# What a name can promise about a column whose values are numbers: 14155552671 is still a phone.
_NUMERIC_KINDS = frozenset({"id", "date", "phone", "postal", "token"})


def classify(name: str, values: Iterable[Any], is_key: bool = False) -> Treatment:
    """What one column holds. A name that says text-only data doesn't fit numbers, so those are kept and flagged."""
    sample = list(values)
    has_binary = any(isinstance(v, (bytes, memoryview)) for v in sample)
    present = [v for v in sample if v is not None and v != ""]
    strings = [v for v in sample if isinstance(v, str) and v.strip()]
    # Text worth a second look: not a number or a date written as text.
    texty = [v for v in strings if not _is_number(v) and not _DATE_VALUE.fullmatch(v.strip())]
    only_text = len(strings) == len([v for v in sample if v is not None and v != ""])
    guess = _by_name(column_words(name))
    if guess is not None and guess[1] == _WEAK and strings and only_text:
        # A name like "contact" is a weak hint: what the values are is stronger.
        better = _by_values(strings)
        if better is not None and better[0] != "keep":
            guess = better
    if guess is None and strings and only_text:
        guess = _by_values(strings)
    if guess is None:
        if is_key:
            return Treatment("id", "it is a key, so joins must keep working")
        return Treatment("keep", "nothing personal was recognised", review=bool(texty) or has_binary)
    kind, reason = guess
    if kind == "keep":
        return Treatment("keep", reason)
    # With nothing to look at, the name is all there is: the column may fill up after the sample.
    if is_key and present and not strings and kind not in _NUMERIC_KINDS:
        # A numeric key or foreign key is permuted whatever its name says, or its joins would break.
        return Treatment("id", "it is a key, so joins must keep working")
    if present and not strings and kind not in _NUMERIC_KINDS:
        return Treatment("keep", "the name suggests personal data, but the values aren't text", review=True)
    if is_key and kind in ("text",):
        return Treatment("id", "it is a key, so joins must keep working")
    return Treatment(kind, reason)


def build_plan(
    columns: Mapping[str, list[Any]],
    keys: frozenset[str] = frozenset(),
    overrides: Mapping[str, str] | None = None,
    keep: frozenset[str] = frozenset(),
    strict: bool = False,
) -> dict[str, Treatment]:
    """A treatment for every column. `keys` are the columns that must stay joinable."""
    overrides = {name.lower(): kind for name, kind in (overrides or {}).items()}
    kept = {name.lower() for name in keep}
    plan: dict[str, Treatment] = {}
    for name, values in columns.items():
        low = name.lower()
        if low in overrides:
            plan[name] = Treatment(overrides[low], "set with --column")
        elif low in kept:
            plan[name] = Treatment("keep", "kept at your request")
        else:
            treatment = classify(name, values, name in keys)
            if strict and treatment.kind == "keep" and treatment.review:
                treatment = Treatment("category", "--strict: text that wasn't recognised is replaced")
            plan[name] = treatment
    return plan


def parse_overrides(items: Iterable[str]) -> dict[str, str]:
    """`name=kind` pairs from the command line."""
    result: dict[str, str] = {}
    for item in items:
        name, separator, kind = item.partition("=")
        if not separator or not name.strip():
            raise SourceError(f"--column needs NAME=KIND, not {item!r}")
        if kind not in KINDS:
            raise SourceError(f"unknown kind {kind!r} for {name}; the choices are {', '.join(KINDS)}")
        result[name.strip()] = kind
    return result


def render_plan(title: str, plan: Mapping[str, Treatment]) -> str:
    width = min(max((len(name) for name in plan), default=0), 40)
    lines = [f"{title}:"]
    for name, treatment in plan.items():
        flag = "   <- kept: check it" if treatment.review else ""
        lines.append(f"  {name[:40].ljust(width)}  {treatment.kind.ljust(10)}  {treatment.reason}{flag}")
    return chr(10).join(lines)
