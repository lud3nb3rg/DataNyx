"""The MCP server: what a path or variable may reach, what each tool does, and the HTTP token."""

import asyncio

import pytest

from datanyx.cli import main
from datanyx.mcp_server import Access, BearerAuth, http_app
from datanyx.sources import SourceError
from datanyx.text import DictionaryDetector

# --- MCP: access ---------------------------------------------------------------------------------------------------


def test_an_allowed_variable_holding_a_sqlite_url_outside_the_roots_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("APP_DB", f"sqlite:///{(tmp_path / 'secrets.db').as_posix()}")
    with pytest.raises(SourceError, match="outside"):
        Access([root], allow_env=["APP_DB"]).check("env:APP_DB")
    monkeypatch.setenv("APP_DB", f"sqlite:///{(root / 'ok.db').as_posix()}")
    assert Access([root], allow_env=["APP_DB"]).check("env:APP_DB") == "env:APP_DB"


def test_a_sqlite_url_is_under_the_same_rules_as_a_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    access = Access([root], allow_urls=True)
    with pytest.raises(SourceError, match="outside"):
        access.check(f"sqlite:///{(tmp_path / 'secrets.db').as_posix()}")
    with pytest.raises(SourceError, match="outside"):
        access.check(f"sqlite+pysqlite:///{(tmp_path / 'secrets.db').as_posix()}")
    inside = f"sqlite:///{(root / 'ok.db').as_posix()}"
    assert access.check(inside) == inside
    assert access.check("sqlite:///:memory:") == "sqlite:///:memory:"
    assert access.check("postgresql://u:p@db/app") == "postgresql://u:p@db/app"


def test_a_mongodb_url_in_a_variable_goes_to_the_mongodb_source(monkeypatch):
    from datanyx.sources import resolve_source

    monkeypatch.setenv("MONGO_URL", "mongodb://host/db")
    monkeypatch.setenv("PG_URL", "postgresql://host/db")
    assert resolve_source("env:MONGO_URL").kind == "mongodb"
    assert resolve_source("env:PG_URL").kind == "sql"
    assert resolve_source("mongodb+srv://host/db").kind == "mongodb"


# --- MCP: tools ------------------------------------------------------------------------------------------------------


def call(server, tool, arguments):
    """Runs a tool through the MCP library, whichever version is installed."""
    if hasattr(server, "call_tool"):
        result = asyncio.run(server.call_tool(tool, arguments))
        content = getattr(result, "content", result)
        if isinstance(content, tuple):
            content = content[0]
        return "".join(getattr(item, "text", "") for item in content)
    raise AssertionError("no way to call a tool")


def test_the_anonymize_tool_takes_no_terms_from_the_agent(tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    (tmp_path / "t.txt").write_text("Jane Doe wrote this", encoding="utf-8")
    server = create_server([tmp_path], custom_detectors=[])
    tools = asyncio.run(server.list_tools())
    tools = getattr(tools, "tools", tools)
    tool = next(item for item in tools if item.name == "anonymize_file")
    schema = getattr(tool, "inputSchema", None) or tool.input_schema
    assert "hide" not in schema["properties"] and "keep" not in schema["properties"]


def test_the_anonymize_tool_hides_credentials_and_uses_every_model(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from datanyx import mcp_server

    made = []

    class FakeSpacy:
        uses_model = True

        def __init__(self, name):
            made.append(name)

        def find(self, text):
            return []

    monkeypatch.setattr(mcp_server, "SpacyDetector", FakeSpacy)
    (tmp_path / "t.txt").write_text('password = "correcthorse" a@corp.com', encoding="utf-8")
    server = mcp_server.create_server([tmp_path], model=["en_model", "fr_model"])
    out = call(server, "anonymize_file", {"target": str(tmp_path / "t.txt")})
    assert "correcthorse" not in out and "a@corp.com" not in out and made == ["en_model", "fr_model"]


def test_the_command_line_gives_the_server_every_model(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    seen = {}
    monkeypatch.setattr("datanyx.mcp_server.run", lambda *args, **kwargs: seen.update(args=args, kwargs=kwargs))
    monkeypatch.chdir(tmp_path)
    assert main(["--no-config", "mcp", "--model", "a,b"]) == 0
    assert seen["args"][2] == ["a", "b"]


def test_the_scan_tool_uses_what_the_server_was_started_with(tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    (tmp_path / "a.txt").write_text("client Acme", encoding="utf-8")
    (tmp_path / "skip.log").write_text("mail a.person@corp.io", encoding="utf-8")
    server = create_server(
        [tmp_path],
        custom_detectors=[DictionaryDetector("CLIENT", ["Acme"])],
        scan_settings={"excludes": ["*.log"]},
    )
    out = call(server, "scan_folder", {"target": str(tmp_path)})
    assert "CLIENT 1" in out and "EMAIL" not in out
    assert "CLIENT 1" in call(server, "scan_folder", {"target": str(tmp_path), "kinds": ["CLIENT"]})


# --- MCP: HTTP -------------------------------------------------------------------------------------------------------


def serve(app_scope, token="t" * 16, headers=()):
    sent, reached = [], []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def send(message):
        sent.append(message)

    asyncio.run(BearerAuth(app, token)({"type": app_scope, "headers": list(headers)}, None, send))
    return sent, reached


def test_only_startup_events_get_through_without_the_token():
    assert serve("lifespan")[1] == ["lifespan"]
    sent, reached = serve("http")
    assert reached == [] and sent[0]["status"] == 401
    sent, reached = serve("websocket")
    assert reached == [] and sent[0]["type"] == "websocket.close"
    assert serve("something-new")[1] == []


def test_the_token_lets_a_connection_through():
    good = [(b"authorization", b"Bearer " + b"t" * 16)]
    assert serve("http", headers=good)[1] == ["http"]
    assert serve("websocket", headers=good)[1] == ["websocket"]


def test_an_old_mcp_library_still_gets_the_host_and_security_settings():
    class Settings:
        host = "127.0.0.1"
        transport_security = None

    class OldServer:
        settings = Settings()

        def streamable_http_app(self, **options):
            if options:
                raise TypeError("no such option")
            return "app"

    pytest.importorskip("mcp")
    server = OldServer()
    app = http_app(server, "0.0.0.0", "t" * 16)
    assert app.app == "app" and server.settings.host == "0.0.0.0" and server.settings.transport_security is not None
