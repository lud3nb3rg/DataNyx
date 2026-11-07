"""Each test here is a way past DataNyx's promises that once worked, kept so it can't come back: the MCP server's limits."""

import asyncio
import sqlite3

import pytest

from datanyx.mcp_server import Access, BearerAuth
from datanyx.sources import SourceError
from datanyx.text import PatternDetector, SecretDetector, anonymize

# --- the MCP server's limits ---------------------------------------------------------------


@pytest.fixture
def outside_database(tmp_path):
    path = tmp_path / "outside.db"
    connection = sqlite3.connect(path)
    connection.execute("create table payroll (employee text)")
    connection.commit()
    connection.close()
    return path


def test_env_targets_need_the_users_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_URL", "postgresql://h/db")
    access = Access([tmp_path])
    allowed = Access([tmp_path], allow_env=["SOME_URL"])
    assert allowed.check("env:SOME_URL") == "env:SOME_URL"
    with pytest.raises(SourceError, match="--allow-env"):
        access.check("env:SOME_URL")


def test_a_refused_variable_looks_the_same_whether_it_exists_or_not(tmp_path, monkeypatch):
    monkeypatch.setenv("REAL_ONE", "x")
    monkeypatch.delenv("NOT_THERE", raising=False)
    messages = []
    for name in ("REAL_ONE", "NOT_THERE"):
        with pytest.raises(SourceError) as error:
            Access([tmp_path]).check(f"env:{name}")
        messages.append(str(error.value))
    assert messages[0] == messages[1]


def test_an_allowed_variable_holding_a_path_stays_inside_the_roots(tmp_path, outside_database, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("A_PATH", str(outside_database))
    with pytest.raises(SourceError, match="outside"):
        Access([root], allow_env=["A_PATH"]).check("env:A_PATH")


def mcp_call(server, tool, arguments):
    async def run():
        return await server.call_tool(tool, arguments)

    return asyncio.run(run())


ENV_FILE = (
    "DB_PASSWORD=correcthorsebatterystaple\nAPI_KEY=abcdefghijklmnopqrstuvwx\nSTRIPE=" + "sk_live" + "_abcdefghijklmnopqrstuv\n"
    "GH=" + "ghp" + "_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAabcdefghij\n-----END RSA PRIVATE KEY-----\nAFTER=visible\n"
)


def test_credentials_are_hidden_by_the_text_tools():
    out = anonymize(ENV_FILE, [PatternDetector(), SecretDetector()])
    for secret in ("correcthorse", "abcdefghijklmnopqrstuvwx", "sk_live_", "ghp_", "MIIEpAIB", "BEGIN RSA"):
        assert secret not in out, secret
    assert "AFTER=visible" in out


def test_a_cut_off_private_key_is_hidden_to_the_end_of_the_text():
    text = "-----BEGIN PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n"
    assert "MIIEp" not in anonymize(text, [SecretDetector()])


@pytest.mark.parametrize("name", [".env", ".env.production", "server.key", "cert.pem", "notes.mapping.json", "datanyx.local.toml", "id_rsa", ".pgpass"])
def test_anonymize_file_refuses_files_that_exist_to_hold_secrets(tmp_path, name):
    from datanyx.mcp_server import _read_for_anonymizing

    path = tmp_path / name
    path.write_text("x=1\n", encoding="utf-8")
    with pytest.raises(SourceError, match="credentials"):
        _read_for_anonymizing(path)


def test_anonymize_file_refuses_a_mapping_by_any_name_and_a_huge_file(tmp_path, monkeypatch):
    from datanyx import mcp_server

    path = tmp_path / "harmless.json"
    path.write_text('{"<PERSON_1>": "Jane Doe"}', encoding="utf-8")
    with pytest.raises(SourceError, match="credentials"):
        mcp_server._read_for_anonymizing(path)
    path.write_text("plain text", encoding="utf-8")
    assert mcp_server._read_for_anonymizing(path) == "plain text"
    monkeypatch.setattr(mcp_server, "MAX_ANONYMIZE_BYTES", 3)
    with pytest.raises(SourceError, match="larger"):
        mcp_server._read_for_anonymizing(path)


def test_anonymize_file_offers_no_hide_option_to_test_guesses(tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    async def schema():
        tools = await create_server([tmp_path]).list_tools()
        return next(tool for tool in tools if tool.name == "anonymize_file")

    tool = asyncio.run(schema())
    parameters = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    assert list(parameters["properties"]) == ["target"]


def test_every_kind_of_connection_needs_the_token():
    reached = []
    sent = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def send(message):
        sent.append(message)

    auth = BearerAuth(app, "x" * 20)

    async def go():
        await auth({"type": "http", "headers": []}, None, send)
        await auth({"type": "websocket", "headers": []}, None, send)
        await auth({"type": "lifespan"}, None, send)
        await auth({"type": "websocket", "headers": [(b"authorization", b"Bearer " + b"x" * 20)]}, None, send)

    asyncio.run(go())
    assert reached == ["lifespan", "websocket"]
    assert sent[0]["status"] == 401 and sent[-1] == {"type": "websocket.close", "code": 1008}
