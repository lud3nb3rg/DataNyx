"""Avro, ORC, DuckDB, SAS/Stata/SPSS, MongoDB, FHIR and HL7: right types, and no values in the output."""

import datetime
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from datanyx import SourceError, extract, render
from datanyx.cli import main
from datanyx.sources import resolve_source, source_kinds

CANARY = "zq-canary-value"
LABEL = "zq-label-value"
AVRO_LABEL = "zq_label_value"  # Avro symbols cannot hold a hyphen.


def schema_json(path, **options):
    return json.loads(render(extract(str(path), **options)))


def everything(path, **options):
    """Every output format at once, so a leak in any of them is caught."""
    schema = extract(str(path), **options)
    return "\n".join(render(schema, name) for name in ("json-schema", "text", "typescript", "pydantic", "sql"))


def row_props(schema):
    return schema["items"]["properties"]


def test_every_new_kind_is_registered():
    assert {"avro", "orc", "duckdb", "mongodb", "stata", "spss", "sas", "fhir", "hl7"} <= set(source_kinds())


# --- Avro ----------------------------------------------------------------------------------------------------


def test_avro_types_come_from_the_header(tmp_path):
    fastavro = pytest.importorskip("fastavro")
    schema = {
        "type": "record",
        "name": "User",
        "fields": [
            {"name": "id", "type": "long"},
            {"name": "email", "type": ["null", "string"]},
            {"name": "plan", "type": {"type": "enum", "name": "Plan", "symbols": [AVRO_LABEL, "free"]}},
            {"name": "born", "type": {"type": "int", "logicalType": "date"}},
            {"name": "seen", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            {"name": "score", "type": "double"},
            {"name": "tags", "type": {"type": "array", "items": "string"}},
            {"name": "attrs", "type": {"type": "map", "values": "string"}},
            {"name": "home", "type": {"type": "record", "name": "Home", "fields": [{"name": "city", "type": "string"}]}},
            {"name": "work", "type": ["null", "Home"]},
        ],
    }
    path = tmp_path / "u.avro"
    with open(path, "wb") as stream:
        fastavro.writer(
            stream,
            schema,
            [
                {
                    "id": 1,
                    "email": f"{CANARY}@example.com",
                    "plan": AVRO_LABEL,
                    "born": datetime.date(2000, 1, 2),
                    "seen": datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc),
                    "score": 1.5,
                    "tags": [CANARY],
                    "attrs": {CANARY: CANARY},
                    "home": {"city": CANARY},
                    "work": None,
                }
            ],
            metadata={"note": CANARY},
        )
    props = row_props(schema_json(path))
    assert props["id"]["type"] == "integer"
    assert set(props["email"]["type"]) == {"string", "null"}
    assert props["plan"]["type"] == "string"  # The symbols are values.
    assert (props["born"]["type"], props["born"]["format"]) == ("string", "date")
    assert props["seen"]["format"] == "date-time"
    assert props["tags"]["type"] == "array" and props["tags"]["items"]["type"] == "string"
    assert list(props["home"]["properties"]) == ["city"]
    assert "null" in str(props["work"])
    output = everything(path)
    assert CANARY not in output and AVRO_LABEL not in output


def test_avro_errors_are_fixed_messages(tmp_path):
    pytest.importorskip("fastavro")
    bad = tmp_path / "bad.avro"
    bad.write_text(CANARY, encoding="utf-8")
    with pytest.raises(SourceError, match="readable Avro") as caught:
        extract(str(bad))
    assert CANARY not in str(caught.value)
    with pytest.raises(SourceError, match="no such file"):
        extract(str(tmp_path / "missing.avro"))
    with pytest.raises(SourceError, match="stdin"):
        extract("-", source="avro")


# --- ORC -----------------------------------------------------------------------------------------------------


