"""Find credentials in text: API keys, tokens, private keys and passwords.

These formats are distinctive enough for patterns alone. The detector is a `Detector`
like any other, so it works with `anonymize`, but it isn't part of the default set
there. `datanyx scan` uses it.

Only the secret itself is marked: the name in `password = "..."` stays outside the span.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from .detectors import Span

SECRET = "SECRET"

_NAME = r"(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)"
# Values that stand in for a secret rather than being one.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:<.*>|\$\{.*\}|\$\(.*\)|%.*%|\{\{.*\}\}|x{4,}|\*{4,}|\.{3,}|changeme|change[_-]?me|example|sample|dummy|"
    r"placeholder|your[_-].*|my[_-]?(?:secret|password|key|token).*|none|null|true|false|redacted|"
    r"os\.environ.*|os\.getenv.*|process\.env.*|getenv.*|env\[.*|secrets\..*|config\..*|settings\..*|args\..*|self\..*|"
    r"input\(.*|prompt.*|password|secret|token|string|str|value|testpassword|test[_-]?secret.*|\w+(?:\.\w+)*\(.*)$"
)

# (pattern, group holding the secret, or 0 for the whole match)
_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----"), 0),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), 0),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), 0),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0),
    (re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}\b"), 0),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0),
    # scheme://user:password@host: the credentials, not the address.
    (re.compile(r"(?<=://)[^/\s:@'\"<>]+:([^@\s'\"<>]{3,})(?=@)"), 1),
    (re.compile(rf"(?i)\b\w*{_NAME}\w*[\"']?\s*(?::|=|=>)\s*[bBuUrR]?[\"']?([^\s\"',;]{{8,}})"), 1),
)


class SecretDetector:
    """Credentials and keys, found by their shape."""

    def find(self, text: str) -> Iterator[Span]:
        for pattern, group in _PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span(group)
                value = text[start:end]
                if group and _PLACEHOLDER.match(value):
                    continue
                if start < end:
                    yield Span(start, end, SECRET)
