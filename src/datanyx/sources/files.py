"""Helpers shared by sources that read files."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import re
import sys
import zlib
from collections.abc import Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

from ..formats import detect_format
from ..inference import Observation
from ..schema import Array, DataSchema, Entity, Node
from .base import SourceError

STDIN = "-"
_ENV_PREFIX = "env:"
#: Where a file can be read from over the network, by the library that reads it (fsspec). A local path
#: or `file://` URL is deliberately not here, so a URL can't get around the folders an MCP server is limited to.
REMOTE_SCHEMES = {
    "http": "remote", "https": "remote", "memory": "remote",
    "s3": "s3", "s3a": "s3",
    "gs": "gcs", "gcs": "gcs",
    "az": "azure", "abfs": "azure", "abfss": "azure", "adl": "azure",
}
_REMOTE_TIMEOUT_SECONDS = 300
_REMOTE_CONNECT_SECONDS = 10


def is_remote(target: str) -> bool:
    """Whether this is a URL of a file on a web server or in cloud storage."""
    scheme, separator, _ = target.partition("://")
    return bool(separator) and scheme.lower() in REMOTE_SCHEMES


def remote_url(target: str) -> str | None:
    """The URL if `target` is one, or is `env:NAME` and the variable holds one. Keeps credentials off the command line."""
    value = os.environ.get(target[len(_ENV_PREFIX) :], "") if target.startswith(_ENV_PREFIX) else target
    return value if is_remote(value) else None


#: Compression a text format can be read through. `data.csv.gz` is read as `data.csv`.
_COMPRESSION = {".gz": "gzip", ".bz2": "bzip2", ".xz": "xz", ".zst": "zstd"}


def _path_of(target: str) -> str:
    # A URL can end in a query string (a signed link), which isn't part of the file name.
    return urlsplit(target).path if is_remote(target) else target


def compression_of(target: str) -> str | None:
    """`gzip`, `bzip2`, `xz` or `zstd` when the name ends like a compressed file, else None."""
    return _COMPRESSION.get(os.path.splitext(_path_of(target))[1].lower())


def plain_name(target: str) -> str:
    """The lower-case name with no URL query and no compression ending: `a.fhir.json` for `A.fhir.json.gz?sig=x`."""
    path = _path_of(target).lower()
    root, ending = os.path.splitext(path)
    return root if ending in _COMPRESSION else path


def extension(target: str) -> str:
    """The file type, looking through compression: `.csv` for `data.csv.gz`."""
    root, ending = os.path.splitext(_path_of(target))
    if ending.lower() in _COMPRESSION:
        ending = os.path.splitext(root)[1]
    return ending.lower()


def reject_namespace(namespace: str | None) -> None:
    if namespace is not None:
        raise SourceError("a schema can only be selected for SQL databases")


def require_path(target: str, what: str) -> None:
    """For binary formats, which can't be streamed from stdin or read compressed."""
    if target == STDIN:
        raise SourceError(f"{what} can't be read from stdin; pass a file path")
    if compression_of(target):
        raise SourceError(f"{what} can't be read compressed; decompress the file first")


def require_local(target: str, what: str) -> None:
    """For formats whose reader needs a file on this computer: not stdin, and not a URL."""
    require_path(target, what)
    if is_remote(target):
        raise SourceError(f"{what} can't be read from a URL; download the file and pass its path")


def _storage_options(scheme: str) -> dict[str, Any]:
    """Time limits, so an unreachable server can't stall an agent's session."""
    if scheme in ("http", "https"):
        try:
            import aiohttp
        except ImportError:
            return {}
        timeout = aiohttp.ClientTimeout(total=_REMOTE_TIMEOUT_SECONDS, connect=_REMOTE_CONNECT_SECONDS)
        return {"client_kwargs": {"timeout": timeout}}
    if scheme in ("s3", "s3a"):
        return {"config_kwargs": {"connect_timeout": _REMOTE_CONNECT_SECONDS, "read_timeout": 60, "retries": {"max_attempts": 2}}}
    return {}


@contextmanager
def _open_remote(url: str, mode: str, **options: Any) -> Iterator[Any]:
    """A file at a URL, opened with fsspec. Only fixed messages and exception names leave: a URL can hold a password or token."""
    scheme = url.partition("://")[0].lower()
    extra = REMOTE_SCHEMES[scheme]
    try:
        import fsspec
    except ImportError:
        raise missing_extra("a file at a URL", "remote") from None
    try:
        stream = fsspec.open(url, mode, **options, **_storage_options(scheme)).open()
    except ImportError:
        raise SourceError(f"reading {scheme}:// URLs needs an extra package; install datanyx[{extra}]") from None
    except Exception as error:
        raise SourceError(f"cannot read the file at that URL ({type(error).__name__})") from None
    try:
        yield stream
    finally:
        stream.close()


