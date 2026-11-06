"""Anonymize a folder of documents, or a stream of lines, with placeholders that stay stable.

A folder is anonymized with one `Anonymizer`, so a person in the third document has the same
placeholder as in the first, and one mapping file restores the whole folder. Output files are
renamed `file_001.txt`, `file_002.docx`... because a file name can be as sensitive as what's in
it; the mapping records what each was called. `keep_names` keeps the real names, for when you
know they are safe.

A stream is anonymized a line at a time, as it arrives, so it works on `tail -f`.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..sources.base import SourceError
from .anonymizer import PLACEHOLDER, Anonymizer
from .documents import anonymize_file, can_handle, restore_bytes

# What a handler turns each kind of file into.
_OUTPUT_SUFFIX = {".eml": ".txt", ".pdf": ".txt"}


@dataclass
class BatchResult:
    files: dict[str, str] = field(default_factory=dict)  # output name -> the name it had
    skipped: dict[str, int] = field(default_factory=dict)  # file type -> how many
    failed: list[str] = field(default_factory=list)  # a message per file, using output names
    notes: list[str] = field(default_factory=list)


def output_suffix(suffix: str) -> str:
    return _OUTPUT_SUFFIX.get(suffix.lower(), suffix.lower())


def list_documents(root: Path, recursive: bool) -> tuple[list[tuple[Path, str]], Counter[str]]:
    """The files that can be anonymized, with their relative names, and a count of the rest."""
    found: list[tuple[Path, str]] = []
    skipped: Counter[str] = Counter()

    def consider(path: Path) -> None:
        name = path.name
        if name.startswith(".") or path.is_symlink():
            return
        if can_handle(path):
            found.append((path, path.relative_to(root).as_posix()))
        else:
            skipped[path.suffix.lower() or "(no extension)"] += 1

    if recursive:
        for folder, folders, names in os.walk(root, followlinks=False):
            folders[:] = sorted(name for name in folders if not name.startswith("."))
            for name in sorted(names):
                consider(Path(folder) / name)
    else:
        for path in sorted(root.iterdir()):
            if path.is_file():
                consider(path)
    return found, skipped


def anonymize_folder(
    source: str,
    target: str,
    anonymizer: Anonymizer,
    recursive: bool = False,
    keep_names: bool = False,
    finish: Callable[[BatchResult], object] | None = None,
) -> BatchResult:
    """Anonymize every document in `source` into `target`. `finish` is called with what was done
    so far, even if a file raises, so a mapping file can be written for the output that exists."""
    root, out = Path(source).resolve(), Path(target).resolve()
    if out == root or root in out.parents:
        raise SourceError("the output folder can't be inside the input folder")
    documents, skipped = list_documents(root, recursive)
    result = BatchResult(skipped=dict(skipped))

    planned: list[tuple[Path, str, Path]] = []
    used: set[str] = set()
    for number, (path, relative) in enumerate(documents, start=1):
        suffix = output_suffix(path.suffix)
        name = str(Path(relative).with_suffix(suffix).as_posix()) if keep_names else f"file_{number:03d}{suffix}"
        if os.path.normcase(name) in used:  # a.eml and a.txt both end up as text: keep them apart.
            name = f"{relative}{suffix}"
        used.add(os.path.normcase(name))
        planned.append((path, relative, out / name))
    existing = [str(destination.relative_to(out)) for _, _, destination in planned if destination.exists()]
    if existing:
        raise SourceError("the output folder already has a file called " + existing[0] + "; use a new folder")

    try:
        for path, relative, destination in planned:
            name = destination.relative_to(out).as_posix()
            label = name if not keep_names else relative
            try:
                output = anonymize_file(path, anonymizer)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with open(destination, "xb") as stream:
                    stream.write(output.data)
            except SourceError as error:
                result.failed.append(f"{label}: {error}")
                continue
            except Exception as error:  # One bad file shouldn't end the run. Only its type: the text can quote data.
                result.failed.append(f"{label}: it couldn't be read ({type(error).__name__})")
                continue
            result.files[name] = relative
            result.notes.extend(note for note in output.notes if note not in result.notes)
    finally:
        if finish is not None:
            finish(result)
    return result


def restore_folder(source: str, target: str, placeholders: dict[str, str], files: dict[str, str]) -> tuple[int, list[str]]:
    """Restore every file in a folder `anonymize_folder` made. Returns the count and the unknown placeholders."""
    root, out = Path(source).resolve(), Path(target).resolve()
    if out == root or root in out.parents or out in root.parents:
        raise SourceError("the input and output folders must be separate")
    count = 0
    unknown: set[str] = set()
    paths = sorted(path for path in root.rglob("*") if path.is_file() and not path.name.startswith("."))
    for path in paths:
        relative = path.relative_to(root).as_posix()
        original = files.get(relative)
        name = str(Path(original).with_suffix(path.suffix)) if original else relative
        destination = out / name
        if destination.exists():
            raise SourceError("the output folder already has a file called " + name + "; use a new folder")
        data = path.read_bytes()
        try:
            restored = restore_bytes(data, path.suffix.lower(), placeholders)
        except (UnicodeDecodeError, SourceError):
            continue  # Not a text or Word file: nothing to restore in it.
        unknown.update(_unknown(restored, path.suffix.lower(), placeholders))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "xb") as stream:
            stream.write(restored)
        count += 1
    return count, sorted(unknown)


def _unknown(data: bytes, suffix: str, placeholders: dict[str, str]) -> set[str]:
    if suffix == ".docx":
        return set()
    text = data.decode("utf-8", errors="replace")
    return {match.group() for match in PLACEHOLDER.finditer(text)} - set(placeholders)


def anonymize_stream(
    lines: Iterable[bytes],
    anonymizer: Anonymizer,
    write: Callable[[str], object],
    flush: Callable[[], object] | None = None,
    after_line: Callable[[], object] | None = None,
) -> int:
    """Anonymize lines as they arrive. Returns how many there were."""
    count = 0
    for raw in lines:
        text = raw.decode("utf-8", errors="replace")
        ending = "\r\n" if text.endswith("\r\n") else "\n" if text.endswith("\n") else ""
        write(anonymizer.anonymize(text[: len(text) - len(ending)]) + ending)
        if flush is not None:
            flush()
        if after_line is not None:
            after_line()
        count += 1
    return count
