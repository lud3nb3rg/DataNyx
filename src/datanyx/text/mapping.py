"""The file that records each placeholder's real value, so anonymized text can be restored.

It holds the very data being hidden, so it is created readable by its owner only, is never
overwritten, and every error about it is a fixed message.

Two shapes are read. A single text has a flat object, `{"<PERSON_1>": "Jessica Whitfield"}`.
A folder has `{"placeholders": {...}, "files": {"file_001.txt": "reports/q3.txt"}}`, where
`files` says what each output file was called before it was renamed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import IO, Any

from ..fileperms import restrict_to_owner_or_remove
from ..sources.base import SourceError


class MappingFile:
    """A mapping file being written. It can be rewritten as the mapping grows."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._stream: IO[str] | None = None
        try:
            # "x": placeholders only mean something for the run that made them, so an
            # existing mapping is never replaced.
            self._stream = open(self.path, "x", encoding="utf-8", opener=_owner_only)
        except FileExistsError:
            raise SourceError("the mapping file already exists; choose a new name") from None
        except OSError as error:
            raise SourceError(f"cannot write the mapping: {error.strerror}") from None
        if os.name == "nt":
            self._stream.close()  # Windows won't change or remove an open file.
            self._stream = None
            restrict_to_owner_or_remove(self.path, "the mapping file")
            self._stream = open(self.path, "w", encoding="utf-8")

    def write(self, placeholders: dict[str, str], files: dict[str, str] | None = None) -> None:
        self.write_document(placeholders if files is None else {"placeholders": placeholders, "files": files})

    def write_document(self, document: Any) -> None:
        """Write any JSON document, for mappings that aren't a flat list of placeholders."""
        assert self._stream is not None
        self._stream.seek(0)
        self._stream.truncate()
        json.dump(document, self._stream, ensure_ascii=False, indent=2)
        self._stream.flush()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def _owner_only(file: str, flags: int) -> int:
    return os.open(file, flags, 0o600)


def read_mapping(path: str | Path) -> tuple[dict[str, str], dict[str, str]]:
    """The placeholders and, for a folder, the file names. Every failure is a fixed message."""
    try:
        with open(path, encoding="utf-8") as stream:
            document = json.load(stream)
    except OSError as error:
        raise SourceError(f"cannot read the mapping: {error.strerror}") from None
    except ValueError:
        raise SourceError("the mapping file isn't valid JSON") from None
    if isinstance(document, dict) and isinstance(document.get("placeholders"), dict):
        placeholders, files = document["placeholders"], document.get("files", {})
    else:
        placeholders, files = document, {}
    if not _strings(placeholders) or not _strings(files):
        raise SourceError("the mapping file isn't in the expected format")
    return placeholders, files


def _strings(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
