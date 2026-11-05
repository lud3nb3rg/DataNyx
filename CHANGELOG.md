# Changelog

## Unreleased

- Added readers for CSV, TSV, Parquet, Excel, YAML and XML, and for whole folders.
- Large JSON files are streamed when `ijson` is installed.
- Added `typescript`, `pydantic` and `sql` output formats.
- Added `datanyx mcp`, a restricted MCP server, and `schema_of` / `anonymize_text`.
- Added `datanyx doctor`, which checks the optional packages and the NER model.
- Mapping files are now restricted to their owner on Windows as well as POSIX.

## 0.1.0

- Value-free schemas for JSON, JSON Lines and SQL databases.
- Text anonymization with patterns and spaCy NER, with opt-in mapping and restore.
