"""Print the CHANGELOG.md notes for one version, for the GitHub Release page.

    python tools/release_notes.py 0.2.0

The notes are the lines under the first heading that starts `## 0.2.0` (with or without a date) up to the next
`## ` heading. Exits with 1 and says so when there is no such section or it is empty, so a release can't go out
with a blank page.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def notes_for(version: str, text: str) -> str:
    """The body of the section for `version`, or an empty string when there is none."""
    wanted = re.compile(rf"^## {re.escape(version)}(?:\s.*)?$")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if wanted.match(line)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py VERSION", file=sys.stderr)
        return 2
    notes = notes_for(argv[1].removeprefix("v"), CHANGELOG.read_text(encoding="utf-8"))
    if not notes:
        print(f"CHANGELOG.md has no notes for {argv[1]}; add a '## {argv[1]}' section before tagging", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