def test_orc_types_come_from_the_footer(tmp_path):
    pa = pytest.importorskip("pyarrow")
    orc = pytest.importorskip("pyarrow.orc")
    table = pa.table(
        {
            "id": pa.array([1], pa.int64()),
            "email": pa.array([f"{CANARY}@example.com"]),
            "score": pa.array([1.5]),
            "tags": pa.array([[CANARY]]),
            "when": pa.array([datetime.datetime(2024, 1, 2)], pa.timestamp("us")),
            "jane.doe@example.com": pa.array([CANARY]),
        }
    )
    path = tmp_path / "t.orc"
    try:
        orc.write_table(table, str(path))
    except pa.ArrowException as error:  # Windows builds of pyarrow have no time zone database.
        pytest.skip(f"this pyarrow cannot write ORC here ({type(error).__name__})")
    props = row_props(schema_json(path))
    assert "integer" in str(props["id"])
    assert "date-time" in str(props["when"])
    assert "array" in str(props["tags"])
    output = everything(path)
    assert CANARY not in output and "jane.doe" not in output
    with pytest.raises(SourceError, match="readable ORC"):
        bad = tmp_path / "bad.orc"
        bad.write_text(CANARY, encoding="utf-8")
        extract(str(bad))


# --- DuckDB --------------------------------------------------------------------------------------------------


