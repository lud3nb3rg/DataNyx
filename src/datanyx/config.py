"""Project settings: a policy a team can share and review, instead of command line flags.

`datanyx.toml` sits at the root of a project and holds what would otherwise be repeated
on every command: names to hide or keep, terms to hide in text, scan settings, custom
detectors. A command finds it by looking in the current folder and then each parent.

A second file, `datanyx.local.toml`, next to it is merged on top. It is for what
shouldn't be committed, above all terms to hide, which are the client and company names
you are trying to protect. Lists are joined, and single values in the local file win.
Command line options are added to the lists and override single values.

Mistakes are errors, not silent: an unknown setting is reported with a suggestion, since
a misspelled `hide` would otherwise protect nothing. Values from the file are never put in
an error message.
"""

from __future__ import annotations

import difflib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sources.base import SourceError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

CONFIG_NAME = "datanyx.toml"
LOCAL_NAME = "datanyx.local.toml"
ENVIRONMENT_VARIABLE = "DATANYX_CONFIG"

_LIST, _PATHS, _PATH, _STR, _BOOL, _NUMBER = "list of text", "list of paths", "path", "text", "true or false", "number"
SPEC: dict[str, dict[str, str]] = {
    "schema": {"map_paths": _LIST, "keep_names": _LIST, "public_columns": _LIST, "scale_hints": _BOOL, "infer_keys": _BOOL, "column_hints": _BOOL, "views": _BOOL, "max_rows": _NUMBER},
    "anonymize": {
        "model": _STR, "models": _LIST, "hide": _LIST, "keep": _LIST, "keep_entities": _LIST,
        "detectors": _PATHS, "packs": _LIST, "backends": _LIST,
    },
    "scrub": {"allow": _LIST, "names_from": _PATHS, "keep_numbers": _BOOL, "ner": _BOOL},
    "scan": {"exclude": _LIST, "kinds": _LIST, "max_size": _NUMBER, "include_examples": _BOOL, "ner": _BOOL},
    "mcp": {"roots": _PATHS, "allow_urls": _BOOL, "allow_env": _LIST, "audit_log": _PATH, "debug_log": _PATH},
}
DETECTOR_ARRAYS = ("pattern", "dictionary")


@dataclass(frozen=True)
class Config:
    """The merged settings. Everything is empty or None when there is no config file."""

    files: tuple[Path, ...] = ()
    map_paths: tuple[str, ...] = ()
    keep_names: tuple[str, ...] = ()
    public_columns: tuple[str, ...] = ()
    scale_hints: bool | None = None
    infer_keys: bool | None = None
    column_hints: bool | None = None
    views: bool | None = None
    max_rows: int | None = None
    model: str | None = None
    models: tuple[str, ...] = ()
    packs: tuple[str, ...] = ()
    backends: tuple[str, ...] = ()
    hide: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()
    keep_entities: tuple[str, ...] = ()
    detector_files: tuple[Path, ...] = ()
    detector_entries: tuple[dict[str, Any], ...] = ()
    scrub_allow: tuple[str, ...] = ()
    scrub_names_from: tuple[Path, ...] = ()
    scrub_keep_numbers: bool | None = None
    scrub_ner: bool | None = None
    scan_exclude: tuple[str, ...] = ()
    scan_kinds: tuple[str, ...] = ()
    scan_max_size: float | None = None
    scan_include_examples: bool | None = None
    scan_ner: bool | None = None
    mcp_roots: tuple[Path, ...] = ()
    mcp_allow_urls: bool | None = None
    mcp_allow_env: tuple[str, ...] = ()
    audit_log: Path | None = None
    debug_log: Path | None = None

    @property
    def found(self) -> bool:
        return bool(self.files)


def find_files(start: Path | None = None) -> list[Path]:
    """The config files that apply: the one named by DATANYX_CONFIG, or the nearest project's."""
    named = os.environ.get(ENVIRONMENT_VARIABLE)
    if named:
        return _with_local(Path(named))
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        base, local = folder / CONFIG_NAME, folder / LOCAL_NAME
        if base.is_file() or local.is_file():
            return [path for path in (base, local) if path.is_file()]
    return []


def load_config(explicit: str | Path | None = None, start: Path | None = None) -> Config:
    """Read and merge the config files. `explicit` names one; otherwise it is searched for."""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise SourceError(f"the config file {path.name} doesn't exist")
        files = _with_local(path)
    else:
        files = find_files(start)
    if not files:
        return Config()
    return _build(files, _merge([_read(path) for path in files]))


def _with_local(path: Path) -> list[Path]:
    local = path.with_name(LOCAL_NAME)
    return [path, local] if local.is_file() and local != path else [path]


