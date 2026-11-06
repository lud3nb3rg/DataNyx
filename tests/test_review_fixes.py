"""Regressions from the full code review: leaks, the MCP server, scan, crashes and wrong answers."""

import asyncio
import io
import json
import sqlite3
import zipfile

import pytest

from datanyx import extract, render, schema_of
from datanyx.audit import read_log, summarize
from datanyx.check import check_code
from datanyx.check.model import Column, Table
from datanyx.cli import main
from datanyx.evaluation import EvalReport, KindResult
from datanyx.mcp_server import Access, BearerAuth, http_app
from datanyx.privacy import KeyPolicy
from datanyx.scan import render_report, scan
from datanyx.scrub import ScrubPolicy, scrub
from datanyx.sources import SourceError
from datanyx.sources.files import coerce_text
from datanyx.text import Anonymizer, DictionaryDetector, PatternDetector, SecretDetector, anonymize
from datanyx.text.batch import anonymize_folder
from datanyx.text.detectors import PERSON, luhn_valid
from datanyx.text.documents import anonymize_bytes, restore_bytes
from datanyx.text.secrets import redact_url_credentials


def anonymizer(*names):
    return Anonymizer([DictionaryDetector(PERSON, list(names)), PatternDetector()])


# --- Names in notes -------------------------------------------------------------------------------------


def test_parquet_notes_dont_name_a_hidden_parent(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    inner = pa.struct([("bob@example.com", pa.string())])
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"alice@example.com": pa.array([{"bob@example.com": "x"}], type=inner)}), path)
    schema = extract(str(path))
    assert schema.notes and not any("@example.com" in note for note in schema.notes)
    assert "@example.com" not in render(schema, "text")


def test_avro_notes_dont_name_a_hidden_parent(tmp_path):
    fastavro = pytest.importorskip("fastavro")
    inner = {"type": "record", "name": "inner", "fields": [{"name": "field_a", "type": "string"}]}
    record = {"type": "record", "name": "outer", "fields": [{"name": "Long_Field_Name_" + "1234567", "type": inner}]}
    path = tmp_path / "t.avro"
    with open(path, "wb") as stream:
        fastavro.writer(stream, fastavro.parse_schema(record), [{"Long_Field_Name_1234567": {"field_a": "x"}}])
    schema = extract(str(path), policy=KeyPolicy(map_paths=frozenset({"$[].{*}"})))
    assert not any("Long_Field_Name" in note for note in schema.notes)
    assert any("{*}" in note for note in schema.notes)


