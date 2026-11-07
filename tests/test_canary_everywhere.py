"""One canary planted in every place a value could hide, across every source and every output.

The product promise is that no value reaches an agent. Per-feature tests can miss a new place, so this
one plants `CANARY` in values, in names that must stay hidden, in file and sheet names, in comments, in
defaults and in file metadata, for each format, and runs every way of getting output: the library, the
command line and the MCP tools. The canary must appear in none of it.

A name that looks like a plain identifier is shown by design, so canary names have a hidden shape (a space).
"""

import asyncio
import bz2
import datetime
import gzip
import json
import sqlite3

import pytest

from datanyx import KeyPolicy, extract, render, synthesize
from datanyx.cli import main
from datanyx.diff import diff_schemas, render_diff, snapshot
from datanyx.renderers import RENDERERS
from datanyx.synth import render_table

CANARY = "zqcanaryvalue"
ODD_NAME = f"{CANARY} name"  # A space makes a name look like data, so it is hidden.
ROWS = 25


def _csv(folder):
    path = folder / "plain.csv"
    lines = [f"id,{ODD_NAME},email"] + [f"{i},{CANARY}{i},{CANARY}{i}@example.com" for i in range(1, ROWS + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tsv(folder):
    path = folder / "plain.tsv"
    lines = [f"id\t{ODD_NAME}"] + [f"{i}\t{CANARY}{i}" for i in range(1, ROWS + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _json(folder):
    path = folder / "plain.json"
    rows = [{"id": i, ODD_NAME: f"{CANARY}{i}", f"{CANARY}{i}@x.com": {"v": CANARY}} for i in range(1, ROWS + 1)]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _jsonl(folder):
    path = folder / "plain.jsonl"
    path.write_text("\n".join(json.dumps({"id": i, ODD_NAME: CANARY, "tags": [CANARY]}) for i in range(1, ROWS + 1)), encoding="utf-8")
    return path


def _yaml(folder):
    yaml = pytest.importorskip("yaml")
    path = folder / "plain.yaml"
    path.write_text(yaml.safe_dump({"id": 1, ODD_NAME: CANARY, "items": [{"k": CANARY}]}), encoding="utf-8")
    return path


def _xml(folder):
    path = folder / "plain.xml"
    path.write_text(f'<root a="{CANARY}"><row id="{CANARY}">{CANARY}</row><!-- {CANARY} --></root>', encoding="utf-8")
    return path


def _xlsx(folder):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = ODD_NAME
    sheet.append(["id", ODD_NAME])
    for i in range(1, ROWS + 1):
        sheet.append([i, f"{CANARY}{i}"])
    sheet["A1"].comment = openpyxl.comments.Comment(CANARY, "someone")
    book.properties.title = CANARY
    book.properties.creator = CANARY
    path = folder / "book.xlsx"
    book.save(path)
    return path


def _parquet(folder):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    field = pa.field("name", pa.string(), metadata={"note": CANARY})
    table = pa.table({"id": list(range(ROWS)), "name": [f"{CANARY}{i}" for i in range(ROWS)], ODD_NAME: [CANARY] * ROWS})
    table = table.cast(pa.schema([pa.field("id", pa.int64()), field, pa.field(ODD_NAME, pa.string())]))
    table = table.replace_schema_metadata({"owner": CANARY, "pandas": json.dumps({"index_columns": [CANARY]})})
    path = folder / "cols.parquet"
    pq.write_table(table, path)
    return path


def _sqlite(folder):
    path = folder / "db.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(f'create table "{ODD_NAME}" (id integer primary key, label text default \'{CANARY}\' check (label != \'{CANARY}x\'))')
    connection.execute(f'create table notes (id integer primary key, "{ODD_NAME}" text, kind text check (kind in (\'{CANARY}\')))')
    connection.execute(f'insert into notes values (1, \'{CANARY}\', \'{CANARY}\')')
    connection.execute(f'create view v as select \'{CANARY}\' as c')
    connection.commit()
    connection.close()
    return path


def _duckdb(folder):
    duckdb = pytest.importorskip("duckdb")
    path = folder / "db.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(f"create table t (id integer, label varchar default '{CANARY}')")
    connection.execute(f"comment on column t.label is '{CANARY}'")
    connection.execute(f"comment on table t is '{CANARY}'")
    connection.execute(f"insert into t values (1, '{CANARY}')")
    connection.close()
    return path


def _hl7(folder):
    path = folder / "msg.hl7"
    path.write_text(f"MSH|^~\\&|APP|FAC|||20200101||ADT^A01|{CANARY}|P|2.5\rPID|1||{CANARY}||{CANARY}^{CANARY}||19700101|M\r", encoding="utf-8")
    return path


def _fhir(folder):
    path = folder / "patient.fhir.json"
    path.write_text(json.dumps({"resourceType": "Patient", "id": CANARY, "name": [{"family": CANARY, "given": [CANARY]}], "birthDate": "1970-01-01"}), encoding="utf-8")
    return path


def _arrow(folder):
    pa = pytest.importorskip("pyarrow")
    feather = pytest.importorskip("pyarrow.feather")
    table = pa.table({"id": list(range(ROWS)), ODD_NAME: [CANARY] * ROWS}).replace_schema_metadata({"owner": CANARY})
    path = folder / "cols.feather"
    feather.write_feather(table, str(path))
    return path


def _delta(folder):
    log = folder / "delta_table" / "_delta_log"
    log.mkdir(parents=True)
    schema = {"type": "struct", "fields": [
        {"name": "id", "type": "long", "nullable": False, "metadata": {"comment": CANARY}},
        {"name": ODD_NAME, "type": "string", "nullable": True, "metadata": {}},
    ]}
    lines = [
        {"metaData": {"id": "t", "name": CANARY, "schemaString": json.dumps(schema), "partitionColumns": [], "configuration": {"c": CANARY}}},
        {"add": {"path": f"{CANARY}/p.parquet", "partitionValues": {"x": CANARY}, "stats": json.dumps({"numRecords": 5})}},
    ]
    (log / f"{0:020d}.json").write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return folder / "delta_table"


def _iceberg(folder):
    meta = folder / "iceberg_table" / "metadata"
    meta.mkdir(parents=True)
    document = {"format-version": 2, "location": f"s3://{CANARY}/t", "properties": {"owner": CANARY}, "current-schema-id": 0,
                "schemas": [{"schema-id": 0, "fields": [{"id": 1, "name": "id", "required": True, "type": "long", "doc": CANARY},
                                                          {"id": 2, "name": ODD_NAME, "required": False, "type": "string"}]}],
                "current-snapshot-id": 1, "snapshots": [{"snapshot-id": 1, "summary": {"total-records": "9", "x": CANARY}}]}
    (meta / "v1.metadata.json").write_text(json.dumps(document), encoding="utf-8")
    return folder / "iceberg_table"


def _proto(folder):
    path = folder / "msg.proto"
    path.write_text(
        "\n".join(
            [
                f"// {CANARY}",
                'syntax = "proto3";',
                f"enum E {{ {CANARY.upper()} = 0; }}",
                f'message M {{ string a = 1 [default = "{CANARY}"]; E e = 2; /* {CANARY} */ }}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _ddl(folder):
    pytest.importorskip("sqlglot")
    path = folder / "schema.sql"
    path.write_text(
        "\n".join(
            [
                f"-- {CANARY}",
                f"CREATE TABLE t (id INT PRIMARY KEY, \"{ODD_NAME}\" TEXT DEFAULT '{CANARY}', k ENUM('{CANARY}'), CHECK (id <> 0));",
                f"INSERT INTO t VALUES (1, '{CANARY}');",
                "COPY t (id) FROM stdin;",
                CANARY,
                "\\.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _openapi(folder):
    path = folder / "api.json"
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": CANARY},
        "components": {"schemas": {"S": {"type": "object", "properties": {
            "a": {"type": "string", "example": CANARY, "default": CANARY, "enum": [CANARY], "description": CANARY},
            ODD_NAME: {"type": "string"},
        }}}},
    }), encoding="utf-8")
    return path


def _avro(folder):
    fastavro = pytest.importorskip("fastavro")
    # Avro field names must be identifiers, and an identifier is shown by design, so the canary rides in values, symbols,
    # docs, defaults and file metadata instead.
    schema = {
        "type": "record",
        "name": "r",
        "doc": CANARY,
        "fields": [
            {"name": "id", "type": "long", "doc": CANARY},
            {"name": "plan", "type": {"type": "enum", "name": "Plan", "symbols": [CANARY, "other"]}, "default": CANARY},
            {"name": "note", "type": "string", "default": CANARY},
            {"name": "tags", "type": {"type": "array", "items": "string"}},
        ],
    }
    path = folder / "t.avro"
    with open(path, "wb") as stream:
        fastavro.writer(stream, schema, [{"id": i, "plan": CANARY, "note": CANARY, "tags": [CANARY]} for i in range(ROWS)], metadata={"owner": CANARY})
    return path


def _orc(folder):
    pa = pytest.importorskip("pyarrow")
    orc = pytest.importorskip("pyarrow.orc")
    table = pa.table({"id": list(range(ROWS)), ODD_NAME: [CANARY] * ROWS, "tags": [[CANARY]] * ROWS})
    path = folder / "t.orc"
    try:
        orc.write_table(table, str(path))
    except pa.ArrowException as error:  # Windows builds of pyarrow have no time zone database.
        pytest.skip(f"this pyarrow cannot write ORC here ({type(error).__name__})")
    return path


def _frame():
    pd = pytest.importorskip("pandas")
    return pd.DataFrame(
        {
            "id": list(range(ROWS)),
            "name": [CANARY] * ROWS,
            "sex": [1, 2] * (ROWS // 2) + [1],
            "born": [datetime.date(2000, 1, 2)] * ROWS,
        }
    )


def _dta(folder):
    pyreadstat = pytest.importorskip("pyreadstat")
    path = folder / "t.dta"
    pyreadstat.write_dta(
        _frame(), str(path), variable_value_labels={"sex": {1: CANARY, 2: "f"}}, column_labels=["i", CANARY, CANARY, "b"], file_label=CANARY
    )
    return path


def _sav(folder):
    pyreadstat = pytest.importorskip("pyreadstat")
    path = folder / "t.sav"
    pyreadstat.write_sav(
        _frame(), str(path), variable_value_labels={"sex": {1: CANARY, 2: "f"}}, column_labels=["i", CANARY, CANARY, "b"], file_label=CANARY
    )
    return path


def _xpt(folder):
    pyreadstat = pytest.importorskip("pyreadstat")
    path = folder / "t.xpt"
    pyreadstat.write_xport(_frame()[["id", "name"]], str(path))
    return path


def _csv_gz(folder):
    path = folder / "zipped.csv.gz"
    lines = [f"id,{ODD_NAME}"] + [f"{i},{CANARY}{i}" for i in range(1, ROWS + 1)]
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    return path


def _jsonl_bz2(folder):
    path = folder / "zipped.jsonl.bz2"
    path.write_bytes(bz2.compress("\n".join(json.dumps({"id": i, ODD_NAME: CANARY, "tags": [CANARY]}) for i in range(ROWS)).encode()))
    return path


BUILDERS = [
    _csv, _tsv, _json, _jsonl, _yaml, _xml, _xlsx, _parquet, _sqlite, _duckdb, _hl7, _fhir, _arrow, _delta, _iceberg, _proto, _ddl, _openapi,
    _avro, _orc, _dta, _sav, _xpt, _csv_gz, _jsonl_bz2,
]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    """Every format in one folder, whose own name carries the canary in a shape that must be hidden."""
    root = tmp_path_factory.mktemp("canary") / f"{CANARY}_medical"
    root.mkdir()
    made = {}
    for build in BUILDERS:
        try:
            made[build.__name__.lstrip("_")] = build(root)
        except pytest.skip.Exception:
            continue
    assert len(made) >= 8
    return root, made


def _hint_policies():
    return [KeyPolicy(), KeyPolicy(scale_hints=True, infer_keys=True, column_hints=True)]


def test_library_output_never_holds_the_canary(data):
    root, made = data
    for target in [*map(str, made.values()), str(root)]:
        for policy in _hint_policies():
            schema = extract(target, policy=policy)
            outputs = [render(schema, name) for name in RENDERERS] + list(schema.notes)
            for table in synthesize(schema, rows=30):
                outputs += [render_table(table, kind) for kind in ("json", "jsonl", "csv")]
            joined = "\n".join(outputs)
            assert CANARY not in joined, target
    first, second = snapshot(str(root)), snapshot(str(root))
    assert CANARY not in render_diff(diff_schemas(first, second))


def test_command_line_output_never_holds_the_canary(data, capsys, tmp_path):
    root, made = data
    runs = []
    for target in [*map(str, made.values()), str(root)]:
        for name in RENDERERS:
            runs.append(["schema", target, "--format", name, "--scale-hints", "--infer-keys", "--column-hints"])
        runs.append(["synth", target, "--rows", "30"])
    runs += [["scan", str(root)], ["diff", str(root), str(root)]]
    for argv in runs:
        main(argv)  # Errors are fine; leaking isn't.
        seen = capsys.readouterr()
        assert CANARY not in seen.out + seen.err, argv


def test_mcp_tools_never_hold_the_canary(data, tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    root, made = data
    log = tmp_path / "audit.jsonl"
    server = create_server([root], audit_log=log, scale_hints=True, infer_keys=True, column_hints=True)

    def call(tool, arguments):
        async def run():
            return await server.call_tool(tool, arguments)

        try:
            result = asyncio.run(run())
        except Exception as error:  # Refusals are fine; what they say must be clean.
            return str(error)
        return "\n".join(getattr(block, "text", "") for block in result.content) if hasattr(result, "content") else str(result)

    collected = []
    for target in [*map(str, made.values()), str(root)]:
        collected.append(call("describe_data", {"target": target}))
        collected.append(call("describe_data", {"target": target, "format": "json-schema"}))
        collected.append(call("synthetic_sample", {"target": target, "rows": 20}))
        collected.append(call("check_code", {"code": "select nope from nowhere where id = 5", "target": target}))
        collected.append(call("schema_diff", {"old": target, "new": target}))
    collected.append(call("scan_folder", {"target": str(root)}))
    collected.append(call("scan_folder", {"target": str(root), "kinds": ["EMAIL"]}))
    assert CANARY not in "\n".join(collected)
    # The log names the paths the agent asked about, which it supplied itself; nothing else may carry the canary.
    recorded = log.read_text(encoding="utf-8")
    for target in [*map(str, made.values()), str(root)]:
        recorded = recorded.replace(json.dumps(target)[1:-1], "<path>")
    assert recorded and CANARY not in recorded


def test_the_probe_can_fail(data):
    """A control: if the owner chooses to show the odd name, the canary does appear, so the checks above can see it."""
    _root, made = data
    policy = KeyPolicy(keep_names=frozenset({ODD_NAME}))
    for name in ("csv", "parquet", "sqlite", "csv_gz", "jsonl_bz2", "orc"):
        if name in made:  # ORC can't be written by every pyarrow build, and a skipped builder is not in `made`.
            assert CANARY in render(extract(str(made[name]), policy=policy), "text"), name
    assert {"csv_gz", "jsonl_bz2"} <= set(made), "the compressed builders need nothing optional, so they must run"


# --- MongoDB is not a file, so it has its own fixture; the channels are the same ----------------------------


@pytest.fixture
def mongo_url(monkeypatch):
    mongomock = pytest.importorskip("mongomock")
    pytest.importorskip("pymongo")
    import bson

    from datanyx.sources import mongodb_source

    client = mongomock.MongoClient("mongodb://localhost/shop")
    database = client["shop"]
    database["users"].insert_many(
        [
            {
                "_id": bson.ObjectId(),
                "email": f"{CANARY}@example.com",
                "age": 31,
                "tags": [CANARY],
                "address": {"city": CANARY},
                ODD_NAME: CANARY,
            },
            {"_id": bson.ObjectId(), "email": None, "nick": CANARY},
        ]
    )
    database[ODD_NAME].insert_one({"x": CANARY})
    monkeypatch.setattr(mongodb_source, "_client", lambda url: client)
    monkeypatch.setenv("DN_CANARY_MONGO", f"mongodb://user:{CANARY}-pw@localhost/shop")
    return "env:DN_CANARY_MONGO"


def test_mongodb_never_holds_the_canary_in_any_channel(mongo_url, capsys, tmp_path):
    outputs = []
    for policy in _hint_policies():
        schema = extract(mongo_url, source="mongodb", policy=policy)
        outputs += [render(schema, name) for name in RENDERERS] + list(schema.notes)
        for table in synthesize(schema, rows=30):
            outputs += [render_table(table, kind) for kind in ("json", "jsonl", "csv")]
    assert CANARY not in "\n".join(outputs)

    for argv in (
        *[["schema", mongo_url, "--source", "mongodb", "--format", name, "--column-hints"] for name in RENDERERS],
        ["synth", mongo_url, "--source", "mongodb", "--rows", "30"],
    ):
        main(argv)
        seen = capsys.readouterr()
        assert CANARY not in seen.out + seen.err, argv


def test_mongodb_through_the_mcp_server_never_holds_the_canary(mongo_url, tmp_path):
    pytest.importorskip("mcp")
    from datanyx.mcp_server import create_server

    server = create_server([tmp_path], allow_env=["DN_CANARY_MONGO"], column_hints=True)

    def call(tool, arguments):
        try:
            result = asyncio.run(server.call_tool(tool, arguments))
        except Exception as error:  # Refusals are fine; what they say must be clean.
            return str(error)
        return "\n".join(getattr(block, "text", "") for block in result.content) if hasattr(result, "content") else str(result)

    collected = [
        call("describe_data", {"target": mongo_url}),
        call("synthetic_sample", {"target": mongo_url, "rows": 20}),
        call("check_code", {"code": "select nope from nowhere", "target": mongo_url}),
        call("schema_diff", {"old": mongo_url, "new": mongo_url}),
    ]
    assert CANARY not in "\n".join(collected)
    assert any("users" in text for text in collected), "the tools should have described the collections"
