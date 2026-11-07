"""Helpers shared by the commands: settings, policies, detectors and file writing."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from ..config import Config, load_config
from ..fileperms import restrict_to_owner
from ..privacy import KeyPolicy
from ..sources import SourceError, source_kinds
from ..sources.files import read_bytes
from ..text import (
    DEFAULT_MODEL,
    ENTITY_KINDS,
    SECRET,
    Anonymizer,
    Detector,
    PatternDetector,
    SecretDetector,
    SpacyDetector,
    build_detectors,
    kinds_of,
    load_detector_file,
)
from ..text.backends import build_backend
from ..text.mapping import MappingFile
from ..text.packs import PACKS, load_packs


class _Parser(argparse.ArgumentParser):
    """No abbreviated options: `--rev` meaning `--reveal` would slip past the guard's list of options."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    """The options that say what to read and what to hide, shared by `schema` and `synth`."""
    parser.add_argument(
        "input",
        help=(
            'file or folder to describe, "-" for stdin, a database URL, the URL of a file (s3://, gs://, az:// or '
            "https://), or env:NAME to read a URL from an environment variable (keeps passwords off the command line)"
        ),
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="when the input is a folder, also read its subfolders"
    )
    parser.add_argument("--schema", help="database schema to describe (default: the database's default)")
    parser.add_argument("--source", choices=source_kinds(), help="input type (default: detect)")
    _add_delimiter(parser)
    parser.add_argument(
        "--scale-hints", action="store_true",
        help="also give each table's size as an order of magnitude (\"10^3 to 10^4 rows\"), never a number",
    )
    parser.add_argument(
        "--infer-keys", action="store_true",
        help="also flag key-looking columns that are unique, and joins between tables, as yes/no guesses (CSV, TSV, JSON Lines, JSON and Excel)",
    )
    parser.add_argument("--views", action="store_true", help="also describe database views (names and columns only)")
    parser.add_argument(
        "--max-rows", type=int, metavar="N",
        help="read at most N rows of a CSV, TSV, JSON Lines or Excel file, so a huge file is described quickly; sizes and key guesses are then left out",
    )
    parser.add_argument(
        "--include", action="append", default=[], metavar="PATTERN",
        help="describe only tables (or files) whose names match this glob, such as 'sales_*' (repeatable)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help="leave out tables (or files) whose names match this glob (repeatable)",
    )
    parser.add_argument(
        "--max-tables", type=int, metavar="N",
        help="describe at most N tables and say how many were left out; use --format summary to list them all",
    )
    parser.add_argument(
        "--column-hints", action="store_true",
        help="also give each column coarse ranges: how many distinct values, how often it is missing, how long its text gets (CSV, TSV, JSON Lines, JSON and Excel)",
    )
    parser.add_argument(
        "--public-column", action="append", default=[], metavar="NAME",
        help=(
            "list the values of this column (repeatable), for a column whose labels are safe to show, such as a country or a status; "
            "NAME is in every table that has it, TABLE.NAME in one table only; "
            "sorted, never counted, and only if there are few and they look like plain words. The one option that shows values "
            "(CSV, TSV, JSON Lines, JSON and Excel)"
        ),
    )
    parser.add_argument(
        "--map-path",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            'hide all keys of the object at PATH, e.g. "$.users" or "$[].tags", '
            'or all columns of a table, e.g. "$.users" (repeatable)'
        ),
    )
    parser.add_argument(
        "--keep-name",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "show this exact key, table or column name even if it looks like data "
            "(repeatable); --map-path still hides it"
        ),
    )


def _add_model_and_detectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        action="append",
        metavar="MODEL",
        help=(
            f"spaCy NER model (default: the config's, else {DEFAULT_MODEL}). Repeat it, or separate with commas, "
            "to use several, such as one per language"
        ),
    )
    parser.add_argument(
        "--pack",
        action="append",
        default=[],
        metavar="NAME",
        help=f"a pack of detectors for a field: {', '.join(sorted(PACKS))} (repeatable)",
    )
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="SPEC",
        help="another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable)",
    )
    parser.add_argument(
        "--detectors",
        action="append",
        default=[],
        metavar="FILE",
        help="a file of custom detectors for your own formats and names (repeatable)",
    )


def _use_utf8() -> None:
    """Names can be non-ASCII, which legacy Windows encodings can't print."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _keep_line_endings() -> None:
    """For text passed through: Windows would otherwise turn each line ending into two characters."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="")


def _cfg(args: argparse.Namespace) -> Config:
    """The settings in effect, loaded once. `hook` never calls this: it must not depend on them."""
    if not hasattr(args, "_cfg"):
        args._cfg = Config() if args.no_config else load_config(args.config_file)
    return args._cfg


_DELIMITER_NAMES = {"comma": ",", "semicolon": ";", "tab": chr(9), "pipe": "|", "space": " "}


def _delimiter_arg(value: str) -> str:
    delimiter = _DELIMITER_NAMES.get(value.lower(), value)
    if len(delimiter) != 1 or delimiter in (chr(13), chr(10), chr(34)):
        raise argparse.ArgumentTypeError("use one character, such as ';' or '|', or a name: comma, semicolon, tab, pipe, space")
    return delimiter


