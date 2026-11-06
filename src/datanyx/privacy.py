"""Rules for deciding whether a key is structure or data.

Values are never copied into a schema, so keys are the main way data could
leak: `{"alice@example.com": {...}}` stores a person's email address in a key.
Every rule here errs towards hiding a key, because a hidden field costs the
agent some precision while a leaked key can't be taken back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .formats import detect_format

_SAFE_KEY = re.compile(r"[\w$.:\-]+")
_NUMERIC = re.compile(r"[+-]?\d+([.,]\d+)?")
_PHONE = re.compile(r"\+?[\d\s().\-]{7,}")
_LONG_DIGIT_RUN = re.compile(r"\d{4,}")
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{16,}")
_MAX_KEY_LENGTH = 64
_TOKEN_MIN_LENGTH = 20
_WORDS = re.compile(r"[a-z]+")
# Words that say what a document is about. Beside other words they usually name whose it is, as in
# jane_doe_medical.csv. On its own (medical.csv) such a word is a topic, and stays visible.
_SENSITIVE_SUBJECTS = frozenset(
    {
        "medical", "health", "diagnosis", "therapy", "psych", "psychiatric", "prescription", "hiv", "cancer", "pregnancy",
        "salary", "payslip", "paystub", "bank", "statement", "tax", "passport", "visa", "ssn", "divorce", "custody",
        "criminal", "disciplinary", "resume", "cv", "contract", "invoice", "personal", "private", "confidential",
    }
)


def looks_like_data(key: str) -> bool:
    """True when the key's own shape suggests it holds data, not a field name."""
    if not key or len(key) > _MAX_KEY_LENGTH or not _SAFE_KEY.fullmatch(key):
        return True  # Empty, overlong, or containing spaces, "@", newlines, ...
    if detect_format(key) is not None:
        return True
    if _NUMERIC.fullmatch(key) or _PHONE.fullmatch(key):
        return True
    if _LONG_DIGIT_RUN.search(key) or _HEX_TOKEN.fullmatch(key):
        return True
    return _is_opaque_token(key)


def _is_opaque_token(key: str) -> bool:
    if len(key) < _TOKEN_MIN_LENGTH or any(c in key for c in "_-.:$"):
        return False
    has_digit = any(c.isdigit() for c in key)
    # A long unbroken run mixing digits with letters reads as an ID or API key.
    return has_digit and any(c.isalpha() for c in key)


def file_name_looks_personal(name: str) -> bool:
    """A file name that names a subject and something more, such as a person: jane_doe_medical.csv."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    words = _WORDS.findall(stem.lower())
    return len(words) >= 2 and any(word in _SENSITIVE_SUBJECTS for word in words)


@dataclass(frozen=True)
class KeyPolicy:
    """Settings for when object keys are hidden instead of named.

    map_paths: schema paths that are always maps, e.g. "$.users" or "$[].tags".
    keep_names: exact names to show even if the automatic rules would hide them.
        A map path still wins, since hiding is the safer of two explicit settings.
    min_key_support: a key must appear in this many objects to be named...
    min_instances_for_support: ...once at least this many objects were seen.
    map_threshold_objects: distinct keys at which same-shaped object values
        are treated as a map.
    map_threshold_scalars: the same, for values of a single scalar type. Higher
        because a flat record with many string fields is common and legitimate.
    scale_hints: also record each table's row count as an order of magnitude.
    infer_keys: also flag key-looking columns that are unique, and joins between tables.
    delimiter: the one-character separator of CSV and TSV input, instead of the usual one.
    """

    map_paths: frozenset[str] = frozenset()
    keep_names: frozenset[str] = frozenset()
    min_key_support: int = 2
    min_instances_for_support: int = 5
    map_threshold_objects: int = 8
    map_threshold_scalars: int = 32
    scale_hints: bool = False
    infer_keys: bool = False
    delimiter: str | None = None
    #: Collects key evidence while a source reads (`datanyx.keys.Evidence`); made by `extract`.
    evidence: Any = field(default=None, compare=False, repr=False)

    def shows_name(self, name: str) -> bool:
        """Whether a catalog name, such as a table or column, may be shown."""
        return name in self.keep_names or not looks_like_data(name)

    def shows_file_name(self, name: str) -> bool:
        """Whether a file or folder name may be shown. They say more than a column name can."""
        return name in self.keep_names or not (looks_like_data(name) or file_name_looks_personal(name))
