# Supported sources

| Source | Input | Install |
|---|---|---|
| CSV, TSV | `.csv`, `.tsv`, `.tab` | built in |
| JSON, JSON Lines | `.json`, `.jsonl`, `.ndjson`, or `-` for stdin | built in |
| XML | `.xml` | built in |
| SQLite | `.db`, `.sqlite`, `.sqlite3`, or `sqlite:///path` | built in |
| YAML | `.yaml`, `.yml` | `.[yaml]` |
| Excel | `.xlsx`, `.xlsm` | `.[excel]` |
| Parquet | `.parquet`, `.pq` | `.[parquet]` |
| ORC | `.orc` | `.[orc]` |
| Avro | `.avro` | `.[avro]` |
| Arrow, Feather | `.arrow`, `.feather`, `.ipc` | `.[parquet]` |
| Delta Lake | a folder with a `_delta_log` inside | built in |
| Iceberg | a table folder with a `metadata` folder inside | built in |
| Protocol Buffers | `.proto` | built in |
| SQL files | `.sql` (`CREATE TABLE`, `ALTER TABLE`) | `.[ddl]` |
| OpenAPI, Swagger | `.json`, `.yaml`, `.yml` that start with `openapi:` or `swagger:` | built in (YAML needs `.[yaml]`) |
| SAS | `.sas7bdat`, `.xpt` | `.[stats]` |
| Stata | `.dta` | `.[stats]` |
| SPSS | `.sav`, `.zsav`, `.por` | `.[stats]` |
| DuckDB | `.duckdb`, `.ddb` | `.[duckdb]` |
| MongoDB | `mongodb://`, `mongodb+srv://` (or `--source mongodb` with `env:NAME`) | `.[mongodb]` |
| FHIR | `.fhir.json`, `.fhir.ndjson`, `.fhir`, or any JSON with `--source fhir` | built in |
| HL7 v2 | `.hl7`, `.er7` | built in |
| PostgreSQL | `postgresql://` | `.[postgres]` |
| MySQL | `mysql://` | `.[mysql]` |
| MariaDB | `mariadb://` | `.[mysql]` |
| SQL Server | `mssql://` | `.[mssql]` |
| Oracle | `oracle://` | `.[oracle]` |
| BigQuery | `bigquery://project/dataset` | `.[bigquery]` |
| Snowflake | `snowflake://` | `.[snowflake]` |
| A compressed file | `.gz`, `.bz2`, `.xz`, `.zst` after a text format, such as `orders.csv.gz` | `.zst` needs `.[zstd]` |
| A file at a URL | `https://`, `s3://`, `gs://`, `az://` (also `abfs://`) and a file type above | `.[remote]`, `.[s3]`, `.[gcs]` or `.[azure]` |
| A folder | Every readable file in it, with `-r` for subfolders | as for the files in it |
| Text and documents (`anonymize`) | Text, HTML, `.eml`, `.docx`, `.pdf`, WhatsApp and Slack exports, a folder of them, or `-` for stdin | `.[text]`, plus a [model](anonymization.md#text-anonymization); PDF needs `.[pdf]` |

## Files at a URL

```sh
datanyx schema s3://my-bucket/exports/customers.parquet --format text
datanyx schema "https://example.com/data/report.csv?X-Amz-Signature=..."
export EXPORT_URL=gs://my-bucket/orders.jsonl && datanyx schema env:EXPORT_URL
```

A URL is described exactly as the same file on disk would be. Every file type that can be read from a
path can be read from a URL, except DuckDB, SAS, Stata and SPSS, whose readers need a file on your computer
(download it first). The type comes from the end of the URL's path, so a signed link's query string is ignored.

- **Parquet and ORC are read by range requests**: the file's footer, not the whole file, is fetched, so a
  schema of a huge Parquet file costs a few kilobytes. CSV, JSON, XML and the other row formats have to
  be read through, as they are on disk; JSON is streamed (`.[streaming]`) so it is never held in memory.
- **Credentials come from the environment and the cloud's usual places**: `AWS_*` variables or `~/.aws`
  for S3, `GOOGLE_APPLICATION_CREDENTIALS` for Google Cloud, `AZURE_STORAGE_*` for Azure. Never put a
  secret on the command line. A URL with a password or a signed token can instead be kept in an environment variable
  and passed as `env:NAME`.
- **A message never quotes the URL**, which may hold a password or a token; it names the kind of error only.
- For https and S3, a connection times out after ten seconds and a transfer after five minutes.
- A folder in cloud storage can't be described yet: name the files. `datanyx scan` doesn't read URLs.
- `datanyx mcp` fetches a URL only when you start it with `--allow-urls` (or name the variable with
  `--allow-env`), because the server then makes the request on the agent's behalf. See [the privacy model](privacy-model.md).

## Notes on the newer sources

**Compressed files** (`.csv.gz`, `.jsonl.bz2`, `.json.xz`, `.xml.zst`, ...) are described exactly as the plain
file would be: the name is read through the compression, the data is decompressed as it is read, and a damaged
file gives a fixed message rather than the library's text. This covers the text formats (CSV, TSV, JSON, JSON Lines,
YAML, XML, FHIR and HL7). Parquet, ORC, Excel and the others are containers with their own compression and
can't be read compressed. It works for files at a URL too. A compressed JSON file is streamed when `.[streaming]` is installed,
since its size once unpacked isn't known.

