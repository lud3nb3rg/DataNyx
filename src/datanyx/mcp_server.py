"""An MCP server that gives an agent value-free views of your data.

The agent can only get two things from it: a schema, and text with identifiers
replaced by placeholders. Anything that could reveal what is being hidden is left
out on purpose:

- No `keep` options. Asking for a name to be kept visible is a way of testing
  whether that name exists in the data.
- No restore, and no mapping file, since both hold the real values.
- Files must be inside the folders the server was started with, and database URLs
  are refused (use `env:NAME` so the password stays out of the conversation).
- Errors are reduced to fixed messages, because an exception can quote the data.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from pathlib import Path

from . import extract, render
from .check import LANGUAGES, format_diagnostics, load_tables
from .check import check_code as run_check
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .sources import SourceError
from .synth import render_table, synthesize
from .text import (
    DEFAULT_MODEL,
    ENTITY_KINDS,
    Detector,
    PatternDetector,
    SpacyDetector,
    anonymize,
)

INSTRUCTIONS = (
    "Describes the user's data without revealing it. `describe_data` returns a schema "
    "(types and field names, never values) for a file, folder or database. "
    "`synthetic_sample` returns fake rows that match that schema, for trying code out. "
    "`check_code` checks your SQL or pandas code against that schema before you run it. "
    "`anonymize_file` returns text with names and identifiers replaced by placeholders "
    "like <PERSON_1>; it can miss things, so treat it as a draft. Write code against the "
    "schema, and ask the user to run it, rather than asking for the data."
)
_ENV_PREFIX = "env:"
MAX_SAMPLE_ROWS = 200


class Access:
    """Which targets the agent may point the tools at."""

    def __init__(self, roots: Sequence[str | Path], allow_urls: bool = False) -> None:
        self.roots = [Path(root).resolve() for root in roots]
        self.allow_urls = allow_urls

    def check(self, target: str) -> str:
        if target.startswith(_ENV_PREFIX):
            return target
        if "://" in target:
            if self.allow_urls:
                return target
            raise SourceError("database URLs aren't accepted here; ask the user to set one as env:NAME")
        if target == "-":
            raise SourceError("stdin isn't available; pass a path")
        resolved = Path(target).resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            raise SourceError("that path is outside the folders this server may read")
        return str(resolved)


def _tool_error() -> type[Exception]:
    """The exception type whose message the MCP library passes on to the agent."""
    try:
        from mcp.server.mcpserver.exceptions import ToolError  # type: ignore[import-not-found]  # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]  # mcp 1.x
        except ImportError:
            return ValueError
    return ToolError


def _guard(function: Callable[..., str]) -> Callable[..., str]:
    """Let only fixed messages out: an exception's own text can quote the data."""

    refusal = _tool_error()

    @functools.wraps(function)
    def wrapper(*args, **kwargs) -> str:
        try:
            return function(*args, **kwargs)
        except SourceError as error:
            raise refusal(str(error)) from None
        except RecursionError:
            raise refusal("input is too deeply nested") from None
        except Exception as error:
            raise refusal(f"internal failure ({type(error).__name__})") from None

    return wrapper