@contextmanager
def _raw_binary(target: str) -> Iterator[IO[bytes]]:
    if is_remote(target):
        with _open_remote(target, "rb") as stream:
            yield stream
        return
    with open(target, "rb") as stream:
        yield stream


def _decompress(raw: IO[bytes], kind: str) -> IO[bytes]:
    if kind == "gzip":
        return gzip.GzipFile(fileobj=raw)  # type: ignore[return-value]
    if kind == "bzip2":
        return bz2.BZ2File(raw)
    if kind == "xz":
        return lzma.LZMAFile(raw)
    try:
        import zstandard
    except ImportError:
        raise missing_extra("Zstandard files", "zstd") from None
    return zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True)


def _decompression_errors() -> tuple[type[BaseException], ...]:
    errors: tuple[type[BaseException], ...] = (EOFError, lzma.LZMAError, zlib.error, OSError)
    try:
        import zstandard

        return (*errors, zstandard.ZstdError)
    except ImportError:
        return errors


@contextmanager
def _decoded(target: str, kind: str, translate_open: bool = False) -> Iterator[IO[bytes]]:
    """The bytes of a compressed file, decompressed as they are read. Damaged data becomes one fixed message.

    A file that can't be opened raises OSError, as `open` does, unless `translate_open` is set.
    """
    try:
        raw_context = _raw_binary(target)
        raw = raw_context.__enter__()
    except OSError as error:
        if not translate_open:
            raise
        raise SourceError(f"cannot read input: {error.strerror}") from None
    try:
        stream = _decompress(raw, kind)
        try:
            yield stream
        except _decompression_errors() as error:
            raise SourceError(f"input isn't readable {kind} data ({type(error).__name__})") from None
        finally:
            stream.close()
    finally:
        raw_context.__exit__(None, None, None)


@contextmanager
def binary_stream(target: str) -> Iterator[IO[bytes]]:
    """A binary file object for a path or a URL, decompressed if the name says so. A path that can't be opened raises OSError."""
    kind = compression_of(target)
    if kind is None:
        with _raw_binary(target) as stream:
            yield stream
        return
    with _decoded(target, kind) as stream:
        yield stream


@contextmanager
def path_or_stream(target: str) -> Iterator[Any]:
    """What a library that takes either should be given: the path itself, or a file object for a URL."""
    if is_remote(target):
        with _open_remote(target, "rb") as stream:
            yield stream
        return
    yield target


@contextmanager
def open_text(target: str) -> Iterator[IO[str]]:
    kind = compression_of(target) if target != STDIN else None
    if kind is not None:
        with _decoded(target, kind, translate_open=True) as decoded:
            text = io.TextIOWrapper(decoded, encoding="utf-8-sig")
            try:
                yield text
            finally:
                text.detach()  # The decompressor is closed by its own context.
        return
    if is_remote(target):
        with _open_remote(target, "rt", encoding="utf-8-sig") as stream:
            yield stream
        return
    if target == STDIN:
        # Windows consoles and pipes rarely default to UTF-8, so decode explicitly.
        stream = open(sys.stdin.fileno(), encoding="utf-8-sig", closefd=False)
        yield stream
        return
    try:
        stream = open(target, encoding="utf-8-sig")  # -sig tolerates a Windows BOM.
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    with stream:
        yield stream


