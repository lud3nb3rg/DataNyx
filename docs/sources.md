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
| A folder | Every readable file in it, with `-r` for subfolders | as for the files in it |
| Text and documents (`anonymize`) | Text, HTML, `.eml`, `.docx`, `.pdf`, WhatsApp and Slack exports, a folder of them, or `-` for stdin | `.[text]`, plus a [model](anonymization.md#text-anonymization); PDF needs `.[pdf]` |

## Notes on the newer sources

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

Extras are installed as `pip install -e ".[yaml]"`, and `.[all]` installs every reader and
driver. Large JSON files stream with `.[streaming]`, SAS, Stata and SPSS need `.[stats]`, SQL checking needs `.[check]`, PDF needs `.[pdf]`, the Presidio backend needs `.[presidio]`, and the MCP server needs `.[mcp]`.
Other SQLAlchemy URLs, such as `snowflake://`, should work if you install their dialect, but
only the databases above are tested.
