# Changelog

## Unreleased

A security release: the fixes from a full audit. Each of these closed a way past what DataNyx promises, and `tests/test_security.py` keeps it closed.

**Changes that may need action**

- MCP: `env:NAME` now only works for variables named with `--allow-env NAME` or `allow_env` under `[mcp]`. It used to reach any variable in the server's environment, and through it any database or file outside `--root`.
- MCP: `anonymize_file` no longer takes `hide`, which told an agent whether a guessed term was in the file. Set terms to hide in `datanyx.local.toml`.
- `scan --fail-on-findings` now also fails when a file that may hold data couldn't be read: too large, or binary of an unknown kind. Exclude it, raise `--max-size`, or pass `--allow-unscanned`. Images, fonts and the like don't count.
- The GitHub Action ignores `datanyx.toml` unless `use-config: "true"`, since a pull request can change that file.
- Options can no longer be abbreviated: `--rev` is an error, not `--reveal`.
- Run `datanyx protect --write` again: the hook is now installed with `--require-config`.
- `datanyx anonymize` removes HTML comments and script contents. `pseudonymize` empties BLOBs unless they are in `--keep-column`, and leaves out column defaults, `CHECK` constraints and any view or index with a literal.
- A CSV or Excel sheet whose first row looks like data gets `column_N` names. If it was a header, list its names with `--keep-name` or under `keep_names`.

**Fixed**

- `anonymize`, `scrub` and the MCP server now hide credentials: API keys, tokens, passwords and whole private key blocks. `anonymize_file` also refuses key, mapping and `.env` files.
- The guard reads shell commands the way a shell does, so quoting, escapes, redirections, `$(...)`, backticks, `bash -c` and `eval` no longer hide a path. Paths are compared after resolving them.
- The guard no longer gives `datanyx`'s allowances to a command that merely mentions it (`ls -m datanyx data`), and `datanyx run` checks its command with or without `--`.
- The agent can no longer switch protection off: `rm -rf .datanyx`, `cd .claude && ...`, `./.claude/./settings.json`, `cat x > .claude/settings.json`, `git rm`, `git clean`, a `datanyx.toml` in a sub-folder, `DATANYX_CONFIG`, the user's own Claude settings, or replacing the running install. A hook whose settings have gone now refuses everything.
- The guard refuses `--hide`, `--model`, `--backend`, `--detectors` and `--keep-names`, `--names-from` with anything but the protected data, output options that point at it, and Glob patterns that match it.
- `scan` reads UTF-16 and UTF-32 files and text with a stray NUL, which used to count as binary and pass.
- A headerless CSV or Excel sheet no longer shows its first record as field names, and `pseudonymize` no longer copies that row in the clear.
- Notes from Parquet, ORC and Avro no longer name a hidden parent field.
- Text in an Arrow type (a timezone) can no longer reach a schema or run as SQL in `synth --format sqlite`, which could create a database at a path of the file's choosing.
- Word: comment and revision authors, tracked deletions, alt text, content control labels, custom properties, custom XML, embedded files and the glossary are now covered. HTML: every attribute that isn't about layout is.
- `pseudonymize` no longer keeps real values in column defaults, `CHECK` constraints, DDL comments, view and index text, BLOBs, values under data-like JSON keys, or extra CSV cells.
- Regular expressions that took minutes on a 100 KB line (`1 1 1...`, `a-a-a...`, `x://x://...`) now take linear time, line numbers in `scan` no longer slow down with the number of findings, a YAML alias bomb is refused, and `anonymize_file` has a 5 MB limit.
- The Ollama backend ignores proxy settings, which could send the text for a "local" model through a proxy.
- `scrub` treats only real traceback lines as code, and redacts long quoted values, `://:password@`, `://token@` and `?password=`, as does the audit log.
- `datanyx audit` shows control characters as escapes, so an agent can't write fake lines into the summary. The audit log is private from the moment it is created, also if it already existed.
- Key and mapping files are locked down before anything goes in, and it is an error if they can't be. `run --save-stdout` files are private. `icacls` is started by its full path.
- `restore` can't be sent outside its output folder by an edited mapping, and `schema -o` won't overwrite its own input.
- File names that pair a sensitive subject with other words (`jane_doe_medical.csv`) become `file_N`.
- The HTTP server refuses every kind of connection without the token, not only HTTP requests.
- `datanyx protect` warns if Claude Code is older than 2.1.295, before which a hook that fails to start lets tool calls through.
- Releases: actions pinned to commits, read-only default permissions, the image's base and dependencies pinned (by the hashes in `uv.lock`), and Dependabot to keep them current.

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