# --- reading -------------------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise SourceError(f"cannot read {path.name}") from None
    except UnicodeDecodeError:
        raise SourceError(f"{path.name} isn't valid UTF-8") from None
    except tomllib.TOMLDecodeError as error:
        raise SourceError(f"{path.name} isn't valid TOML: {error}") from None

    allowed = [*SPEC, *DETECTOR_ARRAYS]
    result: dict[str, Any] = {}
    for section, content in raw.items():
        if section not in allowed:
            raise SourceError(f"{path.name}: unknown section [{section}]{_hint(section, allowed)}")
        if section in DETECTOR_ARRAYS:
            result[section] = _detector_entries(path, section, content)
        else:
            result[section] = _section(path, section, content)
    return result


def _section(path: Path, name: str, content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise SourceError(f"{path.name}: [{name}] must be a table")
    spec = SPEC[name]
    values: dict[str, Any] = {}
    for key, value in content.items():
        if key not in spec:
            raise SourceError(f"{path.name}: unknown setting '{key}' in [{name}]{_hint(key, list(spec))}")
        values[key] = _typed(path, name, key, spec[key], value)
    return values


def _typed(path: Path, section: str, key: str, kind: str, value: Any) -> Any:
    ok = {
        _LIST: lambda v: isinstance(v, list) and all(isinstance(item, str) for item in v),
        _PATHS: lambda v: isinstance(v, list) and all(isinstance(item, str) for item in v),
        _PATH: lambda v: isinstance(v, str),
        _STR: lambda v: isinstance(v, str),
        _BOOL: lambda v: isinstance(v, bool),
        _NUMBER: lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    }[kind](value)
    if not ok:
        raise SourceError(f"{path.name}: [{section}] {key} must be a {kind}")
    if kind == _PATHS:
        return [str(_resolve(path, item)) for item in value]
    if kind == _PATH:
        return str(_resolve(path, value))
    return value


def _detector_entries(path: Path, section: str, content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list) or not all(isinstance(item, dict) for item in content):
        raise SourceError(f"{path.name}: [[{section}]] entries must be tables")
    return [{**item, "_dir": str(path.parent), "_type": section} for item in content]


def _resolve(config: Path, text: str) -> Path:
    path = Path(os.path.expanduser(text))
    return path if path.is_absolute() else (config.parent / path).resolve()


def _hint(name: str, options: list[str]) -> str:
    close = difflib.get_close_matches(name, options, n=1, cutoff=0.6)
    return f"; did you mean '{close[0]}'?" if close else f"; the choices are {', '.join(options)}"


# --- merging -------------------------------------------------------------------------


def _merge(layers: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in layers:
        for section, content in layer.items():
            if section in DETECTOR_ARRAYS:
                merged.setdefault(section, []).extend(content)
                continue
            target = merged.setdefault(section, {})
            for key, value in content.items():
                if isinstance(value, list):
                    target[key] = list(dict.fromkeys([*target.get(key, []), *value]))
                else:
                    target[key] = value
    return merged


def _build(files: list[Path], data: dict[str, Any]) -> Config:
    def get(section: str, key: str, default: Any = None) -> Any:
        return data.get(section, {}).get(key, default)

    def paths(section: str, key: str) -> tuple[Path, ...]:
        return tuple(Path(item) for item in get(section, key, []))

    audit = get("mcp", "audit_log")
    debug = get("mcp", "debug_log")
    return Config(
        files=tuple(files),
        map_paths=tuple(get("schema", "map_paths", [])),
        keep_names=tuple(get("schema", "keep_names", [])),
        public_columns=tuple(get("schema", "public_columns", [])),
        scale_hints=get("schema", "scale_hints"),
        infer_keys=get("schema", "infer_keys"),
        column_hints=get("schema", "column_hints"),
        views=get("schema", "views"),
        max_rows=get("schema", "max_rows"),
        model=get("anonymize", "model"),
        models=tuple(get("anonymize", "models", [])),
        packs=tuple(get("anonymize", "packs", [])),
        backends=tuple(get("anonymize", "backends", [])),
        hide=tuple(get("anonymize", "hide", [])),
        keep=tuple(get("anonymize", "keep", [])),
        keep_entities=tuple(get("anonymize", "keep_entities", [])),
        detector_files=paths("anonymize", "detectors"),
        detector_entries=tuple(entry for name in DETECTOR_ARRAYS for entry in data.get(name, [])),
        scrub_allow=tuple(get("scrub", "allow", [])),
        scrub_names_from=paths("scrub", "names_from"),
        scrub_keep_numbers=get("scrub", "keep_numbers"),
        scrub_ner=get("scrub", "ner"),
        scan_exclude=tuple(get("scan", "exclude", [])),
        scan_kinds=tuple(get("scan", "kinds", [])),
        scan_max_size=get("scan", "max_size"),
        scan_include_examples=get("scan", "include_examples"),
        scan_ner=get("scan", "ner"),
        mcp_roots=paths("mcp", "roots"),
        mcp_allow_urls=get("mcp", "allow_urls"),
        mcp_allow_env=tuple(get("mcp", "allow_env", [])),
        audit_log=Path(audit) if audit else None,
        debug_log=Path(debug) if debug else None,
    )


# --- showing and starting a config -------------------------------------------------------


def describe(config: Config, reveal: bool = False) -> str:
    """The effective settings. Terms to hide are counted, not shown, unless `reveal`."""
    if not config.found:
        return "No datanyx.toml found here or in a parent folder. `datanyx init` creates one."
    lines = ["Using " + ", ".join(str(path) for path in config.files), ""]

    def show(label: str, values: Any, secret: bool = False) -> None:
        if values in ((), None, [], ""):
            return
        if isinstance(values, tuple):
            text = ", ".join(map(str, values)) if reveal or not secret else f"{len(values)} (hidden; use --reveal to show)"
        else:
            text = str(values)
        lines.append(f"  {label}: {text}")

    sections = (
        ("schema", (("map_paths", config.map_paths), ("keep_names", config.keep_names), ("public_columns", config.public_columns), ("scale_hints", config.scale_hints), ("infer_keys", config.infer_keys), ("column_hints", config.column_hints), ("views", config.views), ("max_rows", config.max_rows))),
        (
            "anonymize",
            (("model", config.model), ("models", config.models), ("packs", config.packs), ("backends", config.backends), ("hide", config.hide), ("keep", config.keep), ("keep_entities", config.keep_entities),
             ("detectors", config.detector_files)),
        ),
        (
            "scrub",
            (("allow", config.scrub_allow), ("names_from", config.scrub_names_from), ("keep_numbers", config.scrub_keep_numbers),
             ("ner", config.scrub_ner)),
        ),
        (
            "scan",
            (("exclude", config.scan_exclude), ("kinds", config.scan_kinds), ("max_size", config.scan_max_size),
             ("include_examples", config.scan_include_examples), ("ner", config.scan_ner)),
        ),
        ("mcp", (("roots", config.mcp_roots), ("allow_urls", config.mcp_allow_urls), ("allow_env", config.mcp_allow_env), ("audit_log", config.audit_log), ("debug_log", config.debug_log))),
    )
    secret_keys = {"hide", "keep"}
    for name, items in sections:
        shown = [(key, value) for key, value in items if value not in ((), None, [], "")]
        if shown:
            lines.append(f"[{name}]")
            for key, value in shown:
                show(key, value, secret=key in secret_keys)
    if config.detector_entries:
        patterns = sum(entry["_type"] == "pattern" for entry in config.detector_entries)
        dictionaries = len(config.detector_entries) - patterns
        lines.append(f"[custom detectors in the config] {patterns} pattern(s), {dictionaries} dictionary(ies)")
    return chr(10).join(lines)


STARTER = """# DataNyx project settings. Commands read this file from the project root.
# Command line options are added to these lists and override single values.

[schema]
# Hide every key or column at these paths, for plain-looking names the rules can't catch.
# map_paths = ["$.users", "$[].tags"]
# Show names the rules hid because they look like data.
# keep_names = ["Order Details", "report_2024"]
# Columns whose values are safe to show, such as a country, a status or a plan: their labels are listed (sorted, never
# counted) if there are few and they look like plain words. This is the one setting that shows values, so choose with care.
# public_columns = ["country", "status", "plan"]
# Size and key hints, both off by default because they say a little about the data:
# row counts as an order of magnitude, and yes/no flags for unique and joinable key columns.
# scale_hints = true
# infer_keys = true
# Coarse ranges for each column: distinct values, how often it is missing, how long its text gets.
# column_hints = true

# Also describe database views (names and columns; a view's definition is never read).
# views = true

# Read at most this many rows of a CSV, TSV, JSON Lines or Excel file, so a huge file is described in seconds.
# Sizes and key guesses are then left out for the files that were cut short.
# max_rows = 100000

[anonymize]
# model = "en_core_web_lg"
# Several models at once, to cover several languages: models = ["en_core_web_lg", "fr_core_news_md"]
# Packs of detectors for a field: packs = ["healthcare", "finance"]
# A second pass by another detector: backends = ["presidio"]  (see the documentation)
# keep_entities = ["DATE"]
# Files of custom detectors for your own formats (see the documentation).
# detectors = ["detectors/internal.toml"]
# Terms to hide go in datanyx.local.toml, which is not committed, so they stay private.

[scrub]
# Quoted names to leave readable in error messages.
# allow = ["plan"]
# Keep the names that these inputs show.
# names_from = ["data/customers.csv"]

[scan]
# exclude = ["fixtures", "*.lock"]
# max_size = 5

[mcp]
# roots = ["data"]
# Write a record of every tool call (never values) to this file.
# audit_log = "datanyx-audit.jsonl"
# Write where each unexpected failure happened (file, line, function; never a message or a value) to this file.
# debug_log = "datanyx-debug.jsonl"

# Custom detectors can also be written here:
# [[pattern]]
# kind = "EMPLOYEE_ID"
# regex = 'EMP-\\d{6}'
"""

STARTER_LOCAL = """# Settings that stay on this machine. Add this file to .gitignore.
# Terms to hide are the client and company names you are trying to protect, so
# don't commit them.

[anonymize]
# hide = ["Acme", "Northwind"]
# keep = ["Portland"]
"""