@pytest.fixture
def duck(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "shop.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        f"create table users(id integer primary key, email varchar not null, mood enum('{LABEL}','sad'), "
        "tags varchar[], profile struct(zqfield int, b varchar), d decimal(10,2), made timestamp, u uuid, \"jane.doe@example.com\" varchar)"
    )
    con.execute("create table orders(id integer primary key, user_id integer references users(id), amount double, kind decimal(9,0))")
    con.execute("create table \"jane.doe@example.com\"(x integer)")
    con.execute(
        f"insert into users values (1, '{CANARY}@example.com', '{LABEL}', ['{CANARY}'], {{'zqfield': 1, 'b': '{CANARY}'}}, 1.5, now(), uuid(), '{CANARY}')"
    )
    con.close()
    return path


def test_duckdb_tables_keys_and_types(duck):
    schema = extract(str(duck))
    by_name = {entity.name: entity for entity in schema.entities}
    assert set(by_name) == {"users", "orders"}  # The data-like table name is hidden.
    users, orders = by_name["users"], by_name["orders"]
    assert users.primary_key == ("id",) and users.is_table
    assert [(fk.columns, fk.referenced_entity, fk.referenced_columns) for fk in orders.foreign_keys] == [(("user_id",), "users", ("id",))]
    fields = users.node.fields
    assert "jane.doe@example.com" not in fields and "email" in fields
    sql = render(schema, "sql")
    assert "CREATE TABLE" in sql
    props = schema_json(duck)
    assert props  # json-schema renders
    assert any("hid data-like" in note for note in schema.notes)


def test_duckdb_never_leaks_labels_values_or_nested_names(duck):
    output = everything(duck)
    for secret in (CANARY, LABEL, "zqfield", "jane.doe"):
        assert secret not in output, secret
    assert "ENUM" in output  # The type's name is kept, its labels are not.


def test_duckdb_cannot_be_asked_for_rows_or_files(duck, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    extract(str(duck))
    con = duckdb.connect(str(duck), read_only=True, config={"enable_external_access": False})
    try:
        with pytest.raises(Exception):
            con.execute("insert into users(id, email) values (2, 'x')")
        with pytest.raises(Exception):
            con.execute("select * from read_csv('anything.csv')")
    finally:
        con.close()
    bad = tmp_path / "bad.duckdb"
    bad.write_text(CANARY, encoding="utf-8")
    with pytest.raises(SourceError, match="could not open") as caught:
        extract(str(bad))
    assert CANARY not in str(caught.value)


def test_duckdb_map_path_hides_columns(duck):
    from datanyx.privacy import KeyPolicy

    schema = extract(str(duck), policy=KeyPolicy(map_paths=frozenset({"$.users"})))
    users = next(entity for entity in schema.entities if entity.name == "users")
    assert not users.node.fields and users.primary_key == ()


# --- Stata, SPSS and SAS -------------------------------------------------------------------------------------


@pytest.fixture
def frame():
    pd = pytest.importorskip("pandas")
    return pd.DataFrame(
        {
            "id": [1, 2],
            "name": [CANARY, "b"],
            "score": [1.5, 2.5],
            "born": [datetime.date(2000, 1, 2), datetime.date(2001, 2, 3)],
            "seen": [datetime.datetime(2024, 1, 2, 3, 4), datetime.datetime(2024, 1, 3, 3, 4)],
            "sex": [1, 2],
        }
    )


def test_stata_types_and_no_labels(tmp_path, frame):
    pyreadstat = pytest.importorskip("pyreadstat")
    path = tmp_path / "t.dta"
    pyreadstat.write_dta(frame, str(path), variable_value_labels={"sex": {1: LABEL, 2: "f"}}, column_labels=["i", CANARY, "s", "b", "t", "x"])
    props = row_props(schema_json(path))
    assert set(props) == {"id", "name", "score", "born", "seen", "sex"}
    assert "string" in str(props["name"]) and "date" in str(props["born"]) and "date-time" in str(props["seen"])
    output = everything(path)
    assert CANARY not in output and LABEL not in output


def test_spss_types_and_no_labels(tmp_path, frame):
    pyreadstat = pytest.importorskip("pyreadstat")
    path = tmp_path / "t.sav"
    pyreadstat.write_sav(frame, str(path), variable_value_labels={"sex": {1: LABEL, 2: "f"}}, column_labels=["i", CANARY, "s", "b", "t", "x"])
    props = row_props(schema_json(path))
    assert "date" in str(props["born"]) and "date-time" in str(props["seen"])
    output = everything(path)
    assert CANARY not in output and LABEL not in output


def test_sas_transport_file_and_unreadable_sas(tmp_path, frame):
    pyreadstat = pytest.importorskip("pyreadstat")
    path = tmp_path / "t.xpt"
    pyreadstat.write_xport(frame[["id", "name", "score"]], str(path))
    props = row_props(schema_json(path))
    assert set(props) == {"id", "name", "score"}
    assert CANARY not in everything(path)
    bad = tmp_path / "bad.sas7bdat"
    bad.write_bytes(CANARY.encode())
    with pytest.raises(SourceError, match="readable SAS") as caught:
        extract(str(bad))
    assert CANARY not in str(caught.value)
    with pytest.raises(SourceError, match="no such file"):
        extract(str(tmp_path / "gone.sas7bdat"))


def test_a_data_like_column_name_is_hidden_in_stats_files(tmp_path, frame):
    pyreadstat = pytest.importorskip("pyreadstat")
    renamed = frame.rename(columns={"name": "jane_doe"})
    path = tmp_path / "t.dta"
    pyreadstat.write_dta(renamed, str(path))
    assert "jane_doe" in everything(path)  # A plain identifier is shown.


# --- MongoDB -------------------------------------------------------------------------------------------------


@pytest.fixture
def mongo(monkeypatch):
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
                "joined": datetime.datetime(2024, 1, 2),
                "tags": [CANARY],
                "address": {"city": CANARY, "zip": "12345"},
                "jane.doe@example.com": CANARY,
            },
            {"_id": bson.ObjectId(), "email": None, "age": 40, "nick": CANARY},
        ]
    )
    database["empty"]
    database.create_collection("empty")
    database["jane.doe@example.com"].insert_one({"x": 1})
    seen = []
    monkeypatch.setattr(mongodb_source, "_client", lambda url: seen.append(url) or client)
    return seen


def test_mongodb_collections_become_entities(mongo):
    schema = extract("mongodb://user:pw-secret@localhost/shop")
    names = {entity.name for entity in schema.entities}
    assert names == {"users", "empty"}
    users = next(entity for entity in schema.entities if entity.name == "users")
    props = json.loads(render(schema))
    user_props = props["$defs"]["users"]["items"]["properties"]
    assert user_props["age"] == {"type": "integer"}
    assert user_props["joined"]["format"] == "date-time" or "date-time" in str(user_props["joined"])
    assert "_id" in user_props
    assert users.node is not None
    assert any("sample of up to" in note for note in schema.notes)


