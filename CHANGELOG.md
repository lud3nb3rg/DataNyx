# Changelog

## Unreleased

- Added `datanyx scan`, which reports which files in a folder hold personal data or secrets by kind, count and line without showing any value, and a `scan_folder` MCP tool. Added `SecretDetector` for API keys, tokens, private keys and passwords.
- Added `datanyx protect` and `datanyx hook`, which set up Claude Code so an agent can't read protected data directly: deny rules, a guard hook that fails closed, and an MCP config. DataNyx's own commands stay available.
- Added `datanyx check`, which finds unknown tables and columns, ambiguous joins and type mistakes in SQL and pandas code from a schema, and a `check_code` MCP tool. SQL checking needs the new `check` extra.
- Added `datanyx scrub` and `datanyx run`, which remove data from error messages and program output while keeping the exception type, line numbers and code.
- Added `datanyx synth`, which generates fake data from a schema (JSON, JSON Lines, CSV or SQLite), with unique keys and valid foreign keys, and a `synthetic_sample` MCP tool.
- Licensed under MIT.
- Development moved to uv: `uv.lock`, a `dev` dependency group and a `models` group for the NER models.

- Added readers for CSV, TSV, Parquet, Excel, YAML and XML, and for whole folders.
- Large JSON files are streamed when `ijson` is installed.
- Added `typescript`, `pydantic` and `sql` output formats.
- Added `datanyx mcp`, a restricted MCP server, and `schema_of` / `anonymize_text`.
- Added `datanyx doctor`, which checks the optional packages and the NER model.
- Mapping files are now restricted to their owner on Windows as well as POSIX, including in folders that have no permissions to inherit.

## 0.1.0

- Value-free schemas for JSON, JSON Lines and SQL databases.
- Text anonymization with patterns and spaCy NER, with opt-in mapping and restore.
