# Features

- **Over twenty kinds of input.** CSV, TSV, JSON, JSON Lines, YAML, XML, Excel, Parquet, ORC, Avro,
  SAS, Stata, SPSS, SQLite, DuckDB, PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, MongoDB, FHIR,
  HL7 v2, and whole folders of files.
- **Values can't leak by construction.** The schema model has no field that could hold one.
  A fuzz test plants a canary in every value and fails if it reaches any output.
- **Keys are treated as possible data too.** Emails, UUIDs, dates and per-record keys used
  as object keys are hidden automatically.
- **Databases are never queried for rows.** Schemas come from the catalog alone, and SQLite
  files are opened with an authorizer that refuses to read user tables.
- **Five output formats:** JSON Schema, a compact text form, TypeScript, Pydantic and SQL.
- **Synthetic data.** `datanyx synth` generates fake rows from a schema, with foreign keys
  that point at real rows, so an agent can test its code and see output without any real data.
- **Debugging without the data.** `datanyx run` runs a script on the real data and shows the agent
  only a scrubbed error and the size of the output, so it can fix its own bugs.
- **Code checking.** `datanyx check` finds misspelled columns, unknown tables, ambiguous joins and
  type mistakes in SQL and pandas code from the schema alone, before anything runs.
- **Detection you can measure and extend.** `datanyx eval` reports how much of your own labelled text is
  hidden, by kind. Domain packs (healthcare, finance), several language models at once, and optional
  Presidio, GLiNER or local-LLM backends are there to improve what it finds.
- **Documents, folders and streams.** `datanyx anonymize` takes a whole folder with one set of
  placeholders, reads HTML, email, Word, PDF, WhatsApp and Slack exports as well as text, and can
  work on a stream as it arrives (`tail -f app.log | datanyx anonymize - --stream`).
- **Pseudonymized copies of tables.** `datanyx pseudonymize` writes a CSV, JSON or SQLite copy with
  consistent fake values, so the data can be shared and joined without the real ones.
- **A scan before you share.** `datanyx scan` reports which files in a folder hold personal data or
  secrets, by kind, count and line, and never the values themselves.
- **A policy a team can share.** `datanyx.toml` holds the settings that would otherwise be repeated on
  every command, `datanyx.local.toml` the terms that must stay private, and custom detectors cover
  your own formats and names.
- **Change detection.** `datanyx diff` compares two schemas and says which changes could break code
  written for the old one, with an exit code for CI.
- **Enforced boundaries.** `datanyx protect` sets up Claude Code so the agent can't read the data
  directly, and can only work with it through DataNyx.
- **An audit trail.** The MCP server can record every tool call an agent makes, never a value.
- **An MCP server** that gives an agent only schemas and anonymized text, nothing that could
  be used to probe for what is hidden.
- **Reversible when you want it.** Optionally save a mapping and restore real values in what
  the agent writes.
- **Safe error messages.** Every message that can reach an agent, including stderr, is
  built so it never quotes the data.
