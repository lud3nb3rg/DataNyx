# Changelog

## Unreleased

- `--column-hints` also says when some values of a text column differ only by case or spacing (`US` and `us`), as `some` or
  `many`, and says nothing when none do. The schema alone can't show that a column is split this way, and a script that
  groups by it then gives a wrong answer without failing. Still never a number or a value.

## 0.2.0 (2026-11-06)

New sources and options, fixes from a full security audit, and releases that can be verified. Several of the fixes change behaviour: read **Changes that may need action** below before upgrading.

**Added**

- Releases are checkable: PyPI attestations, a GitHub build-provenance attestation for the wheel and sdist, a CycloneDX SBOM, and a signed container image with provenance and an SBOM. A GitHub Release is created from `CHANGELOG.md` when a version is tagged. See [Checking a release](docs/deployment.md#checking-a-release).
- The documentation is published as a site at https://lud3nb3rg.github.io/DataNyx/.
- The container image now includes `zstd`, `remote` and `s3` support.
- `--column-hints` (and `column_hints` under `[schema]`, and `datanyx mcp --column-hints`) gives each column of a CSV, TSV, JSON Lines, JSON array or Excel table ranges for how many distinct values it has, how often it is missing and how long its text gets. Never a number or a value.
- `--infer-keys` now also finds composite keys (two key-looking columns unique together) and composite joins, marked as guesses in every format.
- `--max-rows N` (and `max_rows` under `[schema]`) reads only the first N rows of a CSV, TSV, JSON Lines or Excel file, or of a streamed JSON array. A file cut short says so and gets no size hint or key guesses. `datanyx mcp --max-rows` sets it for the server.
- `datanyx mcp --debug-log FILE` records where each unexpected failure happened (exception types, file, line and function; never a message or value) in a private file for the owner.
- The test suite runs in parallel with `pytest -n auto`, and CI enforces a coverage floor of 88%.
- Compressed files: `.gz`, `.bz2`, `.xz` and `.zst` (new extra `zstd`) after any text format, such as `orders.csv.gz`.
- New sources: Arrow and Feather v2, Delta Lake and Iceberg table folders, Protocol Buffers (`.proto`), SQL files with `CREATE TABLE` (new extra `ddl`) and OpenAPI/Swagger documents. BigQuery and Snowflake work through their SQLAlchemy dialects (new extras `bigquery` and `snowflake`; not tested against a live service).
- `--scale-hints` for SQL Server and Oracle tables, and for Arrow, Delta Lake and Iceberg.
- Files at a URL: every file type that can be read from a path (except DuckDB, SAS, Stata and SPSS) can be read from `https://`, `s3://`, `gs://` or `az://`, or from `env:NAME` holding the URL. Parquet and ORC fetch only the footer. New extras `remote`, `s3`, `gcs` and `azure`.
- SQL databases now show columns the database declares unique (constraints and unique indexes) as facts, not guesses, and sets of columns unique together as `unique (a, b)`.
- `--views` describes database views (names and columns only). `--include`, `--exclude` and `--max-tables` choose which tables to describe, and `--format summary` lists one line per table. The MCP server caps `describe_data` at 200 tables (`datanyx mcp --max-tables`), and the agent can narrow it with `tables`, `exclude` and `limit`.
- `--delimiter` for `schema`, `synth`, `check`, `diff` and `pseudonymize`: read CSV and TSV files that use `;`, `|` or another separator. It takes a character or one of `comma`, `semicolon`, `tab`, `pipe`, `space`.

**Fixed**

- `anonymize` on a `.docx` could write a corrupt file when a document property was empty, such as `<dc:title/>`.
- `pseudonymize` on SQLite left a foreign key unchanged when its name looked personal (`author`), which broke `PRAGMA foreign_key_check`.
- A decimal such as `1234.50000` was hidden as a phone number; it is now an ID. `.xpt` columns no longer show a `None` type, `eval` says "1 labelled string", and the shared-fields note for a folder groups files with the same fields.

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

**Also fixed, from a full code review**

Changes that may need action:

- `pseudonymize`: names, cities, companies and addresses are now invented, pronounceable words rather than a short word list, so different values no longer merge and a fake doesn't depend on row order. Fakes made with an existing key file change: re-export anything that has to line up. With `--key-file`, a note says if any fake had to be drawn again.
- The MCP server: a `sqlite:///` URL under `--allow-urls`, or in an allowed `env:NAME`, must point under `--root`.

The guard:

- It also refuses `dd if=`, `-fdata/x`, `@data/x`, brace expansion (`{data,src}/x.csv`), here-strings, `$PWD/data/x`, a Glob brace list, encoded PowerShell commands, shortened options, and git commands that print protected files or revert the setup (`git show HEAD:x`, `git grep`, `git diff` and `git log -p` when git holds the data, `git checkout -- .`, `git reset --hard`). `datanyx run` checks the command it starts as a command of its own.
- A substitution or subshell is checked from where it runs: `(cd /tmp); cat data/x.csv` and `echo $(cat data/x.csv); cd /tmp` no longer get through. Comments aren't commands, and a here-document is text.
- Git Bash and WSL paths are understood on Windows. `mv /tmp/x .` and `git commit -m "about .claude/settings.json"` are no longer refused, and `git clean` is allowed for a folder that doesn't hold the setup.
- `protect` no longer leaves deny rules behind for paths it stopped protecting.

Real data in output:

- `pseudonymize`: unbroken phone numbers (`+14155552671`), numeric phones and postcodes, a default written `DEFAULT "x"`, full-text index contents, bare top-level JSON values, and JSON keys first seen after the sample. `--mapping` now records every kind.
- `anonymize`: Word text after a text box, picture names and the paths of links out of the document, and strings and comments in HTML stylesheets. A sender or display name known from a chat or email's structure is now hidden in message bodies too.
- `scrub`: terms to hide now reach traceback lines, patterns reach `File` paths, and longer secrets are replaced first. A password holding `@` is removed whole, here and in the audit log.

The MCP server:

- `scan_folder` uses the server's custom detectors, packs and scan settings. `anonymize_file` uses every configured model. A MongoDB URL in `env:NAME` reaches the MongoDB source. The mcp 1.x fallback keeps the host and security settings.

Scan:

- Excludes apply to files named one by one, so they work in the pre-commit hook. `.duckdb`, `.ddb`, `.zsav`, `.por` and `.xpt` are flagged as data files, including when too large. `--backend` findings such as SSN are no longer filtered out, and `--kinds` accepts them.

Crashes and wrong answers:

- `pseudonymize` no longer hangs when a kind's fakes run out, and handles database paths holding `#`, `?` or `%`, duplicate CSV headers, blank lines before a header, and tables named like `sqlite2_users`.
- `synth` on an input with no tables, `anonymize --keep-names` with `a.txt` and `a.eml`, a damaged `.docx` in a folder, a CSV cell of thousands of digits, a YAML date that doesn't exist, recursive YAML aliases and deep XML are all clear errors or per-file notes now. `synth --format sqlite` works from Avro and Stata. One bad audit line no longer hides the log.
- `check`: `df.join(other, on=...)` no longer reports a false error, `SUM(DISTINCT x)` is type-checked, a time column accepts a time, and attribute checks work without pandas installed.
- `eval --kind` with a kind nobody labelled is an error, and nothing measured no longer passes `--min-recall`.
- `init --local` finds the repository from a subfolder. HL7 reads every MLLP frame, and HL7 and FHIR say when they stop at their type limit. A FHIR type FHIR doesn't define is shown as `Resource_N`. `--infer-keys` works on JSON large enough to be streamed. A server denying foreign keys for one table costs only a note. Connection timeouts are set per driver.

Internals:

- A single pass for kept spans in the anonymizer, and `check` no longer imports pandas. SQL and DuckDB share `build_object` for hiding names, so their notes now read "hid data-like names". One home for the Luhn check, the placeholder pattern and the SQLite URI. Output encoding is set once, in `main`.

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
- Value-free schemas for JSON, JSON Lines and SQL databases.
- Text anonymization with patterns and spaCy NER, with opt-in mapping and restore.