def test_mongodb_never_leaks_values_names_or_credentials(mongo):
    schema = extract("mongodb://user:pw-secret@localhost/shop")
    text = "\n".join(render(schema, name) for name in ("json-schema", "text", "typescript", "pydantic")) + "\n".join(schema.notes)
    for secret in (CANARY, "pw-secret", "jane.doe", "user:"):
        assert secret not in text, secret


def test_mongodb_selects_the_database_and_reads_the_url_from_the_environment(mongo, monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost/shop")
    assert {e.name for e in extract("env:MONGO_URL", source="mongodb").entities} == {"users", "empty"}
    assert mongo[-1] == "mongodb://localhost/shop"
    other = extract("mongodb://localhost", namespace="shop")
    assert {e.name for e in other.entities} == {"users", "empty"}


def test_mongodb_urls_are_not_taken_for_sql():
    assert resolve_source("mongodb://h/db").kind == "mongodb"
    assert resolve_source("mongodb+srv://h/db").kind == "mongodb"
    assert resolve_source("postgresql://h/db").kind == "sql"


def test_mongodb_errors_never_quote_the_url(monkeypatch):
    pymongo_errors = pytest.importorskip("pymongo.errors")
    from datanyx.sources import mongodb_source

    def refuse(url):
        raise pymongo_errors.ConfigurationError(f"bad url {url}")

    monkeypatch.setattr(mongodb_source, "_client", refuse)
    with pytest.raises(SourceError) as caught:
        extract("mongodb://user:pw-secret@h/db")
    assert "pw-secret" not in str(caught.value)
    with pytest.raises(SourceError, match="starts with mongodb"):
        extract("not-a-url", source="mongodb")


# --- FHIR ----------------------------------------------------------------------------------------------------


BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "p1", "name": [{"family": CANARY, "given": ["Jane"]}], "birthDate": "1980-01-02", "active": True}},
        {"resource": {"resourceType": "Patient", "id": "p2", "gender": "female"}},
        {"resource": {"resourceType": "Observation", "id": "o1", "valueQuantity": {"value": 72.5, "unit": "bpm"}, "subject": {"reference": "Patient/p1"}}},
        {"resource": {"resourceType": "jane.doe@example.com", "id": "x"}},
        {"fullUrl": "urn:uuid:0"},
    ],
}


def test_a_bundle_is_split_by_resource_type(tmp_path):
    path = tmp_path / "export.fhir.json"
    path.write_text(json.dumps(BUNDLE), encoding="utf-8")
    assert resolve_source(str(path)).kind == "fhir"
    schema = extract(str(path))
    assert [entity.name for entity in schema.entities] == ["Observation", "Patient"]
    assert any("skipped 1 object(s)" in note for note in schema.notes)  # The data-like resource type.
    patient = schema_json(path)["$defs"]["Patient"]["items"]["properties"]
    assert patient["birthDate"] == {"type": "string", "format": "date"} or "date" in str(patient["birthDate"])
    assert patient["active"] == {"type": "boolean"} or "boolean" in str(patient["active"])
    observation = schema_json(path)["$defs"]["Observation"]["items"]["properties"]
    assert observation["valueQuantity"]["properties"]["value"]["type"] == "number"
    output = everything(path)
    assert CANARY not in output and "jane.doe" not in output


def test_fhir_bulk_ndjson_and_single_resources(tmp_path):
    lines = tmp_path / "all.fhir.ndjson"
    lines.write_text("\n".join(json.dumps(entry["resource"]) for entry in BUNDLE["entry"][:3]) + "\n", encoding="utf-8")
    assert {e.name for e in extract(str(lines)).entities} == {"Patient", "Observation"}
    single = tmp_path / "one.json"
    single.write_text(json.dumps({"resourceType": "Patient", "id": "p"}), encoding="utf-8")
    assert [e.name for e in extract(str(single), source="fhir").entities] == ["Patient"]
    nothing = tmp_path / "none.json"
    nothing.write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(SourceError, match="no FHIR resources"):
        extract(str(nothing), source="fhir")
    broken = tmp_path / "broken.json"
    broken.write_text(CANARY, encoding="utf-8")
    with pytest.raises(SourceError, match="valid JSON") as caught:
        extract(str(broken), source="fhir")
    assert CANARY not in str(caught.value)


