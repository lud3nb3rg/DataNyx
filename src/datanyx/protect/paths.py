"""Comparing paths after resolving them, across Windows, POSIX and Git Bash spellings."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DRIVE_PATH = re.compile(r"/(?:cygdrive/|mnt/)?([A-Za-z])(/.*)?")


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(path: Path | str, folder: Path | str) -> bool:
    inner, outer = _key(path), _key(folder)
    return inner == outer or inner.startswith(outer.rstrip(os.sep) + os.sep)


def _native(text: str) -> str:
    """Git Bash and WSL write C:\\Users as /c/Users or /mnt/c/Users. On Windows, that is what they mean."""
    if os.name == "nt":
        match = _DRIVE_PATH.fullmatch(text.replace("\\", "/"))
        if match:
            return f"{match[1]}:{match[2] or '/'}"
    return text


def _drive_form(path: Path) -> str:
    """A Windows path as Git Bash writes it, in lower case. Empty if it has no drive."""
    posix = path.as_posix()
    return f"/{posix[0]}{posix[2:]}".lower() if len(posix) > 1 and posix[1] == ":" else ""
