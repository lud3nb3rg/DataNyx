# Changelog

## Unreleased

Fixes from a full code review. Several change what you get, so read these before upgrading.

**Changes in behaviour**

- `datanyx pseudonymize`: names, cities, companies and addresses are now invented, pronounceable words instead of a short word list, so different values no longer merge and a fake doesn't depend on row order. Fakes made with an existing key file change: re-export anything that has to line up.
- `datanyx scan --fail-on-findings` now also exits with 1 when a file that could hold data wasn't read (too large, unreadable, or a binary data file). Use `--exclude` or `--max-size` for files you've decided about.
- The MCP `anonymize_file` tool no longer takes `hide`, which let an agent test guesses. Choose terms to hide when starting the server. An `env:NAME` that holds a path or a `sqlite:///` URL must now point under `--root`, and so must a `sqlite:///` URL under `--allow-urls`.
- Shortened command-line options (`--rev` for `--reveal`) are no longer accepted.
- `anonymize`, `scrub` and the MCP server now hide credentials such as `password=...`.
- `schema --format sql` keeps native types only for SQL and DuckDB sources. A FHIR type that FHIR doesn't define is shown as `Resource_N`.
- A mapping, key file or audit log that can't be made private to its owner is no longer kept.

**Protect guard**

- The guard reads shell syntax rather than splitting on spaces, so `&`, `$(...)`, backticks, subshells, redirections without spaces, brace expansion, here-documents, joined quotes, `dd if=`, and `bash -c`, `eval`, `cmd /c` and `pwsh -Command` no longer get around it. Encoded PowerShell commands are refused.
- Only datanyx itself counts as a datanyx command: `python x.py -m datanyx` and `uv run cat x datanyx` don't. `datanyx run` checks the command it starts with or without `--`.
- The setup can't be removed by deleting or moving its folders, overwritten through a redirection, or reverted with git. Git commands that print protected files are refused. Git Bash and WSL paths are understood on Windows. Glob refuses a pattern that would list a protected file.
- `protect` no longer leaves deny rules behind for paths it stopped protecting.

**Real data in output**

- `pseudonymize`: unbroken phone numbers (`+14155552671`), BLOBs under any treatment, text in a SQLite schema (defaults, CHECK constraints, comments, views, partial indexes, full-text shadow tables), CSV files with blank leading lines, cells past the header, numeric phones and postcodes, and JSON text with no usable key were all kept. `--mapping` now records every kind.
- `schema`: notes named hidden parents in Parquet, ORC and Avro, and a CSV with no header showed its first row as field names (when that row holds an email or the like).
- `anonymize`: HTML comments, script and style strings, `data-*` attributes and unquoted attributes; Word text after a text box, tracked deletions and their authors, `people.xml`, custom properties, embedded data, alt text and links out of the document. A known sender or display name is now hidden in message bodies too.
- `scrub`: terms to hide now reach traceback lines, patterns reach `File` paths, indented lines that aren't code are treated as messages, long quoted strings are removed, and longer secrets are replaced first.
- URL credentials: passwords holding `@`, tokens with no colon and secrets in the query string are found, by one rule shared by `scrub` and the audit log.
- The Ollama backend ignores proxy settings, so text can't leave the computer through one.

**MCP server**

- `scan_folder` uses the server's custom detectors, packs and scan settings. `anonymize_file` uses every configured model. A MongoDB URL in `env:NAME` reaches the MongoDB source. `BearerAuth` refuses any connection but start-up events without the token. The mcp 1.x fallback keeps the host and security settings.

**Scan**

- UTF-16 and UTF-32 files are read. Excludes apply to files named one by one, so they work in the pre-commit hook. Reports say when they aren't complete. `.duckdb`, `.ddb`, `.zsav`, `.por` and `.xpt` are flagged as data files, including when too large. `--backend` findings such as SSN are no longer filtered out, and `--kinds` accepts them.

**Crashes and wrong answers**