# --- HL7 -----------------------------------------------------------------------------------------------------


HL7 = "\r".join(
    [
        "MSH|^~\\&|APP|FAC|||20240102101500||ADT^A01|MSG0001|P|2.5",
        f"PID|1||12345^^^MRN||{CANARY.upper()}^JANE||19800101|F|||1 Main St^^Town^ST^12345||555-0100",
        "OBX|1|NM|HR||72|bpm|||||F",
        "OBX|2|ST|NOTE||first~second||||||F",
        "",
    ]
)


def test_hl7_segments_become_entities_with_positional_fields(tmp_path):
    path = tmp_path / "msg.hl7"
    path.write_text(HL7, encoding="utf-8")
    schema = extract(str(path))
    assert [entity.name for entity in schema.entities] == ["MSH", "PID", "OBX"]
    props = schema_json(path)["$defs"]
    pid = props["PID"]["items"]["properties"]
    assert pid["PID-7"] == {"type": "string", "format": "date"}
    assert set(pid["PID-5"]["properties"]) == {"c1", "c2"}
    obx = props["OBX"]["items"]
    assert "OBX-6" not in obx["required"]  # Absent from one of the two segments.
    assert "array" in str(obx["properties"]["OBX-5"])  # A repeating field.
    assert props["MSH"]["items"]["properties"]["MSH-7"]["format"] == "date-time"
    assert "MSH-1" not in props["MSH"]["items"]["properties"]  # The separators themselves.
    output = everything(path)
    assert CANARY.upper() not in output and "Main St" not in output and "555-0100" not in output


def test_hl7_accepts_newlines_mllp_and_rejects_other_text(tmp_path):
    framed = tmp_path / "framed.hl7"
    framed.write_bytes(b"\x0b" + HL7.replace("\r", "\n").encode() + b"\x1c\r")
    assert [e.name for e in extract(str(framed)).entities] == ["MSH", "PID", "OBX"]
    other = tmp_path / "other.hl7"
    other.write_text(CANARY, encoding="utf-8")
    with pytest.raises(SourceError, match="isn't an HL7") as caught:
        extract(str(other))
    assert CANARY not in str(caught.value)


# --- through the command line and folders ---------------------------------------------------------------------


def test_cli_describes_the_new_formats_and_folders_mix_them(tmp_path, capsys):
    (tmp_path / "msg.hl7").write_text(HL7, encoding="utf-8")
    (tmp_path / "export.fhir.json").write_text(json.dumps(BUNDLE), encoding="utf-8")
    assert main(["schema", str(tmp_path / "msg.hl7"), "--format", "text"]) == 0
    assert "PID-5" in capsys.readouterr().out
    assert main(["schema", str(tmp_path), "--format", "text"]) == 0
    out = capsys.readouterr().out
    assert "PID-5" in out and "valueQuantity" in out and CANARY not in out
    assert main(["schema", str(tmp_path / "msg.hl7"), "--source", "avro"]) == 1
    capsys.readouterr()
    out_dir = tmp_path / "fake"
    assert main(["synth", str(tmp_path / "msg.hl7"), "--rows", "2", "--format", "jsonl", "-o", str(out_dir)]) == 0
    assert sorted(path.stem for path in out_dir.iterdir()) == ["MSH", "OBX", "PID"]
    assert CANARY.upper() not in "".join(path.read_text(encoding="utf-8") for path in out_dir.iterdir())


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    fields=st.lists(st.text(alphabet="ab^~&\\ 12", max_size=6), min_size=1, max_size=8),
    segment=st.sampled_from(["PID", "OBX", "ZZZ", "NK1"]),
)
def test_hl7_never_leaks_whatever_the_fields_hold(tmp_path_factory, fields, segment):
    body = "|".join(CANARY + field for field in fields)
    path = tmp_path_factory.mktemp("hl7") / "m.hl7"
    path.write_text(f"MSH|^~\\&|A|B\r{segment}|{body}\r", encoding="utf-8")
    assert CANARY not in everything(path)