def test_a_csv_with_no_header_doesnt_show_its_first_row(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("Jane,Doe,jane@corp.com\nBob,Roe,bob@corp.com\n", encoding="utf-8")
    schema = extract(str(path))
    text = render(schema, "text")
    assert "Jane" not in text and "Doe" not in text and "column_1" in text and any("first row" in note for note in schema.notes)


def test_a_header_with_plain_names_is_still_a_header(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("name,email\nJane,jane@corp.com\n", encoding="utf-8")
    text = schema_of(str(path), "text")
    assert "name" in text and "column_1" not in text


# --- HTML --------------------------------------------------------------------------------------------------


def html_out(page, *names):
    return anonymize_bytes(page.encode(), ".html", anonymizer(*names)).data.decode()


@pytest.mark.parametrize(
    "page",
    [
        "<!-- jane@corp.com --><p>x</p>",
        '<p data-email="jane@corp.com">x</p>',
        "<input value=jane@corp.com>",
        '<script>var u="jane@corp.com";</script>',
        "<script>var u='jane@corp.com';</script>",
        "<script>var u=`jane@corp.com`;</script>",
        "<script>// jane@corp.com\nvar x = 1;</script>",
        "<script>/* jane@corp.com */</script>",
        '<script type="application/ld+json">{"email": "jane@corp.com"}</script>',
        '<style>a { background: url("jane@corp.com") }</style>',
        '<a href="x" aria-description="jane@corp.com">y</a>',
        '<img src="/u/jane@corp.com/p.png">',
    ],
)
def test_html_has_nowhere_for_a_value_to_hide(page):
    assert "jane@corp.com" not in html_out(page)


def test_a_script_is_removed_and_a_stylesheet_keeps_its_rules():
    out = html_out('<script>var u = "jane@corp.com"; call();</script><style>.a { color: red; background: url(bob@corp.com) }</style>')
    assert "call()" not in out and "<script></script>" in out and ".a { color: red; background: url(" in out and "bob@corp.com" not in out


def test_nothing_changes_when_there_is_nothing_to_hide():
    page = '<style>.a { content: "b" } /* c */</style><input value=plain data-x="y"><p>Text &amp; more</p>'
    assert html_out(page) == page


def test_an_unquoted_attribute_is_escaped():
    assert "value=&lt;EMAIL_1&gt;>" in html_out("<input value=jane@corp.com>")


def test_html_restores_from_stylesheets_too():
    who = anonymizer()
    page = '<style>a { content: "bob@corp.com" }</style><p data-x="carl@corp.com">dan@corp.com</p>'
    done = anonymize_bytes(page.encode(), ".html", who).data
    assert restore_bytes(done, ".html", who.mapping).decode() == page


# --- Word ------------------------------------------------------------------------------------------------------


def docx(document, parts=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
        for name, content in (parts or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def part(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).decode()


def body(*paragraphs):
    return f'<w:document xmlns:w="w"><w:body>{"".join(paragraphs)}</w:body></w:document>'


def run(text):
    return f"<w:r><w:t>{text}</w:t></w:r>"


def test_text_after_a_text_box_is_anonymized():
    box = f"<w:txbxContent><w:p>{run('inner carl@corp.com')}</w:p></w:txbxContent>"
    document = body(f"<w:p>{run('before amy@corp.com')}{box}{run('after dan@corp.com')}</w:p>")
    xml = part(anonymize_bytes(docx(document), ".docx", anonymizer()).data, "word/document.xml")
    for real in ("amy@corp.com", "carl@corp.com", "dan@corp.com"):
        assert real not in xml
    assert "<w:txbxContent><w:p>" in xml and xml.count("</w:p>") == 2


def test_paragraphs_that_are_empty_or_self_closed_dont_confuse_it():
    document = body('<w:p w:rsidR="1"/>', f"<w:p>{run('amy@corp.com')}</w:p>", "<w:p></w:p>", f'<w:p w:rsidR="2">{run("bob@corp.com")}</w:p>')
    xml = part(anonymize_bytes(docx(document), ".docx", anonymizer()).data, "word/document.xml")
    assert "amy@corp.com" not in xml and "bob@corp.com" not in xml and '<w:p w:rsidR="1"/>' in xml


def test_a_paragraph_with_nothing_to_hide_is_untouched():
    document = body(f'<w:p>{run("plain")}{run(" words")}</w:p>')
    assert part(anonymize_bytes(docx(document), ".docx", anonymizer()).data, "word/document.xml") == document


def test_tracked_changes_lose_their_text_and_their_author():
    change = '<w:del w:author="jane@corp.com" w:initials="JD"><w:r><w:delText>deleted amy@corp.com</w:delText></w:r></w:del>'
    xml = part(anonymize_bytes(docx(body(f"<w:p>{run('x')}{change}</w:p>")), ".docx", anonymizer()).data, "word/document.xml")
    assert "amy@corp.com" not in xml and "jane@corp.com" not in xml and 'w:initials="JD"' not in xml
    assert "deleted" not in xml


def test_other_parts_lose_what_they_hold():
    data = docx(
        body(f"<w:p>{run('x')}</w:p>"),
        {
            "word/people.xml": '<w15:people><w15:person w15:author="jane@corp.com"><w15:presenceInfo w15:userId="jane@corp.com"/></w15:person></w15:people>',
            "docProps/custom.xml": "<Properties><property><vt:lpwstr>Client Jane Doe</vt:lpwstr></property></Properties>",
            "customXml/item1.xml": "<data><owner>Jane Doe</owner></data>",
            "word/_rels/document.xml.rels": '<Relationships><Relationship Id="1" Target="https://corp.example/jane@corp.com" TargetMode="External"/><Relationship Id="2" Target="media/image1.png"/></Relationships>',
        },
    )
    result = anonymize_bytes(data, ".docx", anonymizer())
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        everything = " ".join(archive.read(name).decode() for name in archive.namelist())
    assert "jane@corp.com" not in everything and "Jane Doe" not in everything
    assert 'Target="media/image1.png"' in everything


def test_alt_text_and_picture_names_are_anonymized():
    drawing = '<wp:docPr id="1" name="amy@corp.com.png" descr="Photo of bob@corp.com" title="carl@corp.com"/>'
    xml = part(anonymize_bytes(docx(body(f"<w:p>{run('x')}{drawing}</w:p>")), ".docx", anonymizer()).data, "word/document.xml")
    for real in ("amy@corp.com", "bob@corp.com", "carl@corp.com"):
        assert real not in xml


def test_a_damaged_docx_is_a_clear_error():
    with pytest.raises(SourceError, match="isn't readable text"):
        anonymize_bytes(docx(b"\xff\x00\xff"), ".docx", anonymizer())


# --- Names known from the structure of a document ------------------------------------------------------------


def test_a_whatsapp_sender_is_hidden_in_the_messages_too():
    chat = "\n".join(f"[01/02/2024, 10:0{i}:00] Jane Doe: message {i}" for i in range(3)) + "\n[01/02/2024, 10:05:00] Bob: Thanks Jane Doe, and Jane\n"
    out = anonymize_bytes(chat.encode(), ".txt", Anonymizer([])).data.decode()
    assert "Jane" not in out and "Doe" not in out and "Bob" not in out


def test_an_email_display_name_is_hidden_in_the_body():
    message = b"From: Jane Doe <jane@corp.com>\nTo: bob@corp.com\nSubject: Hi from Jane Doe\n\nRegards, Jane Doe\n"
    out = anonymize_bytes(message, ".eml", Anonymizer([])).data.decode()
    assert "Jane" not in out and "Doe" not in out


def test_a_slack_name_is_hidden_in_message_text():
    data = json.dumps([{"id": "U1", "real_name": "Jane Doe", "profile": {}}, {"ts": "1", "text": "ask Jane Doe", "user": "U1"}])
    out = anonymize_bytes(data.encode(), ".json", Anonymizer([])).data.decode()
    assert "Jane" not in out


def test_a_known_value_respects_what_the_user_kept():
    who = Anonymizer([], keep_terms=["Jane Doe"], keep_kinds=["EMAIL"])
    who.placeholder_for("PERSON", "Jane Doe")
    who.placeholder_for("EMAIL", "a@b.co")
    assert who.anonymize("Jane Doe wrote from a@b.co") == "Jane Doe wrote from a@b.co"


# --- Scrub --------------------------------------------------------------------------------------------------------


TRACEBACK = 'Traceback (most recent call last):\n  File "./clients/Acme/x.py", line 123456, in run\n    client = "Acme Corp"\nKeyError: \'k\'\n'


def test_terms_to_hide_reach_every_line_of_a_traceback():
    out = scrub(TRACEBACK, ScrubPolicy(hide_terms=("Acme",)))
    assert "Acme" not in out and "line 123456" in out


def test_patterns_reach_a_traceback_path_but_not_its_code():
    text = 'Traceback (most recent call last):\n  File "/data/jane@corp.com/x.py", line 3, in f\n    y = "bob@corp.com"\nValueError: bad\n'
    out = scrub(text)
    assert "jane@corp.com" not in out and 'y = "bob@corp.com"' in out and ", line 3, in f" in out


def test_an_indented_line_that_isnt_code_is_a_message():
    text = "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\n    code()\n  jane@corp.com was here\nValueError: bad\n"
    assert "jane@corp.com" not in scrub(text)


def test_carets_stay_in_a_traceback():
    text = 'Traceback (most recent call last):\n  File "x.py", line 1, in f\n    code()\n    ^^^^^^\nValueError: bad\n'
    assert "    ^^^^^^\n" in scrub(text)


@pytest.mark.parametrize("quote", ["'", '"'])
def test_a_long_quoted_value_is_removed(quote):
    assert "x" * 100 not in scrub(f"KeyError: {quote}{'x' * 5000}{quote}")


def test_a_longer_secret_is_replaced_before_a_shorter_one_inside_it(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "hunter22pw")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:hunter22pw@db.internal/app")
    out = scrub("failed: postgres://u:hunter22pw@db.internal/app", ScrubPolicy(detectors=()))
    assert "db.internal" not in out and "hunter22pw" not in out


def test_scrub_hides_credentials_by_default():
    assert "correcthorse" not in scrub("login with password=correcthorse please")


# --- Credentials ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("postgresql://u:p@ss@host/db", "p@ss"),
        ("postgresql://u:pw@host/db", "pw"),
        ("mongodb://tok3n@host/db", "tok3n"),
        ("mongodb+srv://u:pw@a.example,b.example/db", "pw"),
        ("postgresql://host/db?user=u&password=secret", "secret"),
        ("https://host/x?token=abc&y=1", "abc"),
        ("https://host/x?API_KEY=abc", "abc"),
    ],
)
def test_credentials_in_urls_are_found(text, secret):
    assert secret not in redact_url_credentials(text).replace("<CREDENTIALS>", "")


def test_addresses_stay_when_credentials_go():
    assert redact_url_credentials("postgresql://u:p@ss@host/db") == "postgresql://<CREDENTIALS>@host/db"
    assert redact_url_credentials("https://example.com/a@b") == "https://example.com/a@b"
    assert redact_url_credentials("see https://example.com/?page=2") == "see https://example.com/?page=2"


def test_the_audit_log_and_scrub_share_the_rule():
    assert "tok3n" not in json.dumps(summarize("describe_data", {"target": "mongodb://tok3n@host/db"}))
    assert "tok3n" not in scrub("mongodb://tok3n@host/db", ScrubPolicy(detectors=()))


def test_the_secret_detector_finds_a_password_holding_an_at_sign():
    spans = list(SecretDetector().find("postgresql://u:p@ss-word@host/db"))
    assert any("postgresql://u:p@ss-word@host/db"[s.start : s.end] == "p@ss-word" for s in spans)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("token=abcdefghij", True),
        ('csrf_token: "abcdefghij"', True),
        ("max_tokens=1000000000", False),
        ("token_type=bearer-long", False),
    ],
)
def test_the_secret_detector_on_tokens(text, found):
    assert bool(list(SecretDetector().find(text))) is found


