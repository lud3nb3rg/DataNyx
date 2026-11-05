"""Consistent fake values: the same real value always becomes the same fake one.

Everything is derived from a secret key with HMAC, so the fake for a value is the same in every
column, table and file that holds it (joins keep working) and, with the same key, in every run.
Without the key nobody can work out which real value a fake came from, or test a guess.

- Names, emails, companies, cities and addresses come from word lists and look plausible. Two
  different real names never get the same fake: a clash is re-drawn.
- Whole-number ids go through a keyed permutation of their own range, so the result has the same
  number of digits and two ids can never become one. This is what keeps foreign keys valid.
- Other codes (phone numbers, postal codes, account numbers) keep their shape: a digit becomes a
  digit, a letter a letter, punctuation stays.
- Dates move by one secret number of days, so gaps between them survive.
- Free text goes through the text anonymizer.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from ..text.anonymizer import Anonymizer

FIRST_NAMES = ["Ana", "Omar", "Li", "Sofia", "Noah", "Amara", "Jonas", "Mei", "Lucas", "Priya", "Elena", "Tomas", "Hana", "Marco", "Ines", "Kofi", "Yara", "Pavel", "Nadia", "Ravi", "Clara", "Idris", "Mila", "Hugo", "Zoe", "Arjun", "Freya", "Diego", "Aiko", "Leon", "Nora", "Samir", "Vera", "Felix", "Lena", "Tariq", "Alma", "Bruno", "Dina", "Emil", "Greta", "Haruto", "Isla", "Jamal", "Kira", "Luca", "Maya", "Nico", "Olga", "Pia", "Quinn", "Rosa", "Sven", "Tess", "Uma", "Vik", "Wren", "Xavi", "Yuki", "Zane"]
LAST_NAMES = ["Silva", "Haddad", "Chen", "Rossi", "Okafor", "Novak", "Larsen", "Tanaka", "Moreau", "Singh", "Duarte", "Kowal", "Berg", "Costa", "Dubois", "Eze", "Fischer", "Garcia", "Horvat", "Ivanov", "Jensen", "Khan", "Lopez", "Meyer", "Nakamura", "Olsen", "Petrov", "Quinn", "Reyes", "Santos", "Tran", "Ueda", "Vogel", "Weber", "Yilmaz", "Zhang", "Adler", "Brandt", "Castro", "Dalton", "Ellis", "Ford", "Grant", "Hale", "Ito", "Joshi", "Klein", "Lund", "Mori", "Nash", "Ortiz", "Pike"]
CITIES = ["Lisbon", "Osaka", "Leeds", "Austin", "Porto", "Lyon", "Turin", "Quito", "Bergen", "Kyoto", "Cork", "Gdansk", "Malmo", "Brno", "Tartu", "Split", "Bilbao", "Ghent", "Aarhus", "Utrecht", "Graz", "Salerno", "Vigo", "Cadiz", "Tampere", "Oulu", "Riga", "Kaunas", "Plovdiv", "Varna", "Cluj", "Bari"]
COMPANIES = ["Acme Labs", "Northwind Trading", "Globex", "Initech Works", "Umbra Foods", "Blue Heron Co", "Cobalt Systems", "Delta Freight", "Ember Analytics", "Fjord Logistics", "Garnet Health", "Harbor Mills", "Indigo Retail", "Juniper Energy", "Kestrel Media", "Lantern Partners", "Meadow Farms", "Nectar Foods", "Onyx Capital", "Pebble Studio", "Quartz Labs", "Ripple Payments"]
STREETS = ["Maple Street", "Harbor Road", "Station Avenue", "Orchard Lane", "Mill Court", "Cedar Way", "Elm Street", "Park Road", "Bridge Street", "Hill Avenue", "Lake Drive", "River Road", "Church Lane", "Garden Close", "Market Square", "Forest Way"]
WORDS = ["amber", "basil", "cobalt", "delta", "ember", "fjord", "garnet", "harbor", "indigo", "juniper", "kestrel", "lantern", "meadow", "nectar", "onyx", "pebble", "quartz", "ripple", "saffron", "timber", "umber", "velvet", "willow", "yarrow", "zephyr"]

_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(.*)$", re.DOTALL)
KINDS = (
    "name", "first_name", "last_name", "email", "phone", "address", "city", "company", "postal", "id", "token",
    "date", "text", "ip", "url", "category", "keep", "drop",
)


class Pseudonymizer:
    """Turns real values into consistent fake ones. `kinds_replaced` counts what was changed."""

    def __init__(self, key: bytes, text: Anonymizer | Callable[[], Anonymizer] | None = None, record: bool = False) -> None:
        if len(key) < 16:
            raise ValueError("the key is too short")
        self._key = key
        self._text_source = text
        self._text_anonymizer: Anonymizer | None = text if isinstance(text, Anonymizer) else None
        self._record = record
        self._cache: dict[tuple[str, str], str] = {}
        self._taken: dict[str, set[str]] = {}
        self.pairs: dict[str, dict[str, str]] = {}
        self.counts: dict[str, int] = {}
        self.unparsed: dict[str, int] = {}
        digest = hmac.new(key, b"date-offset", hashlib.sha256).digest()
        offset = int.from_bytes(digest[:4], "big") % 730 - 365
        self._days = offset or 97

    # --- the entry point -----------------------------------------------------------------------

    def apply(self, kind: str, value: Any) -> Any:
        if value is None or value == "" or kind == "keep":
            return value
        if kind == "drop":
            return None
        if isinstance(value, bool):
            return value
        handler: Callable[[Any], Any] = getattr(self, "_" + kind)
        result = handler(value)
        if result != value:
            self.counts[kind] = self.counts.get(kind, 0) + 1
        return result

    # --- derivation -----------------------------------------------------------------------------

    def _digest(self, kind: str, normalized: str, attempt: int = 0) -> bytes:
        message = f"{kind}\0{normalized}\0{attempt}".encode()
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def _random(self, kind: str, normalized: str, attempt: int = 0) -> random.Random:
        return random.Random(int.from_bytes(self._digest(kind, normalized, attempt), "big"))

    def _unique(self, kind: str, original: Any, make: Callable[[random.Random], str], normalize: bool = True) -> str:
        text = str(original)
        normalized = " ".join(text.split()).casefold() if normalize else text
        cached = self._cache.get((kind, normalized))
        if cached is not None:
            return cached
        taken = self._taken.setdefault(kind, set())
        attempt = 0
        while True:
            fake = make(self._random(kind, normalized, attempt))
            if fake not in taken:
                break
            attempt += 1
        taken.add(fake)
        self._cache[(kind, normalized)] = fake
        if self._record:
            self.pairs.setdefault(kind, {}).setdefault(fake, text)
        return fake

    # --- kinds -----------------------------------------------------------------------------------

    def _name(self, value: Any) -> str:
        return self._unique("name", value, lambda rng: f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}")

    def _first_name(self, value: Any) -> str:
        return self._random("first_name", str(value).strip().casefold()).choice(FIRST_NAMES)

    def _last_name(self, value: Any) -> str:
        return self._random("last_name", str(value).strip().casefold()).choice(LAST_NAMES)

    def _email(self, value: Any) -> str:
        def make(rng: random.Random) -> str:
            return f"{rng.choice(FIRST_NAMES).lower()}.{rng.choice(LAST_NAMES).lower()}{rng.randint(10, 99)}@example.com"

        return self._unique("email", value, make)

    def _city(self, value: Any) -> str:
        return self._random("city", str(value).strip().casefold()).choice(CITIES)

    def _company(self, value: Any) -> str:
        return self._random("company", str(value).strip().casefold()).choice(COMPANIES)

    def _address(self, value: Any) -> str:
        rng = self._random("address", str(value).strip().casefold())
        return f"{rng.randint(1, 250)} {rng.choice(STREETS)}"

    def _category(self, value: Any) -> str:
        return self._unique("category", value, lambda rng: f"{rng.choice(WORDS)}-{rng.randint(100, 999)}")

    def _phone(self, value: Any) -> str:
        text = str(value)
        keep = 0
        if text.lstrip().startswith("+"):
            match = re.match(r"\s*\+\d+", text)
            keep = match.end() if match else 0  # The country code stays.
        return _shape(text, self._random("phone", re.sub(r"\D", "", text)), keep)

    def _postal(self, value: Any) -> str:
        return _shape(str(value), self._random("postal", str(value).strip().casefold()))

    def _token(self, value: Any) -> str:
        text = str(value)
        digest = self._digest("token", text).hex()
        return ("tok_" + digest * 4)[: max(len(text), 8)]

    def _ip(self, value: Any) -> str:
        text = str(value)
        rng = self._random("ip", text.strip())
        if ":" in text:
            return f"2001:db8::{rng.randint(1, 0xFFFF):x}:{rng.randint(1, 0xFFFF):x}"
        return f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"

    def _url(self, value: Any) -> str:
        text = str(value)
        scheme = text.split("://", 1)[0] if "://" in text else "https"
        return f"{scheme}://example.com/{self._digest('url', text).hex()[:10]}"

    def _text(self, value: Any) -> str:
        if self._text_anonymizer is None:
            if self._text_source is None or isinstance(self._text_source, Anonymizer):
                raise ValueError("free text needs a text anonymizer")
            self._text_anonymizer = self._text_source()  # Built now: loading a language model is slow.
        return self._text_anonymizer.anonymize(str(value))

    def _date(self, value: Any) -> Any:
        text = str(value)
        match = _DATE_PREFIX.match(text)
        if match:
            try:
                moved = date(int(match.group(1)), int(match.group(2)), int(match.group(3))) + timedelta(days=self._days)
            except (ValueError, OverflowError):
                pass
            else:
                return moved.isoformat() + match.group(4)
        self.unparsed["date"] = self.unparsed.get("date", 0) + 1
        return value

    def _id(self, value: Any) -> Any:
        """Whole numbers permute within their own range; everything else keeps its shape."""
        if isinstance(value, float):
            return value
        if isinstance(value, int):
            sign = -1 if value < 0 else 1
            return sign * int(self._permute_digits(str(abs(value)), padded=False))
        text = str(value).strip()
        if text.isdigit() and text.isascii():
            return self._permute_digits(text, padded=len(text) > 1 and text.startswith("0"))
        if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text):
            return self._unique("uuid", text, lambda rng: str(uuid.UUID(int=rng.getrandbits(128), version=4)), normalize=False)
        return self._unique("id", text, lambda rng: _shape(text, rng), normalize=False)

    def _permute_digits(self, digits: str, padded: bool) -> str:
        """The permuted number as text, with the same number of digits (zeros kept if it had them)."""
        length = len(digits)
        if length > 18:
            return self._unique("id", digits, lambda rng: _shape(digits, rng), normalize=False)
        low, high = (0, 10**length) if padded or length == 1 else (10 ** (length - 1), 10**length)
        moved = str(low + _permute(self._key, b"id", int(digits) - low, high - low))
        moved = moved.zfill(length) if padded else moved
        if self._record:
            self.pairs.setdefault("id", {}).setdefault(moved, digits)
        return moved


def _shape(text: str, rng: random.Random, keep: int = 0) -> str:
    """A string of the same shape: digits for digits, letters for letters, punctuation as it was."""
    out = []
    for position, char in enumerate(text):
        if position < keep or not char.isalnum() or not char.isascii():
            out.append(char)
        elif char.isdigit():
            out.append(str(rng.randint(0, 9)))
        elif char.isupper():
            out.append(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        else:
            out.append(rng.choice("abcdefghijklmnopqrstuvwxyz"))
    return "".join(out)


def _permute(key: bytes, label: bytes, value: int, size: int) -> int:
    """A keyed one-to-one mapping of range(size) onto itself (a Feistel network, cycle-walked)."""
    if size <= 1:
        return value
    bits = max(2, (size - 1).bit_length())
    bits += bits % 2
    half = bits // 2
    mask = (1 << half) - 1

    def round_value(round_number: int, part: int) -> int:
        message = label + bytes([round_number]) + part.to_bytes(8, "big")
        return int.from_bytes(hmac.new(key, message, hashlib.sha256).digest()[:8], "big") & mask

    current = value
    while True:
        left, right = current >> half, current & mask
        for round_number in range(4):
            left, right = right, left ^ round_value(round_number, right)
        current = (left << half) | right
        if current < size:
            return current
