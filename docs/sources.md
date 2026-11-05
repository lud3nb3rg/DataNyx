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
| PostgreSQL | `postgresql://` | `.[postgres]` |
| MySQL | `mysql://` | `.[mysql]` |
| MariaDB | `mariadb://` | `.[mysql]` |
| SQL Server | `mssql://` | `.[mssql]` |
| Oracle | `oracle://` | `.[oracle]` |
| A folder | Every readable file in it, with `-r` for subfolders | as for the files in it |
| Text and documents (`anonymize`) | Text, HTML, `.eml`, `.docx`, `.pdf`, WhatsApp and Slack exports, a folder of them, or `-` for stdin | `.[text]`, plus a [model](anonymization.md#text-anonymization); PDF needs `.[pdf]` |

Extras are installed as `pip install -e ".[yaml]"`, and `.[all]` installs every reader and
driver. Large JSON files stream with `.[streaming]`, SQL checking needs `.[check]`, PDF needs `.[pdf]`, the Presidio backend needs `.[presidio]`, and the MCP server needs `.[mcp]`.
Other SQLAlchemy URLs, such as `snowflake://`, should work if you install their dialect, but
only the databases above are tested.
