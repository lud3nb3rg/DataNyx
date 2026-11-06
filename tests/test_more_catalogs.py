"""Arrow, Delta Lake, Iceberg, Protocol Buffers, SQL files and OpenAPI. All data is synthetic.

Every fixture hides the canary in the places each format has for free text or values (comments, defaults,
enum labels, examples, partition values, table properties): none of it may reach the output.
"""

import json
import os

import pytest

from datanyx import KeyPolicy, SourceError, extract, render
from datanyx.cli import main
from datanyx.schema import Object

CANARY = "zqcanaryvalue"


def table(schema, name=None):
    entity = next(e for e in schema.entities if name is None or e.name == name)
    node = getattr(entity.node, "items", entity.node)
    return entity, node.fields if isinstance(node, Object) else {}


def clean(schema):
    everything = "\n".join([render(schema, name) for name in ("json-schema", "text", "typescript", "pydantic", "sql", "summary")])
    assert CANARY not in everything + "\n".join(schema.notes)


# --- Arrow ---------------------------------------------------------------------------------------------------


def test_arrow_file_stream_and_feather(tmp_path):
    pa = pytest.importorskip("pyarrow")
    ipc = pytest.importorskip("pyarrow.ipc")
    feather = pytest.importorskip("pyarrow.feather")
    field = pa.field("name", pa.string(), metadata={"note": CANARY})
    data = pa.table({"id": list(range(5)), "name": ["a"] * 5}).cast(pa.schema([pa.field("id", pa.int64()), field]))
    data = data.replace_schema_metadata({"owner": CANARY})
    with pa.OSFile(str(tmp_path / "f.arrow"), "wb") as sink, ipc.new_file(sink, data.schema) as writer:
        writer.write_table(data)
    with pa.OSFile(str(tmp_path / "s.arrow"), "wb") as sink, ipc.new_stream(sink, data.schema) as writer:
        writer.write_table(data)
    feather.write_feather(data, str(tmp_path / "x.feather"))
    for name, expected in (("f.arrow", "fewer than 1,000 rows"), ("s.arrow", None), ("x.feather", "fewer than 1,000 rows")):
        schema = extract(str(tmp_path / name), policy=KeyPolicy(scale_hints=True))
        entity, fields = table(schema)
        assert set(fields) == {"id", "name"} and entity.scale == expected
        clean(schema)


# --- Delta Lake ----------------------------------------------------------------------------------------------


