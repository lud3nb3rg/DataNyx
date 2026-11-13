# Contributing

## Architecture

```
Source                     --->  DataSchema (schema.py)  --->  Renderer
files, folders, databases,       no values by design           JSON Schema, text, TypeScript,
URLs (sources/)                        |                       Pydantic, SQL, summary
                                       +---------------------> Synthetic data (synth/)
                                       +---------------------> Code checks (check/)
                                       +---------------------> Schema diff (diff.py)

Text  --->  Detectors (patterns, secrets, NER, packs, backends)  --->  Placeholders (text/)
```

| Module | Purpose |
|---|---|
| `schema.py` | Source-neutral model: `DataSchema` > `Entity` > `Node` |
| `sources/` | `Source` interface, registry and input readers (`sql_source.py` covers every SQL database) |
| `sources/directory.py` | Describes a folder: one entry per file, with file names held to the same rules |
| `sources/files.py` | What file sources share: opening a path, a compressed file or a file at a URL, typing cell text, and telling a header from data |
| `inference.py` | Builds nodes from sampled values, for sources without a catalog |
| `privacy.py` | `KeyPolicy`, and which names are structure and which are data |
| `formats.py` | Recognises string formats, returning only a label |
| `keys.py` | The opt-in hints drawn from values: `--infer-keys`, `--column-hints` and `--public-column` |
| `scale.py` | `--scale-hints`: a row count as an order of magnitude |
| `selection.py` | `--include`, `--exclude` and `--max-tables`, on names that may be shown only |
| `renderers/` | Output formats |
| `config.py` | Reads and merges `datanyx.toml` and `datanyx.local.toml`, and rejects mistakes in them |
| `diff.py` | Compares two schemas and sorts the changes by whether they could break old code |
| `audit.py` | The MCP audit log and its summary: tools, paths and outcomes, never values |
| `debuglog.py` | The MCP debug log: where a failure happened, never its message |
| `fileperms.py` | Makes a file readable by its owner only, on every system |
| `scan.py` | Finds personal data and secrets in a folder, by file, kind and line, without values |
| `protect/` | `datanyx protect` and the guard hook: Claude Code permissions, a hook that refuses direct access, MCP config; `guard.py` decides, `shell.py` reads a command the way a shell would, `paths.py` compares paths and `programs.py` knows what git and DataNyx do with their arguments |
| `check/` | Checks SQL and pandas code against a schema: unknown names and type mistakes |
| `scrub.py` | Removes data from error messages and program output, keeping the diagnosis |
| `synth/` | Generates fake data that fits a schema, clean or `messy`, and writes it as JSON, JSON Lines, CSV or SQLite |
| `mcp_server.py` | MCP server that only exposes schemas, synthetic samples and anonymized text |
| `text/detectors.py` | Finds sensitive spans: patterns, and spaCy NER through `Detector` |
| `text/documents.py` | Anonymizes HTML, email, Word, PDF, WhatsApp and Slack files, keeping their structure |
| `text/batch.py` | Folder and stream drivers: one set of placeholders across many inputs, and restoring a folder |
| `text/mapping.py` | The mapping file: created private, never overwritten, rewritten as it grows |
| `pseudonymize/` | Consistent fake values for tables: key-derived fakes, id permutation, column classification, CSV, JSON and SQLite |
| `evaluation.py` | Measures recall by kind over labelled documents, for `datanyx eval` |
| `text/packs.py` | Healthcare and finance detector packs, with check-digit validation |
| `text/backends.py` | Presidio, GLiNER and local-LLM (Ollama) detectors, loaded only when asked for |
| `text/custom.py` | Detectors described in a file: regular expressions and dictionaries of terms |
| `text/secrets.py` | Finds credentials by their shape: API keys, tokens, private keys, passwords |
| `text/anonymizer.py` | Merges spans, finds repeat mentions and assigns placeholders. `Anonymizer` keeps them across many texts |
| `cli/` | Command line, which reports errors without quoting data: one module per command (`schema.py`, `synth.py`, `scan.py`, ...), `parser.py` for the arguments and `common.py` for what they share |

## Documentation

The pages are in `docs/` and are published with MkDocs (`uv sync --group docs`, then `uv run mkdocs serve`).
`tests/test_docs.py` keeps them honest: every page is in `mkdocs.yml`, every link and heading it points at exists,
and every command is explained on some page.

`docs/cli-reference.md` is generated from the argument parser, so change a command's help in `cli/` and run
`uv run python tools/gen_cli_reference.py`; the test fails while the page is stale. The accuracy tables in
`docs/anonymization.md` come from `uv run python tools/ner_benchmark.py --write`.

## Adding a source

Implement `Source` and add it to the registry in `sources/__init__.py`. Documents, API
responses and CSV rows can reuse `Observation` from `inference.py`, and sources that
know their types up front (like Parquet) can use `build_object` from `sources/catalog.py`.

A SQL database with a SQLAlchemy dialect needs no new source. Add its short URL, driver
and connection timeout argument to `sql_source.py`, add the extra to `pyproject.toml`,
and add a server to the live tests. Anything else with a catalog should read it directly
and build `Object` nodes, with one `Entity` per table, so it never has to query any rows.

**Every new source needs a builder in `tests/test_canary_everywhere.py`**: a small function that writes a file with the canary
planted in values, names, comments, defaults and metadata. The test then runs it through the library, the command line and the
MCP tools. A source that can't be written to a file (a database server) gets a test in the same file, or a server test. A source
with no such test is documented as experimental until it has one.

## Development

The project uses [uv](https://docs.astral.sh/uv/). `uv sync` creates `.venv` from
`uv.lock`, so everyone gets the same versions:

```sh
uv sync --all-extras                 # every reader and database driver, plus the dev tools
uv sync --all-extras --group models  # also the large NER models (about 500 MB)
uv run datanyx doctor                # shows what's installed
```

## Tests

```sh
uv run pytest                        # needs no services; model tests skip without the models
uv run pytest -n auto                # in parallel, in about half the time (each worker loads the models,
                                     # so use -n 4 if a MemoryError shows up)
uv run ruff check src tests
uv run mypy
uv run pytest --cov                  # coverage report
```

**Where a test goes.** Tests are named for the behaviour they cover (`test_scan_reports.py`, `test_mcp_access_and_tools.py`,
`test_document_anonymization.py`), not for the review or the fix that produced them. Put a new test with the behaviour it
checks. The `test_security_*.py` files are different on purpose: each test there is a way past one of DataNyx's promises that
once worked, kept so it can't come back, grouped by what was attacked (the MCP server, the guard, schema values, documents,
resources and logs). A new bypass fix goes there, with a comment on what the bypass was. Helpers used by more than one file live
in a small module next to the tests (`text_corpus.py`, `docx_helpers.py`).

The coverage floor in `pyproject.toml` is set from a measurement, with the number and the reasoning beside it. Raise it when
coverage really goes up; don't lower it to get a change through.

`tests/test_fuzz_leaks.py` generates documents with a canary planted in every value, in
JSON, JSON Lines, CSV, YAML and XML, and fails if the canary reaches any output format or
note. Run it with other seeds using `uv run pytest tests/test_fuzz_leaks.py --hypothesis-seed=N`.

To run the server tests against real databases, using Docker:

```sh
docker compose -f tests/docker-compose.yml up -d --wait
DATANYX_TEST_SERVERS=1 uv run pytest -m server
docker compose -f tests/docker-compose.yml down
```

See the `CHANGELOG.md` in the repository root for what's new.