**Arrow and Feather** files (Feather version 2, which is the Arrow file format) are described from the schema
message at the start of the file. Their row count, for `--scale-hints`, comes from the batch metadata. The older
Feather version 1 isn't supported.

**Delta Lake and Iceberg** tables are folders. Point at the table's folder and it is described as one table, from
the transaction log (`_delta_log`) or the table's current metadata file (`metadata/`), and never from a data file.
Table properties, column comments and the paths and partition values of data files are not read into the schema.
For `--scale-hints`, Delta counts the records in the log's file statistics (only when the log has no checkpoint),
and Iceberg uses the record count in the current snapshot's summary. An Iceberg table's identifier fields become
unique keys. A Delta log that has been checkpointed may need `.[parquet]` to read its schema. These tables are
read from a folder on your computer, not a URL.

**Protocol Buffers** (`.proto`) gives one entity per top-level message. Nested messages and message-typed fields
are expanded in place, a message that contains itself ends in `unknown`, maps and `repeated` fields are
typed, and well-known types such as `Timestamp` get their format. Comments, option values (`[default = "..."]`)
and enum value names are never shown, and an enum is just a string. A type defined in a file that isn't there
is `unknown`.

**SQL files** (`.sql`) give the tables defined by `CREATE TABLE` and by `ALTER TABLE ... ADD CONSTRAINT`: columns,
types, `NOT NULL`, primary keys, unique keys and foreign keys. `INSERT` statements, `COPY ... FROM stdin` blocks
and views are skipped without being read, and defaults, `CHECK` expressions, comments and enum labels are not
used. The dialect (PostgreSQL, MySQL, SQL Server, SQLite, Oracle, ...) is found by trying them in turn. Indexes
(`CREATE INDEX`) aren't read. A `.sql` file of queries has no tables and gives an empty schema with a note.

**OpenAPI and Swagger** documents give one entity per schema under `components.schemas` (or `definitions`), with
`$ref`, `allOf`, `oneOf` and `nullable` followed. Examples, defaults, descriptions and enum labels are never used.
A JSON or YAML file is taken for an API description only if it begins with an `openapi:` or `swagger:` key.

**BigQuery and Snowflake** are read like any other SQL database, through their SQLAlchemy dialects
(`bigquery://my-project/my_dataset`, `snowflake://user@account/database/schema`, with the password from
`env:NAME`). These two are wired up but are not tested against a live warehouse the way PostgreSQL, MySQL, MariaDB,
SQL Server and Oracle are, so check the output the first time you use one.

**Formats that carry their own schema** (Parquet, ORC, Avro, SAS, Stata and SPSS) are described from the
file's header or footer alone. No row is decoded. Avro enum symbols, and the value labels and variable
labels of the statistics formats, are values or free text, so they are never shown: an enum is just a
string, and a numeric code stays a number.

**DuckDB** is read from its catalog, like a SQL database, so you get tables, columns, primary keys and
foreign keys. The file is opened read-only with external access switched off, so the connection can't
read other files or write anything. Enum labels and the field names of nested `STRUCT` columns aren't shown.

**MongoDB** has no catalog of fields, so this is the one database source that reads data: it samples up to
500 documents per collection into memory, records their types, and discards them. Only the structure leaves
the program, as with a JSON file, but because documents are read, use a role that can read only what you
are willing to have sampled. Fields that appear in few documents can be missed. Keep the URL, which holds
the password, in the environment: `datanyx schema env:MONGO_URL --source mongodb`. The database is the one
in the URL, or `--schema NAME`.

**FHIR** splits a Bundle (or a bulk-export file) by `resourceType`, so a Patient and an Observation get
their own entities instead of being blended into one. A `resourceType` shows only if it looks like a FHIR
name. Files named `.fhir.json` or `.fhir.ndjson` are recognised automatically; for others use `--source fhir`.

**HL7 v2** messages become one entity per segment type (`PID`, `OBX`, ...), with fields named by position
(`PID-5`), components as `c1`, `c2`, ... and repeating fields as arrays. HL7 meaning comes from position,
so no values are needed to describe it. Timestamps are recognised, and empty fields are omitted, so a field
that is empty in some segments is marked optional.

The `stats` extra isn't installed on Windows with Python 3.10, because `pyreadstat` publishes no wheel for it and
building one needs a C library; use Python 3.11 or newer there.

Extras are installed as `uv tool install "datanyx[yaml]"` (in a clone, `uv sync --extra yaml`), and `[all]` installs every reader and
driver. Large JSON files stream with `[streaming]`, SAS, Stata and SPSS need `[stats]`, SQL checking needs `[check]`, PDF needs `[pdf]`, the Presidio backend needs `[presidio]`, and the MCP server needs `[mcp]`.
Other SQLAlchemy URLs should work if you install their dialect, but only the databases listed above
as tested are.
