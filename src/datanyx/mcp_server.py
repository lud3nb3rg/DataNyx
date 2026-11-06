"""An MCP server that gives an agent value-free views of your data.

The agent can only get two things from it: a schema, and text with identifiers
replaced by placeholders. Anything that could reveal what is being hidden is left
out on purpose:

- No `keep` or `hide` options. Asking for a name to be kept visible, or to be hidden, is a
  way of testing whether that name exists in the data. What to hide is the user's choice,
  made when starting the server.
- No restore, and no mapping file, since both hold the real values.
- Files must be inside the folders the server was started with, and database URLs
  are refused. `env:NAME` works only for the variables the user named with `--allow-env`:
  otherwise it would reach any database or file the server's environment happens to mention.
- `anonymize_file` refuses key, mapping and credential files, whatever else it would hide.
- Errors are reduced to fixed messages, because an exception can quote the data.
"""

from __future__ import annotations

import fnmatch
import functools
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import extract, render
from .audit import AuditLog
from .check import LANGUAGES, format_diagnostics, load_tables
from .check import check_code as run_check
from .diff import diff_schemas, render_diff, snapshot
from .privacy import KeyPolicy
from .renderers import RENDERERS
from .scan import DEFAULT_KINDS, render_report, scan
from .sources import SourceError
from .synth import render_table, synthesize
from .text import (
    DEFAULT_MODEL,
    ENTITY_KINDS,
    SECRET,
    Detector,
    PatternDetector,
    SecretDetector,
    SpacyDetector,
    anonymize,
    kinds_of,
)
from .text.backends import BACKEND_KINDS

INSTRUCTIONS = (
    "Describes the user's data without revealing it. `describe_data` returns a schema "
    "(types and field names, never values) for a file, folder or database. "
    "`synthetic_sample` returns fake rows that match that schema, for trying code out. "
    "`check_code` checks your SQL or pandas code against that schema before you run it. "
    "`scan_folder` reports which files hold personal data or secrets, never the values. "
    "`schema_diff` compares two schemas and says what could break. "
    "`anonymize_file` returns text with names and identifiers replaced by placeholders "
    "like <PERSON_1>; it can miss things, so treat it as a draft. Write code against the "
    "schema, and ask the user to run it, rather than asking for the data."
)
_ENV_PREFIX = "env:"
MAX_SAMPLE_ROWS = 200
MAX_ANONYMIZE_BYTES = 5 * 1024 * 1024
# Files that exist to hold secrets or real values. Replacing what looks sensitive isn't enough for these.
_CREDENTIAL_FILES = (
    ".env", ".env.*", "*.key", "*.pem", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    "*.mapping.json", "datanyx.local.toml",
)
_MAPPING_ENTRY = re.compile(r'"<[A-Z_]+_\d+>"\s*:')


