"""A record of what an agent did through the MCP server: which tool, on what, and how it went.

One JSON object per line. It never holds a value or anything the agent was given back:
only the tool's name, the paths and settings it was called with, whether it worked, and
the sizes involved. Arguments that could carry data are reduced to a length or a count
(the SQL an agent asked to have checked, the terms it asked to hide), and a password in a
database URL is replaced.

Keep the file where the agent can't change it. `datanyx audit FILE` summarises it.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .credentials import hide_url_credentials
from .fileperms import restrict_to_owner_or_remove
from .sources.base import SourceError

_MAX_TEXT = 300
# How each tool's arguments are recorded. Anything not listed is reduced to its size.
_KEEP = "keep"
_COUNT = "count"
_LENGTH = "length"
_RULES: dict[str, dict[str, str]] = {
    "describe_data": {k: _KEEP for k in ("target", "format", "recursive", "map_paths", "database_schema")},
    "synthetic_sample": {k: _KEEP for k in ("target", "rows", "recursive", "map_paths", "database_schema")},
    "check_code": {"code": _LENGTH, **{k: _KEEP for k in ("target", "language", "dialect", "table", "database_schema")}},
    "scan_folder": {"target": _KEEP, "kinds": _KEEP},
    "schema_diff": {"old": _KEEP, "new": _KEEP},
    "anonymize_file": {"target": _KEEP},
}


class AuditLog:
    """Appends records to a file. With no path it does nothing."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.session = uuid.uuid4().hex[:12]
        if self.path is not None:
            self._prepare()

    def _prepare(self) -> None:
        assert self.path is not None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            created = not self.path.exists()
            with open(self.path, "a", encoding="utf-8"):
                pass
            if created:
                restrict_to_owner_or_remove(str(self.path), "the audit log")
        except OSError:
            raise SourceError("cannot write the audit log") from None

    def record(
        self, tool: str, arguments: Mapping[str, Any], outcome: str, message: str | None, started: float, output_chars: int | None
    ) -> None:
        if self.path is None:
            return
        entry: dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": self.session,
            "tool": tool,
            "arguments": summarize(tool, arguments),
            "outcome": outcome,
            "seconds": round(time.monotonic() - started, 3),
        }
        if message:
            entry["message"] = message[:_MAX_TEXT]
        if output_chars is not None:
            entry["output_chars"] = output_chars
        try:
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=True) + chr(10))
        except OSError:
            pass  # A full disk shouldn't stop the tool the agent asked for.


def summarize(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    rules = _RULES.get(tool, {})
    summary: dict[str, Any] = {}
    for name, value in arguments.items():
        mode = rules.get(name)
        if value is None:
            continue
        if mode == _KEEP:
            summary[name] = _clean(value)
        elif mode == _COUNT or (mode is None and isinstance(value, (list, tuple, dict))):
            summary[f"{name}_count"] = len(value)
        else:
            summary[f"{name}_length"] = len(value) if hasattr(value, "__len__") else 1
    return summary


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return hide_url_credentials(value)[:_MAX_TEXT]
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value][:50]
    return value


# --- reading a log back ------------------------------------------------------------------


def read_log(path: str | Path, since: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """The records in a log, and how many lines couldn't be read."""
    cutoff = _parse_time(since) if since else None
    records: list[dict[str, Any]] = []
    unreadable = 0
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise SourceError("cannot read the audit log") from None
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            when = _parse_time(entry["time"])
        except (ValueError, KeyError, TypeError, SourceError):  # SourceError: a time that can't be read.
            unreadable += 1
            continue
        if cutoff is None or when >= cutoff:
            records.append(entry)
    return records, unreadable


def _parse_time(text: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise SourceError("times look like 2025-01-31 or 2025-01-31T09:30:00") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def summarize_log(records: list[dict[str, Any]], unreadable: int = 0, output_format: str = "text") -> str:
    tools = Counter(entry.get("tool", "?") for entry in records)
    sessions = {entry.get("session") for entry in records}
    targets: Counter[str] = Counter()
    for entry in records:
        arguments = entry.get("arguments", {})
        for key in ("target", "old", "new"):
            if isinstance(arguments.get(key), str):
                targets[arguments[key]] += 1
    failures = Counter(
        (entry.get("tool", "?"), entry.get("message", "")) for entry in records if entry.get("outcome") != "ok"
    )
    times = sorted(entry["time"] for entry in records if "time" in entry)

    if output_format == "json":
        return json.dumps(
            {
                "calls": len(records),
                "sessions": len(sessions),
                "first": times[0] if times else None,
                "last": times[-1] if times else None,
                "tools": dict(tools),
                "targets": dict(targets),
                "failures": [{"tool": tool, "message": message, "count": count} for (tool, message), count in failures.items()],
                "unreadable_lines": unreadable,
            },
            indent=2,
        )
    if not records:
        return "The audit log has no calls" + (" in that period." if unreadable == 0 else f" ({unreadable} unreadable lines).")
    lines = [
        f"{len(records):,} call{'s' if len(records) != 1 else ''} in {len(sessions)} session{'s' if len(sessions) != 1 else ''}, "
        f"{times[0]} to {times[-1]}",
        "",
        "By tool: " + " · ".join(f"{tool} {count:,}" for tool, count in tools.most_common()),
    ]
    if targets:
        lines += ["", "Paths asked about:"]
        lines += [f"  {target}  ×{count}" for target, count in targets.most_common(15)]
    if failures:
        lines += ["", f"{sum(failures.values())} call{'s' if sum(failures.values()) != 1 else ''} failed or were refused:"]
        lines += [f"  {tool}: {message}  ×{count}" for (tool, message), count in failures.most_common(10)]
    if unreadable:
        lines += ["", f"note: {unreadable} line{'s' if unreadable != 1 else ''} couldn't be read."]
    return chr(10).join(lines)


__all__ = ["AuditLog", "read_log", "summarize", "summarize_log"]

