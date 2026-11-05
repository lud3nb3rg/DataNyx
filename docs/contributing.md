# Contributing

## Architecture

```
Source               --->  DataSchema (schema.py)  --->  Renderer
CSV, TSV, JSON, JSONL,     no values by design           JSON Schema, text,
YAML, XML, Excel,                |                       TypeScript, Pydantic, SQL
Parquet, SQL, folders            +---------------------> Synthetic data
                                 +---------------------> Code checks

Text  --->  Detectors (patterns, NER)  --->  Placeholders
```

| Module | Purpose |
|---|---|
| `schema.py` | Source-neutral model: `DataSchema` > `Entity` > `Node` |
| `sources/` | `Source` interface, registry and input readers (`sql_source.py` covers every SQL database) |
| `sources/directory.py` | Describes a folder: one entry per file, with file names held to the same rules |
| `inference.py` | Builds nodes from sampled values, for sources without a catalog |
| `privacy.py` | Decides which names are structure and which are data |
| `formats.py` | Recognises string formats, returning only a label |
| `renderers/` | Output formats |
| `config.py` | Reads and merges `datanyx.toml` and `datanyx.local.toml`, and rejects mistakes in them |
| `diff.py` | Compares two schemas and sorts the changes by whether they could break old code |
| `audit.py` | The MCP audit log and its summary: tools, paths and outcomes, never values |
| `scan.py` | Finds personal data and secrets in a folder, by file, kind and line, without values |
| `protect/` | `datanyx protect` and the guard hook: Claude Code permissions, a hook that refuses direct access, MCP config |
| `check/` | Checks SQL and pandas code against a schema: unknown names and type mistakes |
| `scrub.py` | Removes data from error messages and program output, keeping the diagnosis |
| `synth/` | Generates fake data that fits a schema, and writes it as JSON, JSON Lines, CSV or SQLite |
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
| `cli.py` | Command line, which reports errors without quoting data |

## Adding a source

Implement `Source` and add it to the registry in `sources/__init__.py`. Documents, API
responses and CSV rows can reuse `Observation` from `inference.py`, and sources that
know their types up front (like Parquet) can use `build_object` from `sources/catalog.py`.

A SQL database with a SQLAlchemy dialect needs no new source. Add its short URL, driver
and connection timeout argument to `sql_source.py`, add the extra to `pyproject.toml`,
and add a server to the live tests. Anything else with a catalog should read it directly
and build `Object` nodes, with one `Entity` per table, so it never has to query any rows.

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
uv run ruff check src tests
uv run mypy
uv run pytest --cov                  # coverage report
```

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