class Access:
    """Which targets the agent may point the tools at."""

    def __init__(self, roots: Sequence[str | Path], allow_urls: bool = False, allow_env: Sequence[str] = ()) -> None:
        self.roots = [Path(root).resolve() for root in roots]
        self.allow_urls = allow_urls
        self.allow_env = frozenset(allow_env)

    def check(self, target: str) -> str:
        if target.startswith(_ENV_PREFIX):
            return self._check_env(target)
        if "://" in target:
            if self.allow_urls:
                return target
            raise SourceError("database URLs aren't accepted here; ask the user to set one as env:NAME and allow it with --allow-env")
        if target == "-":
            raise SourceError("stdin isn't available; pass a path")
        return self._inside_roots(target)

    def _check_env(self, target: str) -> str:
        """One answer for a variable that isn't allowed and one that doesn't exist, so neither can be probed."""
        name = target[len(_ENV_PREFIX) :]
        if name not in self.allow_env:
            raise SourceError("that environment variable isn't available here; the user can allow it with --allow-env")
        value = os.environ.get(name, "")
        if value and "://" not in value:
            self._inside_roots(value)  # It holds a path, which the folders limit like any other.
        return target

    def _inside_roots(self, target: str) -> str:
        resolved = Path(target).resolve()
        if not self._inside_roots(resolved):
            raise SourceError(_OUTSIDE)
        return str(resolved)

    def _check_variable(self, name: str) -> None:
        value = os.environ.get(name, "")
        allowed = self._url_allowed(value) if "://" in value else bool(value) and self._inside_roots(Path(value).resolve())
        if not allowed:
            raise SourceError(_BAD_VARIABLE)

    def _url_allowed(self, url: str) -> bool:
        """A URL can name a file, which is under the same rules as a path."""
        if url.split("://", 1)[0].split("+", 1)[0].lower() not in _FILE_DATABASES:
            return True
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError

        try:
            database = make_url(url).database
        except (ArgumentError, ValueError):
            return False
        return not database or database == ":memory:" or self._inside_roots(Path(database).resolve())

    def _inside_roots(self, resolved: Path) -> bool:
        return any(resolved.is_relative_to(root) for root in self.roots)


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
    model: str | Sequence[str] = DEFAULT_MODEL,
    keep_entity: Sequence[str] = (),
    hide: Sequence[str] = (),
    custom_detectors: Sequence[Detector] = (),
    audit_log: str | Path | None = None,
    scale_hints: bool = False,
    infer_keys: bool = False,
    allow_env: Sequence[str] = (),
):
    """Build the server.

    `keep_entity`, `hide` and `custom_detectors` are the user's choices, made when starting
    it. `audit_log` is a file that gets a record of every tool call (never values).
    `scale_hints` and `infer_keys` add size buckets and key guesses to schemas; the agent can't switch
    them on or off. `allow_env` names the environment variables the agent may use as `env:NAME`.
    """
    try:
        from mcp.server.mcpserver import MCPServer as Server  # type: ignore[import-not-found]  # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # type: ignore[no-redef,attr-defined]  # mcp 1.x
        except ImportError:
            raise SourceError("the MCP server needs the mcp package; install datanyx[mcp]") from None

    access = Access(roots, allow_urls, allow_env)
    detectors: list[Detector] = []  # Loaded on first use: the model is slow to start.
    scan_options = dict(scan_settings or {})
    custom_kinds = kinds_of(list(custom_detectors))
    backend_kinds = BACKEND_KINDS if any(getattr(item, "uses_model", False) for item in custom_detectors) else frozenset()
    server = Server("datanyx", instructions=INSTRUCTIONS)
    audit = AuditLog(audit_log)
    hide_terms = list(hide)

    def tool(function: Callable[..., str]) -> Callable[..., str]:
        """Register a tool: refusals and failures become fixed messages, and every call is recorded."""
        guarded = _guard(function)

        @functools.wraps(function)
        def audited(*args: Any, **kwargs: Any) -> str:
            started = time.monotonic()
            try:
                result = guarded(*args, **kwargs)
            except Exception as error:
                audit.record(function.__name__, kwargs, "error", str(error), started, None)
                raise
            audit.record(function.__name__, kwargs, "ok", None, started, len(result))
            return result

        return server.tool()(audited)  # type: ignore[no-any-return]

    @tool
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
        policy = KeyPolicy(map_paths=frozenset(map_paths or ()), scale_hints=scale_hints, infer_keys=infer_keys)
        schema = extract(
            access.check(target), policy=policy, namespace=database_schema, recursive=recursive
        )
        output = render(schema, format)
        if schema.notes:
            output += "\n\nNotes:\n" + "\n".join(f"- {note}" for note in schema.notes)
        return output

    @tool
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
        policy = KeyPolicy(map_paths=frozenset(map_paths or ()), scale_hints=scale_hints, infer_keys=infer_keys)
        schema = extract(access.check(target), policy=policy, namespace=database_schema, recursive=recursive)
        tables = synthesize(schema, rows)
        if len(tables) == 1:
            return render_table(tables[0], "jsonl")
        sections = [f"-- {table.name or 'root'}" + chr(10) + render_table(table, "jsonl") for table in tables]
        return chr(10).join(sections)

    @tool
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

    @tool
    def scan_folder(target: str, kinds: list[str] | None = None) -> str:
        """Which files in a folder hold personal data or secrets, by kind and count, never by value.

        kinds: any of EMAIL, PHONE, CREDIT_CARD, IBAN, IP_ADDRESS, MAC_ADDRESS, ADDRESS, SECRET
        (the default), or others such as DATE and URL. File names that look like data are shown
        as file_N. Names of people and companies aren't found.
        """
        allowed = {*ENTITY_KINDS, SECRET, *custom_kinds, *backend_kinds}
        if kinds and not set(kinds) <= allowed:
            raise SourceError(f"kinds must be among {sorted(allowed)}")
        report = scan(
            access.check(target),
            detectors=[*custom_detectors, PatternDetector(), SecretDetector()],  # Custom first: it wins ties.
            kinds=kinds or {*DEFAULT_KINDS, *custom_kinds},
            backend_kinds=() if kinds else backend_kinds,
            **scan_options,
        )
        return render_report(report)

    @tool
    def schema_diff(old: str, new: str, database_schema: str | None = None) -> str:
        """Compare two schemas and list what was added, removed or changed, and what could break.

        old, new: a schema saved with `datanyx schema -o`, or any file, folder or env:NAME database.
        Changes are sorted into breaking, compatible and info.
        """
        first = snapshot(access.check(old), namespace=database_schema)
        second = snapshot(access.check(new), namespace=database_schema)
        return render_diff(diff_schemas(first, second))

    @tool
    def anonymize_file(target: str) -> str:
        """Return a text file with names, credentials and other identifiers replaced by placeholders.

        This is best effort and can miss things. Files that exist to hold secrets are refused.
        """
        if target.startswith(_ENV_PREFIX) or "://" in target:
            raise SourceError("pass a path to a text file")
        path = access.check(target)
        text = _read_for_anonymizing(Path(path))
        if not detectors:
            detectors.extend([*custom_detectors, PatternDetector(), SecretDetector(), SpacyDetector(model)])
        return anonymize(text, detectors, hide_terms, (), keep_entity)

    return server


