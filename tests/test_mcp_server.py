"""The MCP server must expose schemas and anonymized text, and never data."""

import asyncio

import pytest

pytest.importorskip("mcp")

from datanyx import SourceError, anonymize_text, schema_of
from datanyx.mcp_server import Access, create_server

CANARY = "zq-canary-value"


def call(server, tool, arguments):
    async def run():
        return await server.call_tool(tool, arguments)

    return asyncio.run(run())


def error_text(server, tool, arguments):
    try:
        call(server, tool, arguments)
    except Exception as error:  # The library's own exception types differ by version.
        return str(error)
    raise AssertionError("expected the call to fail")


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "d.csv").write_text(f"a,b\n1,{CANARY}\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    return tmp_path


def test_lists_only_the_safe_tools(folder):
    async def names():
        return sorted(tool.name for tool in await create_server([folder]).list_tools())

    assert asyncio.run(names()) == ["anonymize_file", "check_code", "describe_data", "scan_folder", "schema_diff", "synthetic_sample"]


def test_describe_data_returns_schema_without_values(folder):
    result = call(create_server([folder]), "describe_data", {"target": str(folder / "d.csv")})
    text = result.content[0].text
    assert "a: integer" in text and CANARY not in text


def test_paths_outside_the_roots_are_refused(folder, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "x.csv"
    other.write_text("a\n1\n", encoding="utf-8")
    assert "outside" in error_text(create_server([folder]), "describe_data", {"target": str(other)})
    assert "outside" in error_text(create_server([folder]), "describe_data", {"target": str(folder / ".." / "x.csv")})


def test_urls_and_stdin_are_refused_by_default(folder):
    server = create_server([folder])
    assert "env:NAME" in error_text(server, "describe_data", {"target": "postgresql://u:secret@h/db"})
    assert "secret" not in error_text(server, "describe_data", {"target": "postgresql://u:secret@h/db"})
    assert "stdin" in error_text(server, "describe_data", {"target": "-"})


def test_errors_never_quote_the_data(folder):
    (folder / "bad.json").write_text(f"{{{CANARY}", encoding="utf-8")
    message = error_text(create_server([folder]), "describe_data", {"target": str(folder / "bad.json")})
    assert CANARY not in message and "invalid JSON" in message


def test_access_allows_env_names_that_hold_a_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.internal/app")
    assert Access(["."]).check("env:DATABASE_URL") == "env:DATABASE_URL"


# --- Library API -------------------------------------------------------------


def test_schema_of(folder):
    assert "a: integer" in schema_of(str(folder / "d.csv"), "text")
    assert "properties" in schema_of(str(folder / "d.csv"))


def test_anonymize_text_needs_the_model_or_fails_clearly():
    try:
        output = anonymize_text("Mail jane@example.com now", model="en_core_web_lg")
    except SourceError as error:
        assert "spaCy" in str(error) or "model" in str(error)
    else:
        assert "jane@example.com" not in output


# --- the HTTP transport ----------------------------------------------------------------------------------

TOKEN = "a-token-of-sixteen+chars"


def http_status(app, headers):
    """One request through the ASGI app; the status that came back."""
    seen = []

    async def go():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                seen.append(message["status"])

        scope = {"type": "http", "method": "GET", "path": "/nothing", "headers": headers, "query_string": b""}
        await app(scope, receive, send)

    asyncio.run(go())
    return seen[0]


def test_http_requires_the_token(folder):
    from datanyx.mcp_server import BearerAuth

    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerAuth(ok, TOKEN)
    assert http_status(app, []) == 401
    assert http_status(app, [(b"authorization", b"Bearer wrong-wrong-wrong-wrong")]) == 401
    assert http_status(app, [(b"authorization", f"Basic {TOKEN}".encode())]) == 401
    assert http_status(app, [(b"authorization", f"Bearer {TOKEN}".encode())]) == 204


def test_http_app_wraps_the_real_server_and_refuses_weak_tokens(folder):
    from datanyx.mcp_server import BearerAuth, http_app

    server = create_server([folder])
    assert isinstance(http_app(server, "127.0.0.1", TOKEN), BearerAuth)
    assert isinstance(http_app(server, "0.0.0.0", TOKEN), BearerAuth)
    for weak in ("", "short"):
        with pytest.raises(SourceError, match="token"):
            http_app(server, "127.0.0.1", weak)
    assert http_status(http_app(server, "127.0.0.1", TOKEN), []) == 401


def test_cli_http_needs_a_token_and_a_valid_address(monkeypatch, capsys):
    from datanyx.cli import main

    monkeypatch.delenv("DATANYX_MCP_TOKEN", raising=False)
    assert main(["--no-config", "mcp", "--http", "127.0.0.1:8808"]) == 1
    assert "DATANYX_MCP_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("DATANYX_MCP_TOKEN", TOKEN)
    assert main(["--no-config", "mcp", "--http", "nonsense"]) == 1
    assert "HOST:PORT" in capsys.readouterr().err
