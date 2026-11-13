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


def test_every_tool_declares_all_four_hints(folder):
    async def hints():
        return [tool.annotations.model_dump(by_alias=True) for tool in await create_server([folder]).list_tools()]

    expected = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    declared = asyncio.run(hints())
    assert len(declared) == 6
    for hint in declared:
        assert {name: hint[name] for name in expected} == expected


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


def test_access_allows_only_the_env_names_it_was_given():
    assert Access(["."], allow_env=["DATABASE_URL"]).check("env:DATABASE_URL") == "env:DATABASE_URL"
    with pytest.raises(SourceError):
        Access(["."]).check("env:DATABASE_URL")


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


def test_every_tool_describes_data_with_the_owners_hints(tmp_path):
    """A schema saved from describe_data must compare as unchanged against the live file in schema_diff."""
    (tmp_path / "t.csv").write_text("id,v\n" + "".join(f"{i},x\n" for i in range(1, 30)), encoding="utf-8")
    server = create_server([tmp_path], scale_hints=True, infer_keys=True)
    saved = call(server, "describe_data", {"target": str(tmp_path / "t.csv"), "format": "json-schema"}).content[0].text
    assert "x-scale" in saved and "x-unique" in saved
    (tmp_path / "saved.json").write_text(saved.split("\n\nNotes:")[0], encoding="utf-8")
    diff = call(server, "schema_diff", {"old": str(tmp_path / "saved.json"), "new": str(tmp_path / "t.csv")}).content[0].text
    assert diff == "No differences."


def test_the_owners_names_to_keep_and_map_paths_reach_every_tool(tmp_path):
    """`keep_names` and `map_paths` in datanyx.toml must mean the same to the server as to `datanyx schema`."""
    import json

    (tmp_path / "d.json").write_text(json.dumps([{"Order Details": 1, "id": 2, "tags": {"red": 1, "blue": 2}}] * 3), encoding="utf-8")
    target = {"target": str(tmp_path / "d.json")}
    default = call(create_server([tmp_path]), "describe_data", target).content[0].text
    assert "Order Details" not in default and "blue" in default
    server = create_server([tmp_path], keep_names=["Order Details"], map_paths=["$[].tags"])
    described = call(server, "describe_data", target).content[0].text
    assert "Order Details" in described and "blue" not in described
    sample = call(server, "synthetic_sample", {**target, "rows": 3}).content[0].text
    assert "Order Details" in sample and '"red"' not in sample
    # What the agent adds can only hide more.
    assert "id" in call(server, "describe_data", {**target, "map_paths": ["$[]"]}).content[0].text.replace("Order Details", "")


def test_cli_passes_the_settings_files_names_to_the_server(tmp_path, monkeypatch):
    from datanyx import mcp_server
    from datanyx.cli import main

    (tmp_path / "datanyx.toml").write_text('[schema]\nkeep_names = ["Order Details"]\nmap_paths = ["$.users"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen = {}
    monkeypatch.setattr(mcp_server, "run", lambda *args, **kwargs: seen.update(kwargs))
    assert main(["mcp"]) == 0
    assert tuple(seen["keep_names"]) == ("Order Details",) and tuple(seen["map_paths"]) == ("$.users",)
