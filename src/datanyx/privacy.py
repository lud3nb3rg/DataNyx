"""Rules for deciding whether a key is structure or data.

Values are never copied into a schema, so keys are the main way data could
leak: `{"alice@example.com": {...}}` stores a person's email address in a key.
Every rule here errs towards hiding a key, because a hidden field costs the
agent some precision while a leaked key can't be taken back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .formats import detect_format

_SAFE_KEY = re.compile(r"[\w$.:\-]+")
_NUMERIC = re.compile(r"[+-]?\d+([.,]\d+)?")
_PHONE = re.compile(r"\+?[\d\s().\-]{7,}")
_LONG_DIGIT_RUN = re.compile(r"\d{4,}")
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{16,}")
_MAX_KEY_LENGTH = 64
_TOKEN_MIN_LENGTH = 20


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
    """

    map_paths: frozenset[str] = frozenset()
    keep_names: frozenset[str] = frozenset()
    min_key_support: int = 2
    min_instances_for_support: int = 5
    map_threshold_objects: int = 8
    map_threshold_scalars: int = 32

    def shows_name(self, name: str) -> bool:
        """Whether a catalog name, such as a table or column, may be shown."""
        return name in self.keep_names or not looks_like_data(name)
