"""Take literal data out of the CREATE statements a SQLite database keeps.

A database's own SQL can hold real values: `owner TEXT DEFAULT 'Jane Doe'`, `CHECK(email <> 'a@b.co')`,
a view with `WHERE name = 'Bob'`, or a comment. A pseudonymized copy can't carry those over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(
    r"""
    (?P<comment>--[^\n]*|/\*.*?(?:\*/|$))
    |(?P<string>[xX]?'(?:[^']|'')*'?)
    |(?P<quoted>"(?:[^"]|"")*"?|`(?:[^`]|``)*`?|\[[^\]]*\]?)
    |(?P<word>[A-Za-z_][\w$]*)
    |(?P<space>\s+)
    |(?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


@dataclass(frozen=True)
class CleanTable:
    sql: str
    removed_checks: bool = False
    blanked_text: bool = False


def _tokens(sql: str) -> list[tuple[str, str]]:
    return [
        (match.lastgroup or "", match.group())
        for match in _TOKEN.finditer(sql)
        if match.lastgroup not in ("comment", "space")
    ]


def clean_table_sql(sql: str) -> CleanTable:
    """The statement without comments or CHECK constraints, and with its text literals blanked."""
    tokens = _tokens(sql)
    out: list[str] = []
    removed = blanked = False
    index = 0
    while index < len(tokens):
        kind, text = tokens[index]
        if kind == "word" and text.upper() == "CHECK" and tokens[index + 1 : index + 2] == [("other", "(")]:
            end = _closing_paren(tokens, index + 1)
            if len(out) >= 2 and out[-2].upper() == "CONSTRAINT":
                del out[-2:]
            following = tokens[end + 1][1] if end + 1 < len(tokens) else ""
            if out and out[-1] == "," and following in (",", ")"):
                out.pop()  # It was a table constraint: take its comma too.
            removed = True
            index = end + 1
            continue
        if kind == "string" or (kind == "quoted" and text.startswith('"') and out and out[-1].upper() == "DEFAULT"):
            # DEFAULT "x" is a string too, as SQLite reads it. Blanking keeps NOT NULL DEFAULT valid.
            out.append("''")
            blanked = True
        else:
            out.append(text)
        index += 1
    return CleanTable(" ".join(out), removed, blanked)


def holds_text(sql: str) -> bool:
    """Whether a statement has a text literal, so that it can't be kept without changing what it means."""
    return any(kind == "string" for kind, _ in _tokens(sql))


def without_comments(sql: str) -> str:
    return " ".join(text for _, text in _tokens(sql))


def _closing_paren(tokens: list[tuple[str, str]], opening: int) -> int:
    depth = 0
    for index in range(opening, len(tokens)):
        kind, text = tokens[index]
        if kind == "other" and text == "(":
            depth += 1
        elif kind == "other" and text == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(tokens) - 1