def test_one_bad_line_doesnt_hide_the_whole_audit_log(tmp_path):
    log = tmp_path / "a.jsonl"
    log.write_text('{"time":"garbage"}\n{"time":"2025-01-31T09:30:00+00:00","tool":"x"}\n', encoding="utf-8")
    records, unreadable = read_log(log)
    assert len(records) == 1 and unreadable == 1


# --- Owner-only files --------------------------------------------------------------------------------------------


# --- Ollama --------------------------------------------------------------------------------------------------------


def test_ollama_ignores_proxy_settings(monkeypatch):
    from datanyx.text.backends import OllamaDetector

    seen = []

    class Opener:
        def __init__(self, handlers):
            self.handlers = handlers

        def open(self, request, timeout):
            seen.append(self.handlers)
            raise OSError("stop here")

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener(handlers))
    with pytest.raises(SourceError):
        list(OllamaDetector().find("some text"))
    assert seen[0][0].proxies == {}


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


# --- Scan ----------------------------------------------------------------------------------------------------------------


def test_utf16_files_are_read(tmp_path):
    for encoding in ("utf-16", "utf-32"):
        (tmp_path / f"{encoding}.csv").write_text("email\na.person@corp.io\n", encoding=encoding)
    report = scan(str(tmp_path))
    assert report.scanned == 2 and report.totals["EMAIL"] == 2 and report.binary == 0


