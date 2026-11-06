"""Files at a URL are described exactly as the same file on disk. All data is synthetic.

The tests use fsspec's in-memory filesystem (`memory://`) for every format, and a small local web server for
HTTP. A URL can hold a password or a signed token, so none of its text may appear in a message.
"""

import asyncio
import functools
import http.server
import io
import json
import threading

import pytest

fsspec = pytest.importorskip("fsspec")

from datanyx import KeyPolicy, SourceError, extract, render  # noqa: E402
from datanyx.cli import main  # noqa: E402
from datanyx.sources import resolve_source  # noqa: E402

CANARY = "zqcanaryvalue"


@pytest.fixture
def store():
    """A folder in memory, and a way to put files in it. Returns (put, local_dir, url_for)."""
    filesystem = fsspec.filesystem("memory")
    filesystem.store.clear()

    def put(name: str, data: bytes) -> str:
        filesystem.pipe_file(f"/data/{name}", data)
        return f"memory://data/{name}"

    yield put
    filesystem.store.clear()


def same_schema(url: str, local, **options):
    policy = KeyPolicy(**options)
    remote = extract(url, policy=policy)
    on_disk = extract(str(local), policy=policy)
    assert render(remote, "json-schema") == render(on_disk, "json-schema")
    assert remote.notes == on_disk.notes
    return remote


def write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def both(store, tmp_path, name, data):
    return store(name, data), write(tmp_path, name, data)


def test_text_formats_match_the_same_file_on_disk(store, tmp_path):
    csv_rows = b"id,name,email\n" + b"".join(f"{i},n{i},a{i}@example.com\n".encode() for i in range(1, 30))
    cases = {
        "a.csv": csv_rows,
        "a.tsv": b"id\tv\n1\tx\n2\ty\n",
        "a.json": json.dumps([{"id": i, "tags": ["a"]} for i in range(5)]).encode(),
        "a.jsonl": b"\n".join(json.dumps({"id": i, "ok": True}).encode() for i in range(5)),
        "a.xml": b'<root a="1"><row id="2">text</row></root>',
        "a.hl7": b"MSH|^~\\&|APP|FAC|||20200101||ADT^A01|1|P|2.5\rPID|1||123||Doe^Jane||19700101|M\r",
        "a.fhir.json": json.dumps({"resourceType": "Patient", "id": "1", "birthDate": "1970-01-01"}).encode(),
    }
    for name, data in cases.items():
        url, local = both(store, tmp_path, name, data)
        same_schema(url, local)


def test_yaml_matches(store, tmp_path):
    pytest.importorskip("yaml")
    url, local = both(store, tmp_path, "a.yaml", b"id: 1\nitems:\n  - k: v\n")
    same_schema(url, local)


def test_parquet_orc_and_excel_match(store, tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    table = pa.table({"id": list(range(30)), "name": [f"n{i}" for i in range(30)]})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    url, local = both(store, tmp_path, "a.parquet", buffer.getvalue())
    schema = same_schema(url, local, scale_hints=True)
    assert schema.entities[0].scale == "fewer than 1,000 rows"

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.append(["id", "name"])
    book.active.append([1, "x"])
    out = io.BytesIO()
    book.save(out)
    url, local = both(store, tmp_path, "a.xlsx", out.getvalue())
    same_schema(url, local)


def test_avro_matches(store, tmp_path):
    fastavro = pytest.importorskip("fastavro")
    out = io.BytesIO()
    schema = {"type": "record", "name": "R", "fields": [{"name": "id", "type": "long"}, {"name": "v", "type": ["null", "string"]}]}
    fastavro.writer(out, schema, [{"id": 1, "v": None}])
    url, local = both(store, tmp_path, "a.avro", out.getvalue())
    same_schema(url, local)


def test_key_inference_works_on_a_url(store, tmp_path):
    rows = b"id,name\n" + b"".join(f"{i},n{i}\n".encode() for i in range(1, 40))
    url, local = both(store, tmp_path, "keys.csv", rows)
    schema = same_schema(url, local, infer_keys=True)
    assert "unique" in render(schema, "text")


def test_a_url_is_read_as_a_file_not_as_a_database():
    assert resolve_source("s3://bucket/path/data.parquet").kind == "parquet"
    assert resolve_source("https://example.com/export.csv?X-Amz-Signature=abc").kind == "csv"
    assert resolve_source("gs://bucket/a.jsonl").kind == "jsonl"
    assert resolve_source("postgresql://user@host/db").kind == "sql"  # A database URL is still one.


def test_the_url_can_come_from_the_environment(store, tmp_path, monkeypatch):
    store("e.csv", b"id,v\n1,x\n")
    monkeypatch.setenv("DN_REMOTE", "memory://data/e.csv")
    assert extract("env:DN_REMOTE").entities[0].node is not None
    assert render(extract("env:DN_REMOTE"), "text") == render(extract("memory://data/e.csv"), "text")


def test_formats_that_need_a_local_file_say_so(store):
    for name in ("a.duckdb", "a.dta", "a.sav", "a.sas7bdat"):
        url = store(name, b"x")
        with pytest.raises(SourceError, match="can't be read from a URL"):
            extract(url)


def test_a_missing_file_or_backend_gives_a_fixed_message(store, monkeypatch):
    with pytest.raises(SourceError, match=r"cannot read the file at that URL \(FileNotFoundError\)"):
        extract("memory://data/nothing.csv")
    monkeypatch.setitem(__import__("sys").modules, "s3fs", None)
    with pytest.raises(SourceError, match=r"datanyx\[s3\]"):
        extract("s3://bucket/a.csv")


def test_a_password_in_the_url_never_appears_in_a_message(capsys):
    url = f"http://user:{CANARY}@127.0.0.1:9/x.csv?token={CANARY}"  # Nothing listens on port 9.
    with pytest.raises(SourceError) as raised:
        extract(url)
    assert CANARY not in str(raised.value)
    assert main(["schema", url]) == 1
    seen = capsys.readouterr()
    assert CANARY not in seen.out + seen.err


def test_the_command_line_reads_a_url(store, capsys):
    url = store("c.csv", b"id,v\n1,x\n2,y\n")
    assert main(["schema", url, "--format", "text"]) == 0
    assert "id: integer" in capsys.readouterr().out
    assert main(["synth", url, "--rows", "3"]) == 0
    assert main(["schema", url, "--format", "summary"]) == 0


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # Keep the test output clean.
        pass


@pytest.fixture
def web(tmp_path):
    handler = functools.partial(_Quiet, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield tmp_path, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_https_style_urls_work_over_http(web):
    pytest.importorskip("aiohttp")
    folder, base = web
    (folder / "w.csv").write_text("id,name\n" + "".join(f"{i},n\n" for i in range(1, 6)), encoding="utf-8")
    (folder / "w.json").write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")
    assert render(extract(f"{base}/w.csv?sig=abc"), "text") == render(extract(str(folder / "w.csv")), "text")
    assert render(extract(f"{base}/w.json"), "text") == render(extract(str(folder / "w.json")), "text")
    with pytest.raises(SourceError, match="cannot read the file at that URL"):
        extract(f"{base}/missing.csv")


def test_the_mcp_server_fetches_a_url_only_when_the_owner_allows_it(store):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    url = store("m.csv", b"id,v\n1,x\n")

    def call(server):
        try:
            return asyncio.run(server.call_tool("describe_data", {"target": url})).content[0].text
        except Exception as error:  # The library's own exception types differ by version.
            return str(error)

    assert "id: integer" not in call(create_server(["."]))
    assert "id: integer" in call(create_server(["."], allow_urls=True))
