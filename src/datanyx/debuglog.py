"""Where an unexpected failure happened in the MCP server, for whoever runs it.

The agent is only ever told "internal failure (TypeName)", because an exception's message can quote the data. That
leaves the owner with little to go on. This log is for them: one JSON object per line with the tool, the time, the
exception types (the chain of causes too) and the frames of the traceback as file, line and function name.

Never a message, never a source line, never an argument or a local variable: those are where values live. A file
and line number say which code failed and nothing about the data it was handling. The file is created private.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .audit import prepare_private_log

_MAX_FRAMES = 40
_MAX_CAUSES = 5
_PACKAGE_DIR = Path(__file__).resolve().parent
_INSTALLED = ("site-packages", "dist-packages")


def _where(filename: str) -> str:
    """A path shortened to the package and module: `datanyx/sources/files.py`, `pyarrow/parquet/core.py`."""
    path = Path(filename)
    try:
        return "datanyx/" + path.resolve().relative_to(_PACKAGE_DIR).as_posix()
    except (ValueError, OSError):
        pass
    parts = path.parts
    for marker in _INSTALLED:
        if marker in parts:
            return "/".join(parts[parts.index(marker) + 1 :])
    return path.name


def describe(error: BaseException) -> dict[str, object]:
    """The type names and traceback frames of `error`, with nothing a value could be in."""
    causes: list[str] = []
    current: BaseException | None = error.__cause__ or error.__context__
    while current is not None and len(causes) < _MAX_CAUSES:
        causes.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames = [
        {"file": _where(frame.filename), "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(error.__traceback__)[-_MAX_FRAMES:]
    ]
    return {"error": type(error).__name__, "causes": causes, "frames": frames}


class DebugLog:
    """Appends failure records to a private file. With no path it does nothing."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        if self.path is not None:
            prepare_private_log(self.path, "debug log")

    def record(self, tool: str, error: BaseException) -> None:
        if self.path is None:
            return
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": tool, **describe(error)}
        try:
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=True) + os.linesep)
        except OSError:
            pass  # A full disk shouldn't turn a failure into a different one.