def _add_delimiter(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--delimiter", type=_delimiter_arg, metavar="CHAR",
        help="the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV)",
    )


def _policy(args: argparse.Namespace) -> KeyPolicy:
    config = _cfg(args)
    return KeyPolicy(
        map_paths=frozenset([*config.map_paths, *getattr(args, "map_path", [])]),
        keep_names=frozenset([*config.keep_names, *getattr(args, "keep_name", [])]),
        scale_hints=bool(getattr(args, "scale_hints", False) or config.scale_hints),
        infer_keys=bool(getattr(args, "infer_keys", False) or config.infer_keys),
        column_hints=bool(getattr(args, "column_hints", False) or config.column_hints),
        public_columns=frozenset([*config.public_columns, *getattr(args, "public_column", [])]),
        delimiter=getattr(args, "delimiter", None),
        views=bool(getattr(args, "views", False) or config.views),
        include=tuple(getattr(args, "include", None) or ()),
        exclude=tuple(getattr(args, "exclude", None) or ()),
        max_tables=getattr(args, "max_tables", None),
        max_rows=getattr(args, "max_rows", None) or config.max_rows,
    )


def _models(args: argparse.Namespace) -> list[str]:
    """The spaCy models to use: the command line's, else the config's, else the default."""
    given = [name.strip() for item in (getattr(args, "model", None) or []) for name in item.split(",") if name.strip()]
    config = _cfg(args)
    return given or list(config.models) or ([config.model] if config.model else [DEFAULT_MODEL])


def _backend_detectors(args: argparse.Namespace) -> list[Detector]:
    return [build_backend(spec) for spec in [*_cfg(args).backends, *getattr(args, "backend", [])]]


def _ner_detectors(args: argparse.Namespace) -> list[Detector]:
    """Every model and backend that looks for names, places and the like."""
    return [*(SpacyDetector(name) for name in _models(args)), *_backend_detectors(args)]


def _custom_detectors(args: argparse.Namespace) -> list[Detector]:
    config = _cfg(args)
    detectors = build_detectors(config.detector_entries)
    for path in [*config.detector_files, *map(Path, args.detectors)]:
        detectors.extend(load_detector_file(Path(path)))
    detectors.extend(load_packs([*config.packs, *getattr(args, "pack", [])]))
    return detectors


def _write_new(path: str, text: str, private: bool = False) -> None:
    """Write a file that must not exist yet, so real data next to it can't be overwritten.

    `private` is for real data: the file is readable by its owner alone from the start."""
    try:
        with open(path, "x", encoding="utf-8", newline="", opener=_owner_only if private else None) as stream:
            if private and os.name == "nt" and not restrict_to_owner(path):
                raise SourceError("cannot make the output file private")
            stream.write(text)
    except FileExistsError:
        raise SourceError("the output file already exists; choose a new name") from None
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


def _owner_only(file: str, flags: int) -> int:
    return os.open(file, flags, 0o600)


def _anonymizer(args: argparse.Namespace) -> Anonymizer:
    return _anonymizer_factory(args)()


def _anonymizer_factory(args: argparse.Namespace, ner: bool = True) -> Callable[[], Anonymizer]:
    """Builds the detectors once, and returns a function that makes a fresh Anonymizer from them."""
    config = _cfg(args)
    custom = _custom_detectors(args)
    keep_entity = [*config.keep_entities, *getattr(args, "keep_entity", [])]
    allowed = set(ENTITY_KINDS) | {SECRET} | kinds_of(custom)
    unknown = [kind for kind in keep_entity if kind not in allowed]
    if unknown:
        raise SourceError(f"unknown entity kind {unknown[0]!r}; the choices are {sorted(allowed)}")
    detectors: list[Detector] = [*custom, PatternDetector(), SecretDetector(), *(_ner_detectors(args) if ner else [])]  # Custom wins ties.
    hide, keep = [*config.hide, *args.hide], [*config.keep, *getattr(args, "keep", [])]
    return lambda: Anonymizer(detectors, hide, keep, keep_entity)


def _check_paths_differ(*paths: str | None) -> None:
    """Refuse to overwrite an original, which couldn't be recovered afterwards."""
    real = [path for path in paths if path and path != "-"]
    if any(_same_file(a, b) for index, a in enumerate(real) for b in real[index + 1 :]):
        raise SourceError("the input, output and mapping must be different files")


def _write_text(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


def _write_mapping(path: str, mapping: dict[str, str]) -> None:
    mapping_file = MappingFile(path)
    try:
        mapping_file.write(mapping)
    finally:
        mapping_file.close()


def _write_bytes(path: str, data: bytes) -> None:
    try:
        with open(path, "wb") as stream:
            stream.write(data)
    except OSError as error:
        raise SourceError(f"cannot write the output: {error.strerror}") from None


def _read_text(target: str) -> str:
    # Read as bytes so that line endings pass through unchanged.
    data = read_bytes(target)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None


def _same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