def test_a_report_says_when_it_isnt_complete(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 2000, encoding="utf-8")
    report = scan(str(tmp_path), max_bytes=1000)
    assert report.clean and not report.complete
    text = render_report(report)
    assert "No personal data" not in text and "weren't" in text
    assert json.loads(render_report(report, "json"))["complete"] is False


def test_a_binary_file_that_isnt_data_doesnt_spoil_a_clean_result(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    report = scan(str(tmp_path))
    assert report.clean and report.complete and "No personal data" in render_report(report)


def test_ci_fails_on_what_it_couldnt_read(tmp_path, capsys):
    (tmp_path / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")
    assert main(["--no-config", "scan", str(tmp_path), "--fail-on-findings", "--max-size", "1"]) == 1
    (tmp_path / "big.txt").unlink()
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    assert main(["--no-config", "scan", str(tmp_path), "--fail-on-findings"]) == 0
    capsys.readouterr()


def test_an_unreadable_file_is_counted(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    real_open = open

    def refusing(path, *args, **kwargs):
        if str(path).endswith("a.txt"):
            raise PermissionError
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", refusing)
    report = scan(str(tmp_path))
    assert report.unscanned == ["a.txt"] and not report.complete


@pytest.mark.parametrize("name", ["x.duckdb", "x.ddb", "x.zsav", "x.por", "x.xpt"])
def test_data_formats_the_project_reads_are_flagged(tmp_path, name):
    (tmp_path / name).write_bytes(bytes(range(256)) * 4)
    assert scan(str(tmp_path)).not_scanned_data == [name]


def test_a_data_file_too_large_to_scan_is_listed(tmp_path):
    (tmp_path / "x.db").write_bytes(b"x" * 2000)
    report = scan(str(tmp_path), max_bytes=1000)
    assert report.not_scanned_data == ["x.db"] and report.too_large == 1


def test_excludes_apply_to_files_named_one_by_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fixtures").mkdir()
    for path in ("uv.lock", "fixtures/a.txt", "real.txt"):
        (tmp_path / path).write_text("a.person@corp.io", encoding="utf-8")
    report = scan(["uv.lock", "fixtures/a.txt", "real.txt"], excludes=["*.lock", "fixtures"])
    assert report.scanned == 1 and [item.display for item in report.findings] == ["real.txt"]
    assert scan("uv.lock", excludes=["*.lock"]).scanned == 0


def test_where_a_checkout_lives_is_no_reason_to_skip_a_file(tmp_path, monkeypatch):
    project = tmp_path / "build" / "proj"
    project.mkdir(parents=True)
    (project / "a.txt").write_text("a.person@corp.io", encoding="utf-8")
    monkeypatch.chdir(project)
    assert scan(str(project / "a.txt")).scanned == 1
    assert scan("a.txt").scanned == 1


def test_line_numbers_stay_right(tmp_path):
    (tmp_path / "a.txt").write_text("one\nsecond a@corp.io\n\nfourth b@corp.io\n", encoding="utf-8")
    assert scan(str(tmp_path)).findings[0].lines["EMAIL"] == [2, 4]


class FakeBackend:
    uses_model = True

    def find(self, text):
        from datanyx.text import Span

        at = text.find("123456789")
        return [Span(at, at + 9, "SSN")] if at >= 0 else []


def test_what_a_backend_reports_isnt_filtered_away(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("id 123456789 on 2024-01-02", encoding="utf-8")
    monkeypatch.setattr("datanyx.cli.scan._backend_detectors", lambda args: [FakeBackend()])
    assert main(["--no-config", "scan", str(tmp_path), "--format", "json"]) == 0
    totals = json.loads(capsys.readouterr().out)["totals"]
    assert totals["SSN"] == 1 and "DATE" not in totals


def test_a_backend_kind_is_reported_but_the_patterns_stay_quiet(tmp_path):
    from datanyx.text.backends import BACKEND_KINDS

    (tmp_path / "a.txt").write_text("id 123456789 on 2024-01-02", encoding="utf-8")
    report = scan(str(tmp_path), detectors=[PatternDetector(), FakeBackend()], kinds={"EMAIL"}, backend_kinds=BACKEND_KINDS)
    assert report.totals["SSN"] == 1 and report.totals["DATE"] == 0


def test_a_backend_kind_can_be_asked_for(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("id 123456789", encoding="utf-8")
    monkeypatch.setattr("datanyx.cli.scan._backend_detectors", lambda args: [FakeBackend()])
    assert main(["--no-config", "scan", str(tmp_path), "--kinds", "SSN", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["SSN"] == 1


# --- Anonymize ---------------------------------------------------------------------------------------------------------


def test_the_kept_span_filter_keeps_what_it_should():
    text = "on 2024-01-02 mail a@corp.io or 2024-02-03"
    out = anonymize(text, [PatternDetector()], keep_kinds=["DATE"])
    assert "2024-01-02" in out and "2024-02-03" in out and "a@corp.io" not in out


def test_the_kept_span_filter_with_overlapping_spans():
    from datanyx.text.anonymizer import _outside
    from datanyx.text.detectors import Span

    kept = [Span(0, 10, "A"), Span(2, 4, "B"), Span(20, 25, "C")]
    spans = [Span(3, 9, "x"), Span(5, 12, "y"), Span(21, 22, "z"), Span(26, 27, "w"), Span(0, 10, "A")]
    assert _outside(spans, kept) == [Span(5, 12, "y"), Span(26, 27, "w")]
    assert _outside(spans, []) == spans


def test_luhn_has_one_home():
    from datanyx.text import packs

    assert packs.luhn_valid is luhn_valid and luhn_valid("4539 1488 0343 6467") and not luhn_valid("1")


def test_the_command_line_hides_credentials(tmp_path, capsys):
    path = tmp_path / "c.txt"
    path.write_text("password = correcthorse\n", encoding="utf-8")
    assert main(["--no-config", "scrub", str(path), "--no-ner"]) == 0
    assert "correcthorse" not in capsys.readouterr().out


# --- Crashes --------------------------------------------------------------------------------------------------------------


def test_a_huge_number_in_a_cell_is_just_text():
    digits = "9" * 5000
    assert coerce_text(digits) == digits


def test_one_bad_file_doesnt_cost_a_folder(tmp_path, monkeypatch):
    (tmp_path / "good.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    from datanyx.sources import delimited_source

    original = delimited_source.DelimitedSource.extract

    def failing(self, target, policy, namespace=None):
        if target.endswith("bad.csv"):
            raise ValueError("quoting jane@corp.com")
        return original(self, target, policy, namespace)

    monkeypatch.setattr(delimited_source.DelimitedSource, "extract", failing)
    schema = extract(str(tmp_path))
    notes = " ".join(schema.notes)
    assert "good" in render(schema, "text") and "could not describe" in notes and "jane@corp.com" not in notes


def test_a_folder_survives_a_cell_past_the_digit_limit(tmp_path):
    (tmp_path / "wide.csv").write_text("a\n" + "9" * 5000 + "\n", encoding="utf-8")
    assert "a" in render(extract(str(tmp_path)), "text")


def test_a_date_that_doesnt_exist_in_yaml(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "x.yaml"
    path.write_text("when: 2024-13-45\n", encoding="utf-8")
    with pytest.raises(SourceError, match="invalid YAML"):
        extract(str(path))
    assert "could not describe" in " ".join(extract(str(tmp_path)).notes)  # And a folder goes on.


def test_a_recursive_yaml_alias_is_a_clear_error(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "x.yaml"
    path.write_text("a: &a [*a]\n", encoding="utf-8")
    with pytest.raises(SourceError, match="deeply nested"):
        extract(str(path))
    assert "could not describe" in " ".join(extract(str(tmp_path)).notes)


def test_deep_xml_is_a_clear_error(tmp_path):
    path = tmp_path / "x.xml"
    path.write_text("<a>" * 100_000 + "</a>" * 100_000, encoding="utf-8")
    with pytest.raises(SourceError, match="deeply nested"):
        extract(str(path))


def test_empty_input_to_synth_is_a_clear_error(tmp_path, capsys):
    sqlite3.connect(tmp_path / "e.db").close()
    assert main(["--no-config", "synth", str(tmp_path / "e.db")]) == 1
    assert "no tables to make fake data" in capsys.readouterr().err


def test_schema_wont_write_over_its_input(tmp_path, capsys):
    path = tmp_path / "c.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "-o", str(path)]) == 1
    assert path.read_text(encoding="utf-8") == '{"a": 1}' and "different files" in capsys.readouterr().err


def test_a_bad_schema_output_path_is_a_clear_error(tmp_path, capsys):
    path = tmp_path / "c.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "-o", str(tmp_path / "no" / "such" / "x")]) == 1
    assert "cannot write the output" in capsys.readouterr().err


def test_keep_names_doesnt_collide(tmp_path):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "a.txt").write_text("from a.person@corp.io", encoding="utf-8")
    (source / "a.eml").write_bytes(b"From: b.person@corp.io\n\nhello\n")
    result = anonymize_folder(str(source), str(target), Anonymizer([PatternDetector()]), keep_names=True)
    assert result.failed == [] and len(result.files) == 2 and len(list(target.iterdir())) == 2


def test_a_damaged_file_doesnt_end_a_folder(tmp_path):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "a.docx").write_bytes(docx(b"\xff\x00\xff"))
    (source / "b.txt").write_text("ok", encoding="utf-8")
    result = anonymize_folder(str(source), str(target), Anonymizer([PatternDetector()]))
    assert len(result.failed) == 1 and len(result.files) == 1


def test_an_unexpected_failure_is_reported_by_type_only(tmp_path, monkeypatch):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "a.txt").write_text("ok", encoding="utf-8")

    def failing(path, anonymizer):
        raise ValueError("quoting jane@corp.com")

    monkeypatch.setattr("datanyx.text.batch.anonymize_file", failing)
    result = anonymize_folder(str(source), str(target), Anonymizer([]))
    assert "ValueError" in result.failed[0] and "jane@corp.com" not in result.failed[0]


@pytest.mark.parametrize("native", ["union", "%8.0g", "timestamp[ms]"])
def test_other_sources_type_names_arent_sql(native):
    from datanyx.renderers import render_sql_ddl
    from datanyx.schema import Array, DataSchema, Entity, Field, Object, String

    node = Array(Object({"a": Field(String(), True, native)}))
    ddl = render_sql_ddl(DataSchema("avro", (Entity(None, node),)))
    assert native not in ddl
    sqlite3.connect(":memory:").executescript(ddl)


def test_a_database_keeps_its_own_types():
    from datanyx.renderers import render_sql_ddl
    from datanyx.schema import DataSchema, Entity, Field, Object, String

    node = Object({"a": Field(String(), True, "VARCHAR(255)")})
    for kind in ("sql", "duckdb"):
        assert "VARCHAR(255)" in render_sql_ddl(DataSchema(kind, (Entity("t", node),)))


def test_synth_makes_a_sqlite_file_from_avro(tmp_path, capsys):
    fastavro = pytest.importorskip("fastavro")
    record = {"type": "record", "name": "r", "fields": [{"name": "a", "type": ["null", "string"]}]}
    path = tmp_path / "t.avro"
    with open(path, "wb") as stream:
        fastavro.writer(stream, fastavro.parse_schema(record), [{"a": "x"}])
    out = tmp_path / "o.db"
    assert main(["--no-config", "synth", str(path), "--format", "sqlite", "-o", str(out), "--rows", "2"]) == 0
    assert out.exists()


# --- Sources --------------------------------------------------------------------------------------------------------------


def test_foreign_keys_a_server_denies_cost_only_that_note(tmp_path, monkeypatch):
    path = tmp_path / "s.db"
    connection = sqlite3.connect(path)
    connection.executescript("create table a (id integer primary key); create table b (id integer primary key)")
    connection.close()

    def denied(*args):
        raise RuntimeError("permission denied")

    monkeypatch.setattr("datanyx.sources.sql_source._read_foreign_keys", denied)
    schema = extract(str(path))
    assert {entity.name for entity in schema.entities} == {"a", "b"}
    assert sum("foreign keys" in note for note in schema.notes) == 2


@pytest.mark.parametrize(
    ("url", "argument"),
    [
        ("postgresql://h/d", "connect_timeout"),
        ("postgresql+pg8000://h/d", "timeout"),
        ("mysql://h/d", "connect_timeout"),
        ("mssql+pyodbc://h/d", "timeout"),
        ("oracle://h/d", "tcp_connect_timeout"),
    ],
)
def test_the_timeout_argument_is_the_drivers(monkeypatch, url, argument):
    from datanyx.sources import sql_source

    seen = {}
    monkeypatch.setattr(sql_source.sa, "create_engine", lambda url, **options: seen.update(options))
    sql_source._create_engine(url)
    assert argument in seen["connect_args"]


def test_an_unknown_driver_keeps_its_default_timeout(monkeypatch):
    from datanyx.sources import sql_source

    seen = {}
    monkeypatch.setattr(sql_source.sa, "create_engine", lambda url, **options: seen.update(options))
    sql_source._create_engine("oracle+cx_oracle://h/d")
    assert seen["connect_args"] == {}


def test_every_mllp_frame_is_read(tmp_path):
    frame = "\x0bMSH|^~\\&|A|B\rPID|1||123\r\x1c\r"
    other = "\x0bMSH#^~\\&#A#B\rOBX#1#X\r\x1c\r"
    path = tmp_path / "m.hl7"
    path.write_bytes((frame + other).encode())
    schema = extract(str(path))
    assert {entity.name for entity in schema.entities} == {"MSH", "PID", "OBX"}
    assert not any("skipped" in note for note in schema.notes)


def test_hl7_says_when_it_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("datanyx.sources.hl7_source._MAX_SEGMENT_TYPES", 1)
    path = tmp_path / "m.hl7"
    path.write_text("MSH|^~\\&|A\rPID|1\r", encoding="utf-8")
    assert any("left out 1" in note for note in extract(str(path)).notes)


def test_only_real_fhir_types_are_named(tmp_path):
    path = tmp_path / "x.fhir.ndjson"
    path.write_text('{"resourceType":"Patient","a":1}\n{"resourceType":"Whitfield","a":1}\n', encoding="utf-8")
    names = {entity.name for entity in extract(str(path)).entities}
    assert "Patient" in names and "Whitfield" not in names and any(name.startswith("Resource_") for name in names)


def test_fhir_says_when_it_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("datanyx.sources.fhir_source._MAX_TYPES", 1)
    path = tmp_path / "x.fhir.ndjson"
    path.write_text('{"resourceType":"Patient"}\n{"resourceType":"Observation"}\n', encoding="utf-8")
    assert any("left out 1" in note for note in extract(str(path)).notes)


def test_key_evidence_comes_from_a_streamed_file_too(tmp_path, monkeypatch):
    pytest.importorskip("ijson")
    monkeypatch.setattr("datanyx.sources.json_source.STREAM_THRESHOLD_BYTES", 1)
    path = tmp_path / "big.json"
    path.write_text(json.dumps([{"customer_id": i, "n": 1} for i in range(50)]), encoding="utf-8")
    schema = extract(str(path), policy=KeyPolicy(infer_keys=True))
    records = schema.entities[0].node.items
    assert records.fields["customer_id"].unique


def test_init_local_finds_the_repository_from_a_subfolder(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    assert main(["--no-config", "init", "--local"]) == 0
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines() == ["sub/datanyx.local.toml"]
    assert "to .gitignore" in capsys.readouterr().out


def test_init_local_outside_a_repository_writes_no_ignore_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A repository further up would count, so only check it doesn't appear here.
    assert main(["--no-config", "init", "--local"]) == 0
    assert not (tmp_path / ".gitignore").exists()


# --- Eval and checks -----------------------------------------------------------------------------------------------------


def test_a_kind_nobody_labelled_cant_pass_the_recall_gate(tmp_path, capsys):
    path = tmp_path / "e.json"
    path.write_text(json.dumps([{"text": "Hello Jane Doe", "sensitive": {"PERSON": ["Jane Doe"]}}]), encoding="utf-8")
    assert main(["--no-config", "eval", str(path), "--no-ner", "--kind", "PERSNO", "--min-recall", "0.9"]) == 1
    assert "nothing in the file is labelled PERSNO" in capsys.readouterr().err


def test_nothing_measured_is_no_pass():
    assert EvalReport().below(0.5) == ["overall"]
    assert EvalReport(kinds={"A": KindResult(total=0)}).below(0.5) == ["overall"]
    assert EvalReport(kinds={"A": KindResult(total=1, hidden=1)}).below(0.5) == []


def sql_warnings(sql, **columns):
    table = Table("t", {name: Column(kind) for name, kind in columns.items()})
    return [item.message for item in check_code(sql, [table], "sql")]


def test_a_time_column_takes_a_time():
    pytest.importorskip("sqlglot")
    for value in ("12:30:00", "12:30", "23:59:59.5", "9:15 PM"):
        assert sql_warnings(f"select * from t where at_time > '{value}'", at_time="time") == [], value
    assert any("isn't a time" in message for message in sql_warnings("select * from t where at_time > 'noon'", at_time="time"))


def test_a_date_column_still_takes_a_date():
    pytest.importorskip("sqlglot")
    assert sql_warnings("select * from t where d > '2024-01-31'", d="date") == []
    assert any("isn't a date" in message for message in sql_warnings("select * from t where d > '12:30:00'", d="date"))


def test_aggregates_over_distinct_are_type_checked():
    pytest.importorskip("sqlglot")
    assert any("SUM over string" in message for message in sql_warnings("select sum(distinct name) from t", name="string"))
    assert sql_warnings("select sum(distinct n) from t", n="integer") == []


PANDAS_TABLES = [
    Table("l", {"id": Column("integer"), "key": Column("string")}, aliases=frozenset({"l", "l.csv"})),
    Table("r", {"val": Column("integer")}, aliases=frozenset({"r", "r.csv"})),
]


def python_findings(code):
    return check_code(code, PANDAS_TABLES, "python")


def test_join_on_is_a_column_of_the_left_frame_only():
    code = 'import pandas as pd\nleft = pd.read_csv("l.csv")\nright = pd.read_csv("r.csv")\nx = left.join(right, on="{}")\n'
    assert python_findings(code.format("key")) == []
    assert any("nope" in item.message for item in python_findings(code.format("nope")))


def test_merge_on_is_still_checked_against_both():
    code = 'import pandas as pd\nleft = pd.read_csv("l.csv")\nright = pd.read_csv("r.csv")\nx = left.merge(right, on="key")\n'
    assert any("isn't in 'r'" in item.message for item in python_findings(code))


def test_attribute_checks_need_no_pandas(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pandas(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pandas)
    code = 'import pandas as pd\ndf = pd.read_csv("l.csv")\ndf.head()\nprint(df.shape, df.key, df.typo)\n'
    messages = [item.message for item in python_findings(code)]
    assert len(messages) == 1 and "typo" in messages[0]


def test_every_public_pandas_attribute_is_known():
    pandas = pytest.importorskip("pandas")
    from datanyx.check.python_check import _DATAFRAME_ATTRIBUTES

    missing = {name for name in dir(pandas.DataFrame) if not name.startswith("_")} - _DATAFRAME_ATTRIBUTES
    assert not missing


# --- One shared rule for hiding names in catalogs ------------------------------------------------------------------------


def test_sql_and_duckdb_hide_names_the_same_way(tmp_path):
    path = tmp_path / "s.db"
    connection = sqlite3.connect(path)
    connection.execute('create table t (id integer primary key, "alice@example.com" text)')
    connection.close()
    schema = extract(str(path))
    node = schema.entities[0].node
    assert "id" in node.fields and node.extra is not None and node.key_format == "email"
    assert any("hid data-like names at $.t (key shape)" in note for note in schema.notes)
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "s.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute('create table t (id integer primary key, "alice@example.com" varchar)')
    connection.close()
    schema = extract(str(path))
    node = schema.entities[0].node
    assert "id" in node.fields and node.extra is not None and node.key_format == "email"
    assert any("hid data-like names at $.t (key shape)" in note for note in schema.notes)


def test_output_is_utf8_from_one_place(tmp_path, capsysbinary):
    path = tmp_path / "c.csv"
    path.write_text("naïve_id\n1\n", encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "--format", "text"]) == 0
    assert "naïve_id".encode() in capsysbinary.readouterr().out
