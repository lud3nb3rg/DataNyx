# Features

## Schemas instead of data

- **Over thirty kinds of input.** CSV, TSV, JSON, JSON Lines, YAML, XML, Excel, Parquet, ORC, Avro, Arrow,
  SAS, Stata, SPSS, SQLite, DuckDB, PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, BigQuery, Snowflake,
  MongoDB, Delta Lake, Iceberg, FHIR, HL7 v2, Protocol Buffers, SQL files, OpenAPI, whole folders of files,
  compressed files (`.gz`, `.bz2`, `.xz`, `.zst`) and files at a URL. See [supported sources](sources.md).
- **Values can't leak by construction.** The schema model has no field that could hold one, except the labels
  of a column the data owner declares public. A test plants a canary in every value and fails if it reaches any output.
- **Keys are treated as possible data too.** Emails, UUIDs, dates and per-record keys used
  as object keys, table names or file names are hidden automatically.
- **Databases are never queried for rows.** Schemas come from the catalog alone, and SQLite
  files are opened with an authorizer that refuses to read user tables.
- **Six output formats:** JSON Schema, a compact text form, TypeScript, Pydantic, SQL and a one-line-per-table summary.
- **Optional hints, all off by default.** `--scale-hints` gives a table's size as an order of magnitude,
  `--infer-keys` flags unique columns, composite keys and likely joins as yes/no guesses, `--column-hints` gives each
  column ranges for its distinct values, missing values and text length, and `--public-column` lists the labels of a
  column you declare safe, such as a country. None shows a count. See [schemas](schemas.md).
- **Built for big databases and files.** Declared unique keys, views, `--include`/`--exclude` patterns, `--max-tables`,
  `--max-rows` and the `summary` format.

## Writing code without the data

- **Synthetic data.** `datanyx synth` generates fake rows from a schema, with foreign keys that point at real rows,
  and `--messy` adds the flaws real data has (stray spaces, `n/a` among numbers, repeated rows), so code is tested
  against them before it meets the real thing. See [synthetic data](synthetic-data.md).
- **Code checking.** `datanyx check` finds misspelled columns, unknown tables, ambiguous joins and
  type mistakes in SQL and pandas code from the schema alone, before anything runs. See [checking code](checking-code.md).
- **Debugging without the data.** `datanyx run` runs a script on the real data and shows the agent
  only a scrubbed error, the size of the output, and with `--describe-output` the schema of the file it wrote, so it
  can fix its own bugs and see a wrong result. See [debugging](debugging.md).
- **Change detection.** `datanyx diff` compares two schemas and says which changes could break code
  written for the old one, with an exit code for CI. See [schema diff](schema-diff.md).

## Personal data in text and tables

- **A scan before you share.** `datanyx scan` reports which files in a folder hold personal data or
  secrets, by kind, count and line, and never the values themselves. See [scanning](scanning.md).
- **Text anonymization.** `datanyx anonymize` replaces names, emails, ids, credentials and the like with placeholders.
  It takes a whole folder with one set of placeholders, reads HTML, email, Word, PDF, WhatsApp and Slack exports as
  well as text, and can work on a stream as it arrives (`tail -f app.log | datanyx anonymize - --stream`).
  See [text anonymization](anonymization.md).
- **Reversible when you want it.** Optionally save a mapping and restore real values in what
  the agent writes, with `datanyx restore`.
- **Pseudonymized copies of tables.** `datanyx pseudonymize` writes a CSV, JSON or SQLite copy with
  consistent fake values, so the data can be shared and joined without the real ones. See [pseudonymizing tables](pseudonymization.md).
- **Detection you can measure and extend.** `datanyx eval` reports how much of your own labelled text is
  hidden, by kind. Custom detectors, domain packs (healthcare, finance), several language models at once, and optional
  Presidio, GLiNER or local-LLM backends improve what it finds. See [detection quality](detection.md).

## Working with agents and teams

- **Enforced boundaries.** `datanyx protect` sets up Claude Code so the agent can't read the data
  directly, and can only work with it through DataNyx. See [using it with an agent](agents.md).
- **An MCP server** that gives an agent schemas, fake rows, code checks, scans, diffs and anonymized text, and nothing
  that could be used to probe for what is hidden. It can run over HTTP for a team, with a bearer token.
- **An audit trail.** The MCP server can record every tool call an agent makes, never a value, and `datanyx audit`
  summarises it.
- **A policy a team can share.** `datanyx.toml` holds the settings that would otherwise be repeated on
  every command, `datanyx.local.toml` the terms that must stay private. See [team settings](settings.md).
- **Safe error messages.** Every message that can reach an agent, including stderr, is
  built so it never quotes the data.
- **Ways to deploy it.** PyPI, a container image, a pre-commit hook, a GitHub Action and verifiable releases. See
  [installing and deploying](deployment.md).
