"""Detector packs for fields whose formats have rules: healthcare and finance.

A pack is a bundle of detectors, switched on with `--pack healthcare` or `packs = [...]` in
`datanyx.toml`. They are patterns, so they find what has a recognisable shape (an SSN, an IBAN's
neighbours, a VIN) or follows a label ("MRN: 00482913", "routing number 021000021"). Where a
format has a check digit it is verified, so a random run of digits isn't mistaken for an account.

They add to the built-in detectors and don't replace them, and they find *identifiers*, not
meaning: a diagnosis, or a description that points at one person, isn't something a pattern sees.

The healthcare pack follows the identifiers of HIPAA's Safe Harbor list that have a form: names
after "Patient:" or "Dr.", social security and medical record numbers, health plan and device
numbers, vehicle identifiers, ZIP codes after a state, ages over 89, and fax numbers. Dates, phone
numbers, emails, URLs and IP addresses are found by the general detectors. It is a help for
de-identification, not a certification that a text meets the standard.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from ..sources.base import SourceError
from .custom import RegexDetector
from .detectors import Detector, Span, luhn_valid


class PackDetector(RegexDetector):
    """A pattern, with an optional check that the matched text is really of the kind."""

    def __init__(
        self,
        kind: str,
        pattern: str,
        group: int = 0,
        check: Callable[[str], bool] | None = None,
        flags: int = 0,
    ) -> None:
        super().__init__(kind, re.compile(pattern, flags), group)
        self._check = check

    def find(self, text: str) -> Iterator[Span]:
        for span in super().find(text):
            if self._check is None or self._check(text[span.start : span.end]):
                yield span


# --- check digits ----------------------------------------------------------------------------------------


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def aba_valid(value: str) -> bool:
    """A US routing number: nine digits whose weighted sum is a multiple of ten."""
    digits = _digits(value)
    if len(digits) != 9:
        return False
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    return sum(int(d) * w for d, w in zip(digits, weights)) % 10 == 0


def isin_valid(value: str) -> bool:
    text = value.upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", text):
        return False
    return luhn_valid("".join(str(int(char, 36)) for char in text))


def npi_valid(value: str) -> bool:
    digits = _digits(value)
    return len(digits) == 10 and luhn_valid("80840" + digits)


def dea_valid(value: str) -> bool:
    text = value.upper()
    if not re.fullmatch(r"[ABFGMPRX][A-Z9]\d{7}", text):
        return False
    d = [int(char) for char in text[2:]]
    return (d[0] + d[2] + d[4] + 2 * (d[1] + d[3] + d[5])) % 10 == d[6]


_VIN_VALUES = {**{str(n): n for n in range(10)}}
_VIN_VALUES.update({c: v for c, v in zip("ABCDEFGH", range(1, 9))})
_VIN_VALUES.update({c: v for c, v in zip("JKLMN", range(1, 6))})
_VIN_VALUES.update({"P": 7, "R": 9})
_VIN_VALUES.update({c: v for c, v in zip("STUVWXYZ", range(2, 10))})


def vin_valid(value: str) -> bool:
    text = value.upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", text):
        return False
    weights = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
    total = sum(_VIN_VALUES[char] * weight for char, weight in zip(text, weights))
    check = "X" if total % 11 == 10 else str(total % 11)
    return text[8] == check


def over_89(value: str) -> bool:
    digits = _digits(value)
    return bool(digits) and int(digits) > 89


def has_digit(value: str) -> bool:
    """A code after a label, not a word: "member id: pending" has no digit."""
    return any(char.isdigit() for char in value)


def all_capitals(value: str) -> bool:
    return value == value.upper() and any(char.isalpha() for char in value)


def not_a_year_or_date(value: str) -> bool:
    return not re.fullmatch(r"(19|20)\d{2}", value.strip())


# --- the packs --------------------------------------------------------------------------------------------

_NAME_WORD = r"[A-Z][\w'’-]+"
_FLAGS = re.IGNORECASE


def healthcare() -> list[Detector]:
    return [
        PackDetector("SSN", r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        PackDetector("MRN", r"\b(?:MRN|medical\s+record(?:\s+(?:number|no\.?))?)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,11})\b", 1, has_digit, _FLAGS),
        PackDetector(
            "MEMBER_ID",
            r"\b(?:member|policy|subscriber|beneficiary|insurance|health\s*plan)\s*(?:id|number|no\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{5,19})\b",
            1,
            has_digit,
            _FLAGS,
        ),
        PackDetector("NPI", r"\bNPI\s*(?:number|no\.?|#)?\s*[:#]?\s*(\d{10})\b", 1, npi_valid, _FLAGS),
        PackDetector("DEA_NUMBER", r"\b[ABFGMPRX][A-Z9]\d{7}\b", 0, dea_valid),
        PackDetector("VIN", r"\b[A-HJ-NPR-Z0-9]{17}\b", 0, vin_valid),
        PackDetector("DEVICE_ID", r"\b(?:serial|device|implant|udi)\s*(?:no\.?|number|id)?\s*[:#]\s*([A-Z0-9][A-Z0-9-]{5,29})\b", 1, has_digit, _FLAGS),
        PackDetector("ZIP_CODE", r"\b[A-Z]{2}\s+(\d{5}(?:-\d{4})?)\b", 1),
        PackDetector("AGE", r"\b(?:ages?[ds]?\s*[:\-]?\s*|aged\s+)(\d{2,3})\b", 1, over_89, _FLAGS),
        PackDetector("AGE", r"\b(\d{2,3})[- ]year[- ]old\b", 1, over_89, _FLAGS),
        # Only the label ignores case: the name after it must be capitalised, or "Amara Okonkwo was seen" is all taken.
        PackDetector("PERSON", rf"\b(?i:patient|pt|patient\s+name|name\s+of\s+patient)\s*[:\-]\s*({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,3}})", 1),
        PackDetector("PERSON", rf"\b(?:Dr|Doctor|Nurse|Prof)\.?\s+({_NAME_WORD}(?:\s+{_NAME_WORD})?)"),
        PackDetector("PHONE", r"\bfax\s*[:#]?\s*(\+?\d[\d\s().-]{6,}\d)", 1, flags=_FLAGS),
    ]


def finance() -> list[Detector]:
    return [
        PackDetector("SWIFT_CODE", r"\b(?:swift|bic)(?:\s*/\s*(?:swift|bic))?\s*(?:code|number|no\.?)?\s*[:#]?\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", 1, all_capitals, _FLAGS),
        PackDetector("ROUTING_NUMBER", r"\b(?:routing|aba|rtn)\s*(?:number|no\.?|#)?\s*[:#]?\s*(\d{9})\b", 1, aba_valid, _FLAGS),
        PackDetector("SORT_CODE", r"\bsort\s*code\s*[:#]?\s*(\d{2}-?\d{2}-?\d{2})\b", 1, flags=_FLAGS),
        PackDetector("ACCOUNT_NUMBER", r"\b(?:account|acct|a/c)\s*(?:number|num|no\.?|#)?\s*[:#]?\s*(\d[\d -]{5,20}\d)\b", 1, not_a_year_or_date, _FLAGS),
        PackDetector("ISIN", r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", 0, isin_valid),
        PackDetector("TAX_ID", r"\b(?:EIN|TIN|tax\s*id(?:entification)?(?:\s*number)?)\s*[:#]?\s*(\d{2}-?\d{7})\b", 1, flags=_FLAGS),
        PackDetector("SSN", r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        PackDetector("NATIONAL_INSURANCE", r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"),
        PackDetector("VAT_NUMBER", r"\bVAT\s*(?:number|no\.?|id|reg(?:istration)?(?:\s*number)?)?\s*[:#]?\s*([A-Z]{2}[A-Z0-9]{8,12})\b", 1, has_digit, _FLAGS),
        PackDetector("CRYPTO_WALLET", r"\b(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|0x[a-fA-F0-9]{40})\b"),
    ]


PACKS: dict[str, Callable[[], list[Detector]]] = {"healthcare": healthcare, "finance": finance}


def load_packs(names: list[str] | tuple[str, ...]) -> list[Detector]:
    """The detectors of the named packs, each pack once."""
    detectors: list[Detector] = []
    for name in dict.fromkeys(names):
        try:
            detectors.extend(PACKS[name.lower()]())
        except KeyError:
            raise SourceError(f"unknown pack {name!r}; the choices are {', '.join(sorted(PACKS))}") from None
    return detectors