def create_server(
    roots: Sequence[str | Path] = (".",),
    allow_urls: bool = False,
    model: str = DEFAULT_MODEL,
    keep_entity: Sequence[str] = (),
):
    """Build the server. `keep_entity` is the user's choice, set when starting it."""
    try:
        from mcp.server.mcpserver import MCPServer as Server  # type: ignore[import-not-found]  # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # type: ignore[no-redef,attr-defined]  # mcp 1.x
        except ImportError:
            raise SourceError("the MCP server needs the mcp package; install datanyx[mcp]") from None

    access = Access(roots, allow_urls)
    detectors: list[Detector] = []  # Loaded on first use: the model is slow to start.
    server = Server("datanyx", instructions=INSTRUCTIONS)

    @server.tool()
    @_guard
    def describe_data(
        target: str,
        format: str = "text",
        recursive: bool = False,
        map_paths: list[str] | None = None,
        database_schema: str | None = None,
    ) -> str:
        """Describe the structure of a file, folder or database, with no values.

        target: a path to a CSV, TSV, JSON, JSON Lines, YAML, XML, Excel, Parquet or
            SQLite file, a folder of those, or env:NAME naming an environment variable
            that holds a database URL.
        format: "text" (compact) or "json-schema".
        recursive: for a folder, include subfolders.
        map_paths: paths such as "$.users" whose keys should all be hidden.
        database_schema: a database schema other than the default.
        """
        if format not in RENDERERS:
            raise SourceError(f"format must be one of {sorted(RENDERERS)}")
        policy = KeyPolicy(map_paths=frozenset(map_paths or ()))
        schema = extract(
            access.check(target), policy=policy, namespace=database_schema, recursive=recursive
        )
        output = render(schema, format)
        if schema.notes:
            output += "\n\nNotes:\n" + "\n".join(f"- {note}" for note in schema.notes)
        return output

    @server.tool()
    @_guard
    def synthetic_sample(
        target: str,
        rows: int = 20,
        recursive: bool = False,
        map_paths: list[str] | None = None,
        database_schema: str | None = None,
    ) -> str:
        """Fake rows that match the schema of a file, folder or database, as JSON Lines.

        Use this to try out code and see realistic output. Nothing real is in it: it is
        generated from the schema alone. Several tables come back as sections, one per
        table, each headed by its name.
        """
        if not 1 <= rows <= MAX_SAMPLE_ROWS:
            raise SourceError(f"rows must be between 1 and {MAX_SAMPLE_ROWS}")
        policy = KeyPolicy(map_paths=frozenset(map_paths or ()))
        schema = extract(access.check(target), policy=policy, namespace=database_schema, recursive=recursive)
        tables = synthesize(schema, rows)
        if len(tables) == 1:
            return render_table(tables[0], "jsonl")
        sections = [f"-- {table.name or 'root'}" + chr(10) + render_table(table, "jsonl") for table in tables]
        return chr(10).join(sections)

    @server.tool()
    @_guard
    def check_code(
        code: str,
        target: str,
        language: str = "sql",
        dialect: str | None = None,
        table: str | None = None,
        database_schema: str | None = None,
    ) -> str:
        """Check your own SQL or pandas code against the schema of the data it will run on.

        Reports misspelled or unknown columns and tables, ambiguous joins and type mistakes,
        with line numbers and suggestions. Nothing is run. Hidden names are indistinguishable
        from names that don't exist, so this can't be used to find them.

        code: the SQL or Python source. language: "sql" or "python".
        target: a file, folder or env:NAME database, as for describe_data.
        table: the name your code uses for a single file, such as the table in FROM.
        """
        if language not in LANGUAGES:
            raise SourceError(f"language must be one of {list(LANGUAGES)}")
        tables, guessed = load_tables(
            [access.check(target)], [table] if table else [], namespace=database_schema
        )
        return format_diagnostics(run_check(code, tables, language, dialect or guessed))

    @server.tool()
    @_guard
    def anonymize_file(target: str, hide: list[str] | None = None) -> str:
        """Return a text file with names and other identifiers replaced by placeholders.

        This is best effort and can miss things. hide: extra terms to always replace.
        """
        if target.startswith(_ENV_PREFIX) or "://" in target:
            raise SourceError("pass a path to a text file")
        path = access.check(target)
        try:
            text = Path(path).read_bytes().decode("utf-8-sig")
        except OSError as error:
            raise SourceError(f"cannot read input: {error.strerror}") from None
        except UnicodeDecodeError:
            raise SourceError("input is not valid UTF-8") from None
        if not detectors:
            detectors.extend([PatternDetector(), SpacyDetector(model)])
        return anonymize(text, detectors, hide or (), (), keep_entity)

    return server


def run(
    roots: Sequence[str | Path],
    allow_urls: bool = False,
    model: str = DEFAULT_MODEL,
    keep_entity: Sequence[str] = (),
) -> None:
    unknown = set(keep_entity) - set(ENTITY_KINDS)
    if unknown:
        raise SourceError(f"unknown entity kinds: {sorted(unknown)}")
    create_server(roots, allow_urls, model, keep_entity).run("stdio")
