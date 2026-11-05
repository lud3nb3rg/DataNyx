"""Recognise the format of a string, returning a label and never the string."""

from __future__ import annotations

import ipaddress
import re
from datetime import date, datetime

_MAX_FORMATTED_LENGTH = 320  # Longer than any supported format; bounds regex work.

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_URI = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}.*")


def detect_format(text: str) -> str | None:
    """Return a JSON Schema format name for `text`, or None if it has none."""
    if not text or len(text) > _MAX_FORMATTED_LENGTH:
        return None
    if _UUID.fullmatch(text):
        return "uuid"
    if _EMAIL.fullmatch(text):
        return "email"
    if _URI.fullmatch(text):
        return "uri"
    if _DATE.fullmatch(text):
        return "date" if _parses(date.fromisoformat, text) else None
    if _DATE_TIME.fullmatch(text):
        # fromisoformat only accepts a trailing "Z" from Python 3.11 onwards.
        normalized = re.sub(r"[zZ]$", "+00:00", text)
        return "date-time" if _parses(datetime.fromisoformat, normalized) else None
    return _ip_format(text)


def _parses(parser, text: str) -> bool:
    try:
        parser(text)
    except ValueError:
        return False
    return True


def _ip_format(text: str) -> str | None:
    if "." not in text and ":" not in text:
        return None
    try:
        version = ipaddress.ip_address(text).version
    except ValueError:
        return None
    return f"ipv{version}"