def make_delta(folder, commits=1):
    log = folder / "_delta_log"
    log.mkdir(parents=True)
    schema = {
        "type": "struct",
        "fields": [
            {"name": "id", "type": "long", "nullable": False, "metadata": {"comment": CANARY}},
            {"name": "name", "type": "string", "nullable": True, "metadata": {}},
            {"name": "price", "type": "decimal(10,2)", "nullable": True, "metadata": {}},
            {"name": "qty", "type": "decimal(5,0)", "nullable": True, "metadata": {}},
            {"name": "seen", "type": "timestamp", "nullable": True, "metadata": {}},
            {"name": "tags", "type": {"type": "array", "elementType": "string", "containsNull": True}, "nullable": True, "metadata": {}},
            {"name": "attrs", "type": {"type": "map", "keyType": "string", "valueType": "long", "valueContainsNull": True}, "nullable": True, "metadata": {}},
            {"name": "address", "type": {"type": "struct", "fields": [{"name": "city", "type": "string", "nullable": True, "metadata": {}}]}, "nullable": True, "metadata": {}},
            {"name": f"{CANARY} name", "type": "string", "nullable": True, "metadata": {}},
        ],
    }
    meta = {"id": "t", "name": f"{CANARY} table", "format": {"provider": "parquet"}, "schemaString": json.dumps(schema),
            "partitionColumns": ["name"], "configuration": {"comment": CANARY}, "createdTime": 1}
    for number in range(commits):
        lines = [{"protocol": {"minReaderVersion": 1, "minWriterVersion": 2}}] if number == 0 else []
        if number == 0:
            lines.append({"metaData": meta})
        stats = json.dumps({"numRecords": 1500 // commits})
        lines.append({"add": {"path": f"name={CANARY}/part-{number}.parquet", "partitionValues": {"name": CANARY}, "size": 1,
                              "modificationTime": 1, "dataChange": True, "stats": stats}})
        (log / f"{number:020d}.json").write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return folder


def test_delta_table_schema_and_size(tmp_path):
    folder = make_delta(tmp_path / "events", commits=3)
    schema = extract(str(folder), policy=KeyPolicy(scale_hints=True))
    entity, fields = table(schema)
    assert schema.source_kind == "delta"
    assert set(fields) == {"id", "name", "price", "qty", "seen", "tags", "attrs", "address"}  # The odd name is hidden.
    assert "qty: integer" in render(schema, "text") and "price: number" in render(schema, "text")
    assert entity.scale == "10^3 to 10^4 rows"
    clean(schema)


def test_delta_log_with_a_removed_file_and_a_checkpoint(tmp_path):
    folder = make_delta(tmp_path / "t", commits=2)
    log = folder / "_delta_log"
    (log / f"{2:020d}.json").write_text(json.dumps({"remove": {"path": f"name={CANARY}/part-1.parquet"}}), encoding="utf-8")
    assert extract(str(folder), policy=KeyPolicy(scale_hints=True)).entities[0].scale == "fewer than 1,000 rows"  # 750 left.
    (log / "_last_checkpoint").write_text('{"version": 2}', encoding="utf-8")
    schema = extract(str(folder), policy=KeyPolicy(scale_hints=True))
    assert schema.entities[0].scale is None and any("no row-count hint" in n for n in schema.notes)


def test_a_folder_without_a_delta_log_is_still_a_folder(tmp_path):
    (tmp_path / "a.csv").write_text("id\n1\n", encoding="utf-8")
    assert extract(str(tmp_path)).source_kind == "directory"


def test_an_empty_delta_log_is_reported(tmp_path):
    (tmp_path / "_delta_log").mkdir()
    with pytest.raises(SourceError, match="no table schema"):
        extract(str(tmp_path))


# --- Iceberg -------------------------------------------------------------------------------------------------


def make_iceberg(folder, identifier=(1,)):
    meta = folder / "metadata"
    meta.mkdir(parents=True)
    document = {
        "format-version": 2,
        "table-uuid": "0000",
        "location": f"s3://{CANARY}-bucket/t",
        "properties": {"owner": CANARY, "comment": CANARY},
        "current-schema-id": 1,
        "schemas": [
            {"schema-id": 0, "fields": [{"id": 1, "name": "old", "required": True, "type": "long"}]},
            {
                "schema-id": 1,
                "identifier-field-ids": list(identifier),
                "fields": [
                    {"id": 1, "name": "id", "required": True, "type": "long", "doc": CANARY},
                    {"id": 2, "name": "name", "required": False, "type": "string"},
                    {"id": 3, "name": "tags", "required": False, "type": {"type": "list", "element-id": 6, "element": "string", "element-required": True}},
                    {"id": 4, "name": "amount", "required": False, "type": "decimal(9, 2)"},
                    {"id": 5, "name": f"{CANARY} name", "required": False, "type": "string"},
                ],
            },
        ],
        "current-snapshot-id": 7,
        "snapshots": [{"snapshot-id": 7, "summary": {"total-records": "25000", "owner": CANARY}}],
    }
    (meta / "v1.metadata.json").write_text(json.dumps({**document, "current-snapshot-id": -1, "snapshots": []}), encoding="utf-8")
    (meta / "v2.metadata.json").write_text(json.dumps(document), encoding="utf-8")
    (meta / "version-hint.text").write_text("2", encoding="utf-8")
    return folder


def test_iceberg_table_uses_the_current_schema_and_snapshot(tmp_path):
    folder = make_iceberg(tmp_path / "ice")
    schema = extract(str(folder), policy=KeyPolicy(scale_hints=True))
    entity, fields = table(schema)
    assert schema.source_kind == "iceberg"
    assert set(fields) == {"id", "name", "tags", "amount"}
    assert fields["id"].unique and fields["id"].unique_declared
    assert entity.scale == "10^4 to 10^5 rows"
    clean(schema)


def test_iceberg_without_a_version_hint_takes_the_highest_version(tmp_path):
    folder = make_iceberg(tmp_path / "ice2")
    os.remove(folder / "metadata" / "version-hint.text")
    assert extract(str(folder), policy=KeyPolicy(scale_hints=True)).entities[0].scale == "10^4 to 10^5 rows"


def test_a_composite_identifier_becomes_a_unique_key(tmp_path):
    folder = make_iceberg(tmp_path / "ice3", identifier=(1, 2))
    entity, _ = table(extract(str(folder)))
    assert entity.unique_keys == (("id", "name"),)


# --- Protocol Buffers ----------------------------------------------------------------------------------------

PROTO = f"""
// {CANARY}
syntax = "proto3";
package shop.v1;
import "google/protobuf/timestamp.proto";
option java_package = "{CANARY}";

/* {CANARY} */
enum Status {{ STATUS_UNKNOWN = 0; {CANARY.upper()}_ACTIVE = 1; }}

message Address {{
  string city = 1;  // {CANARY}
  string street = 2 [default = "{CANARY}"];
}}

message Customer {{
  int64 id = 1;
  string name = 2;
  optional string nickname = 3;
  repeated string tags = 4;
  Status status = 5;
  Address home = 6;
  repeated Address others = 7;
  map<string, int32> counts = 8;
  google.protobuf.Timestamp joined = 9;
  bytes photo = 10;
  Customer referrer = 11;
  oneof contact {{ string email = 12; string phone = 13; }}
  message Inner {{ double weight = 1; }}
  Inner inner = 14;
  reserved 100 to 110;
}}
"""


def test_proto_messages_become_entities(tmp_path):
    path = tmp_path / "shop.proto"
    path.write_text(PROTO, encoding="utf-8")
    schema = extract(str(path))
    assert schema.source_kind == "proto"
    assert [e.name for e in schema.entities] == ["Address", "Customer"]
    _, fields = table(schema, "Customer")
    assert set(fields) == {"id", "name", "nickname", "tags", "status", "home", "others", "counts", "joined", "photo", "referrer", "email", "phone", "inner", "contact"} - {"contact"}
    text = render(schema, "text")
    assert "tags?: array<string>" in text and "counts?:" in text and "weight?: number" in text
    assert "joined?: string<date-time>" in text and "photo?: string<binary>" in text
    assert "referrer?: unknown" in text  # A message that contains itself ends there.
    clean(schema)


def test_proto_required_labels(tmp_path):
    path = tmp_path / "old.proto"
    path.write_text('syntax = "proto2"; message M { required int32 a = 1; optional int32 b = 2; }', encoding="utf-8")
    _, fields = table(extract(str(path)))
    assert fields["a"].required and not fields["b"].required


# --- SQL files -----------------------------------------------------------------------------------------------

DDL = f"""
-- {CANARY}
CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  email VARCHAR(80) NOT NULL UNIQUE,
  status ENUM('{CANARY}', 'other') DEFAULT '{CANARY}',
  balance NUMERIC(10, 2) DEFAULT 0,
  count NUMERIC(5, 0),
  tags TEXT[],
  joined TIMESTAMP,
  "{CANARY} odd" TEXT,
  CONSTRAINT check_x CHECK (email <> '{CANARY}')
);
COMMENT ON TABLE customers IS '{CANARY}';
CREATE TABLE orders (
  id INT NOT NULL,
  customer_id INT REFERENCES customers(id),
  line INT,
  note TEXT DEFAULT '{CANARY}',
  PRIMARY KEY (id),
  UNIQUE (customer_id, line)
);
CREATE TABLE items (order_id INT, sku TEXT);
ALTER TABLE ONLY items ADD CONSTRAINT items_fk FOREIGN KEY (order_id) REFERENCES orders(id);
INSERT INTO customers (id, email) VALUES (1, '{CANARY}@example.com');
COPY customers (id, email) FROM stdin;
1\t{CANARY}@example.com
2\t{CANARY}2@example.com
\\.
CREATE VIEW v AS SELECT '{CANARY}' AS c;
"""


def test_sql_file_tables_columns_and_keys(tmp_path):
    pytest.importorskip("sqlglot")
    path = tmp_path / "schema.sql"
    path.write_text(DDL, encoding="utf-8")
    schema = extract(str(path))
    assert schema.source_kind == "ddl"
    assert [e.name for e in schema.entities] == ["customers", "orders", "items"]
    customers, fields = table(schema, "customers")
    assert set(fields) == {"id", "email", "status", "balance", "count", "tags", "joined"}
    assert customers.primary_key == ("id",)
    assert fields["email"].unique and fields["email"].unique_declared
    text = render(schema, "text")
    assert "count: integer | null" in text and "balance: number | null" in text and "tags: array<string> | null" in text
    assert "email: string" in text and "email: string | null" not in text  # NOT NULL.
    orders, _ = table(schema, "orders")
    assert orders.primary_key == ("id",) and orders.unique_keys == (("customer_id", "line"),)
    assert [(k.columns, k.referenced_entity) for k in orders.foreign_keys] == [(("customer_id",), "customers")]
    items, _ = table(schema, "items")
    assert [(k.columns, k.referenced_entity, k.referenced_columns) for k in items.foreign_keys] == [(("order_id",), "orders", ("id",))]
    clean(schema)


def test_sql_file_in_another_dialect(tmp_path):
    pytest.importorskip("sqlglot")
    path = tmp_path / "mysql.sql"
    path.write_text("CREATE TABLE `users` (\n  `id` int(11) NOT NULL AUTO_INCREMENT,\n  `name` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n", encoding="utf-8")
    entity, fields = table(extract(str(path)))
    assert entity.name == "users" and set(fields) == {"id", "name"} and entity.primary_key == ("id",)


def test_sql_file_without_tables_is_not_an_error(tmp_path):
    pytest.importorskip("sqlglot")
    (tmp_path / "q.sql").write_text("SELECT 1 FROM t;", encoding="utf-8")
    schema = extract(str(tmp_path / "q.sql"))
    assert schema.entities == () and "no CREATE TABLE" in schema.notes[0]


def test_sql_file_needs_its_package(tmp_path, monkeypatch):
    (tmp_path / "a.sql").write_text("CREATE TABLE t (a INT);", encoding="utf-8")
    monkeypatch.setitem(__import__("sys").modules, "sqlglot", None)
    with pytest.raises(SourceError, match=r"datanyx\[ddl\]"):
        extract(str(tmp_path / "a.sql"))


# --- OpenAPI -------------------------------------------------------------------------------------------------

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": CANARY, "description": CANARY},
    "paths": {"/x": {"get": {"summary": CANARY}}},
    "components": {
        "schemas": {
            "Customer": {
                "type": "object",
                "required": ["id", "email"],
                "properties": {
                    "id": {"type": "integer", "format": "int64", "example": CANARY},
                    "email": {"type": "string", "format": "email", "description": CANARY, "default": CANARY},
                    "status": {"type": "string", "enum": [CANARY, "other"]},
                    "nickname": {"type": "string", "nullable": True},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "home": {"$ref": "#/components/schemas/Address"},
                    "parent": {"$ref": "#/components/schemas/Customer"},
                    "extra": {"type": "object", "additionalProperties": {"type": "integer"}},
                    f"{CANARY} name": {"type": "string"},
                },
            },
            "Address": {"type": "object", "properties": {"city": {"type": "string", "example": CANARY}}},
            "Pet": {"allOf": [{"$ref": "#/components/schemas/Address"}, {"type": "object", "properties": {"legs": {"type": "integer"}}}]},
            "Either": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        }
    },
}


def test_openapi_schemas_become_entities(tmp_path):
    path = tmp_path / "api.json"
    path.write_text(json.dumps(OPENAPI), encoding="utf-8")
    schema = extract(str(path))
    assert schema.source_kind == "openapi"
    assert [e.name for e in schema.entities] == ["Customer", "Address", "Pet", "Either"]
    _, fields = table(schema, "Customer")
    assert set(fields) == {"id", "email", "status", "nickname", "tags", "home", "parent", "extra"}
    assert fields["id"].required and fields["email"].required and not fields["status"].required
    text = render(schema, "text")
    assert "email: string<email>" in text and "nickname?: string | null" in text
    assert "parent?: unknown" in text  # Recursion ends there.
    _, pet = table(schema, "Pet")
    assert set(pet) == {"city", "legs"}
    clean(schema)


def test_openapi_in_yaml_and_plain_json_is_still_json(tmp_path):
    yaml = pytest.importorskip("yaml")
    (tmp_path / "api.yaml").write_text(yaml.safe_dump(OPENAPI), encoding="utf-8")
    assert extract(str(tmp_path / "api.yaml")).source_kind == "openapi"
    (tmp_path / "plain.json").write_text(json.dumps({"openapi_version": 1, "id": 2}), encoding="utf-8")
    assert extract(str(tmp_path / "plain.json")).source_kind == "json"


# --- Together ------------------------------------------------------------------------------------------------


def test_a_folder_reads_these_files_but_treats_a_table_folder_as_one_table(tmp_path):
    pytest.importorskip("sqlglot")
    (tmp_path / "s.sql").write_text("CREATE TABLE t (a INT);", encoding="utf-8")
    (tmp_path / "shop.proto").write_text("message M { int32 a = 1; }", encoding="utf-8")
    names = [e.name for e in extract(str(tmp_path)).entities]
    assert "s.sql#t" in names and "shop.proto#M" in names


def test_command_line_reads_the_new_formats(tmp_path, capsys):
    path = make_delta(tmp_path / "d")
    assert main(["schema", str(path), "--format", "summary"]) == 0
    assert "1 table" in capsys.readouterr().out
    assert main(["schema", str(path), "--source", "iceberg"]) == 1  # Not an Iceberg table.
    capsys.readouterr()


def test_doctor_copes_with_a_missing_parent_package(monkeypatch, capsys):
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name, *args):
        if "." in name:  # A dotted name imports its parent first, which raises when that is what is missing.
            raise ModuleNotFoundError(f"No module named {name.split('.')[0]!r}")
        return real(name, *args)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    assert main(["doctor"]) == 0
    assert "Snowflake" in capsys.readouterr().out