def read_bytes(target: str) -> bytes:
    kind = compression_of(target) if target != STDIN else None
    if kind is not None:
        with _decoded(target, kind, translate_open=True) as decoded:
            return decoded.read()
    if is_remote(target):
        with _open_remote(target, "rb") as stream:
            return stream.read()
    try:
        if target == STDIN:
            return sys.stdin.buffer.read()
        with open(target, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None


def sqlite_read_only_uri(path: str | Path) -> str:
    """A URI that opens a SQLite file read-only, whatever its name holds: `#`, `?` and `%` are escaped."""
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def missing_extra(what: str, extra: str) -> SourceError:
    return SourceError(f"reading {what} needs an extra package; install datanyx[{extra}]")


def records_observation() -> Observation:
    """An observation of an array whose items are fed in one at a time."""
    root = Observation()
    root.seen = root.arrays = 1
    return root


def unnamed(kind: str, node: Node, notes: tuple[str, ...], scale: str | None = None) -> DataSchema:
    return DataSchema(kind, (Entity(None, node, scale=scale),), notes)


def items_seen(observation: Observation) -> int | None:
    """How many records a source read, when what it read is a list of them."""
    if observation.arrays and observation.items is not None:
        return observation.items.seen
    return None


EMPTY_TABLE: Node = Array()

_INTEGER = re.compile(r"[+-]?(0|[1-9]\d*)")
_FLOAT = re.compile(r"[+-]?((0|[1-9]\d*)(\.\d+)?|\.\d+)([eE][+-]?\d+)?")


def coerce_text(text: str) -> object:
    """Give text from an untyped format (CSV, XML) the type it plainly has.

    Leading zeros keep a value a string, since "007" is an identifier, not a number.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if _INTEGER.fullmatch(stripped):
        try:
            return int(stripped)
        except ValueError:  # Past Python's limit of digits, so it's no ordinary number.
            return text
    if _FLOAT.fullmatch(stripped):
        return float(stripped)
    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return text


HEADERLESS_NOTE = (
    "the first row looked like data rather than a header, so the columns are called column_N; "
    "if it is a header, list its names under keep_names or with --keep-name"
)
_CAMEL_CASE = re.compile(r"[a-z0-9][A-Z]")
_LOWERCASE_NAME = re.compile(r"[a-z][a-z0-9 .-]*")
_CELL_WORDS = re.compile(r"[a-z]+")
# Words that head columns. Capitalised words alone can't separate "Name,City" from "Jane,Portland".
_HEADER_WORDS = frozenset(
    ["id", "name", "first", "last", "full", "middle", "email", "mail", "phone", "mobile", "fax", "tel", "address", "street", "city", "town", "state", "province", "country", "region", "zip", "postal", "postcode", "date", "time", "year", "month", "day", "week", "quarter", "age", "dob", "birth", "gender", "sex", "title", "status", "type", "kind", "category", "class", "group", "code", "number", "num", "no", "key", "ref", "reference", "value", "amount", "total", "sum", "count", "qty", "quantity", "price", "cost", "rate", "tax", "fee", "balance", "currency", "salary", "revenue", "sales", "profit", "percent", "score", "rank", "level", "grade", "description", "desc", "details", "notes", "note", "comment", "comments", "text", "message", "subject", "summary", "body", "url", "link", "website", "user", "username", "login", "password", "customer", "client", "contact", "company", "organization", "organisation", "org", "department", "dept", "team", "employee", "manager", "owner", "author", "product", "item", "sku", "order", "invoice", "account", "payment", "transaction", "created", "updated", "modified", "deleted", "start", "end", "active", "enabled", "flag", "latitude", "longitude", "lat", "lon", "lng", "size", "length", "width", "height", "weight", "color", "colour", "language", "file", "path", "host", "ip", "version", "source", "target", "label", "tag", "field", "column", "row", "index", "position", "role", "location", "site", "place", "project", "task", "patient", "doctor", "provider", "diagnosis"]
)


def first_row_is_data(cells: Iterable[object], keep_names: Collection[str] = ()) -> bool:
    """Whether a table's first row is a record, not a header, so its cells must not become names.

    Nothing marks a header, so this looks for what headers have and data doesn't. A name the user
    confirmed, a snake_case, camelCase or lowercase name, or mostly words that head columns, make
    it a header. An email or a date makes it data, as does a row with none of those signs:
    `Jane,Doe,Oncology,Portland`. Mistaking a header costs some names; mistaking data shows it.
    """
    present = [cell for cell in cells if cell is not None and str(cell).strip()]
    if not present:
        return False
    texts = [cell for cell in present if isinstance(cell, str)]
    if any(cell in keep_names for cell in texts):
        return False
    if any(detect_format(cell.strip()) for cell in texts):
        return True
    if any("_" in cell or _CAMEL_CASE.search(cell) or _LOWERCASE_NAME.fullmatch(cell.strip()) for cell in texts):
        return False
    familiar = sum(any(word in _HEADER_WORDS for word in _CELL_WORDS.findall(cell.lower())) for cell in texts)
    return familiar * 2 < len(present)


def positional_names(count: int) -> list[str]:
    return [f"column_{position}" for position in range(1, count + 1)]


def column_names(header: Sequence[object]) -> list[str]:
    """Unique names for header cells. Empty ones get a positional name, never a value."""
    names: list[str] = []
    seen: set[str] = set()
    for position, cell in enumerate(header, start=1):
        # Not stripped: "name " has a space, which the privacy rules read as data. Trimming
        # it first would turn a hidden name into a plain one.
        name = "" if cell is None else str(cell)
        name = name if name.strip() else f"column_{position}"
        base, suffix = name, 2
        while name in seen:
            name, suffix = f"{base}_{suffix}", suffix + 1
        seen.add(name)
        names.append(name)
    return names