def _read_for_anonymizing(path: Path) -> str:
    refusal = "that file holds credentials or real values, so it can't be read here; ask the user"
    if any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in _CREDENTIAL_FILES):
        raise SourceError(refusal)
    try:
        if path.stat().st_size > MAX_ANONYMIZE_BYTES:
            raise SourceError(f"that file is larger than {MAX_ANONYMIZE_BYTES // (1024 * 1024)} MB; ask the user to split it")
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None
    if _MAPPING_ENTRY.search(text):  # A mapping file by any other name.
        raise SourceError(refusal)
    return text


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class BearerAuth:
    """ASGI middleware: only requests that carry the shared token get through.

    The token is compared in constant time and never appears in a message. A request without it
    gets a bare 401, so an unauthenticated caller learns nothing about the server. Everything but
    the server's own startup and shutdown needs the token, so a new kind of connection can't skip it.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan" and not self._allowed(scope):
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 401, "headers": [(b"www-authenticate", b"Bearer"), (b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
            elif scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)

    def _allowed(self, scope) -> bool:
        import hmac

        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, supplied = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(supplied.strip(), self._token)
        return False


def http_app(server, host: str, token: str):
    """The server as an ASGI app that requires `token`."""
    if not token or len(token) < 16:
        raise SourceError("the HTTP server needs a token of at least 16 characters")
    options: dict = {}
    if host not in LOOPBACK:
        # The default only accepts a loopback Host header. A named host is protected by the token instead.
        try:
            from mcp.server.transport_security import TransportSecuritySettings

            options["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        except ImportError:
            pass
    try:
        app = server.streamable_http_app(host=host, **options)
    except TypeError:  # mcp 1.x: these are settings of the server
        settings = server.settings
        settings.host = host
        if "transport_security" in options:
            settings.transport_security = options["transport_security"]
        app = server.streamable_http_app()
    return BearerAuth(app, token)


def run(
    roots: Sequence[str | Path],
    allow_urls: bool = False,
    model: str | Sequence[str] = DEFAULT_MODEL,
    keep_entity: Sequence[str] = (),
    hide: Sequence[str] = (),
    detectors: Sequence[Detector] = (),
    audit_log: str | Path | None = None,
    http: tuple[str, int] | None = None,
    token: str | None = None,
    scale_hints: bool = False,
    infer_keys: bool = False,
    allow_env: Sequence[str] = (),
) -> None:
    allowed = {*ENTITY_KINDS, SECRET, *kinds_of(list(detectors))}
    unknown = set(keep_entity) - allowed
    if unknown:
        raise SourceError(f"unknown entity kinds: {sorted(unknown)}")
    server = create_server(roots, allow_urls, model, keep_entity, hide, detectors, audit_log, scale_hints, infer_keys, allow_env)
    if http is None:
        server.run("stdio")
        return
    host, port = http
    app = http_app(server, host, token or "")
    try:
        import uvicorn
    except ImportError:
        raise SourceError("the HTTP server needs uvicorn; install datanyx[mcp]") from None
    uvicorn.run(app, host=host, port=port, log_level="warning")