- `pseudonymize` no longer hangs when a kind's fakes run out, and handles database paths holding `#`, `?` or `%`, duplicate CSV headers, and tables named like `sqlite2_users`.
- `schema -o` refuses to overwrite its input. `synth` on an input with no tables, `anonymize --keep-names` with `a.txt` and `a.eml`, a damaged `.docx` in a folder, a CSV cell of thousands of digits, a YAML date that doesn't exist, recursive YAML aliases and deep XML are all clear errors or per-file notes now. `synth --format sqlite` works from Avro and Stata. One bad audit line no longer hides the log.
- `check`: `df.join(other, on=...)` no longer reports a false error, `SUM(DISTINCT x)` is type-checked, a time column accepts a time, and attribute checks work without pandas installed.
- `eval --kind` with a kind nobody labelled is an error, and nothing measured no longer passes `--min-recall`.
- `init --local` finds the repository from a subfolder. HL7 reads every MLLP frame, and HL7 and FHIR say when they stop at their type limit. `--infer-keys` works on JSON large enough to be streamed. A server denying foreign keys for one table costs only a note. Connection timeouts are set per driver.

**Internals**

- Faster line numbers in `scan`, and a single pass for kept spans in the anonymizer. `check` no longer imports pandas.
- SQL and DuckDB share `build_object` for hiding names, so their notes now read "hid data-like names". One home for the Luhn check, the placeholder pattern and the SQLite URI. Output encoding is set once, in `main`.

## 0.1.0 (2026-11-06)

The first release.

- Added opt-in `--scale-hints` (a table's size as an order of magnitude, from the rows read, a file footer or a catalog estimate) and `--infer-keys` (yes/no guesses that a column is unique and that tables join, from names and values), with `scale_hints` and `infer_keys` settings and the same options on `datanyx mcp`. Inferred joins are marked as guesses in every format and drive `synth`.
- Added sources: Avro (`.[avro]`), ORC (`.[orc]`), DuckDB (`.[duckdb]`), SAS, Stata and SPSS (`.[stats]`), MongoDB (`.[mongodb]`, sampled from documents), FHIR (split by resource type) and HL7 v2 (one entity per segment). Schemas come from headers, footers or catalogs wherever the format has them; enum labels and value labels are never shown.
- Added a release workflow (PyPI trusted publishing and a `ghcr.io` image on a version tag), a `Dockerfile`, a pre-commit hook (`datanyx-scan`) and a GitHub Action (`scan` or `diff`). `datanyx scan` now takes several paths.
- Added `datanyx mcp --http HOST:PORT`: the MCP server over HTTP with a required bearer token, read from an environment variable.
- CI: newer `checkout` and `setup-uv` actions, `ubuntu-24.04`, a package/Docker build job, and tests that need the spaCy model now skip where it isn't installed.
- Added `datanyx eval`, which runs the anonymizer over labelled documents and reports recall by kind (hidden, partly hidden, missed), with `--min-recall` as a gate and `--show-misses` for the owner of the labels.
- Added detector packs (`--pack healthcare`, `--pack finance`) with check-digit validation, optional backends (`--backend presidio|gliner|ollama`, the last only for a model on this computer, with the new `presidio` extra), and several spaCy models at once (`--model a --model b`). New `models`, `packs` and `backends` settings. `datanyx doctor` lists backends and packs.
- `datanyx anonymize` now takes a folder (one set of placeholders for every file, files renamed, one mapping that restores the folder), `--stream` for live input, and reads HTML, email (`.eml`), Word, PDF (the new `pdf` extra), WhatsApp and Slack exports with their structure kept. `datanyx restore` restores folders, Word and HTML files. The `Anonymizer` class keeps placeholders across many texts.
- Added `datanyx pseudonymize`, which writes a copy of a CSV, TSV, JSON, JSON Lines or SQLite file with consistent fake values: joins and foreign keys keep working, ids keep their length and never collide, and a key file makes the fakes repeatable.
- Added `datanyx.toml` and `datanyx.local.toml` for project settings, with `datanyx init` and `datanyx config`. Unknown settings are errors with a suggestion, and terms to hide can stay in the uncommitted local file. Needs `tomli` on Python 3.10.
- Added custom detectors: patterns and dictionaries described in a file, found by `anonymize`, `scrub`, `scan` and the MCP server.
- Added `datanyx diff`, which compares two schemas (saved or live) and sorts the changes into breaking, compatible and info, and a `schema_diff` MCP tool.
- Added an MCP audit log (`datanyx mcp --audit-log`) and `datanyx audit` to summarise it. It records tools, paths and outcomes, never values.
- `datanyx protect` now also stops an agent from reading `datanyx.local.toml`, editing the settings files, or using options that loosen the policy.
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
