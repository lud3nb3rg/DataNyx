<div align="center">

# DataNyx

**Let an AI agent work with your data without ever seeing it.**

DataNyx turns files and databases into value-free schemas an agent can write code against,
and replaces names and identifiers in free text with placeholders.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)](pyproject.toml)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Types: mypy](https://img.shields.io/badge/types-mypy-2a6db2.svg)](https://mypy-lang.org/)

[Getting started](docs/getting-started.md) ·
[Documentation](docs/index.md) ·
[Command reference](docs/cli-reference.md) ·
[Privacy model](docs/privacy-model.md) ·
[Website](https://lud3nb3rg.github.io/DataNyx/) ·
[Changelog](CHANGELOG.md)

</div>

---

You want an agent to write a report, a query or a data-cleaning script, but the data is customers,
patients or payroll. Pasting rows into a chat is a leak, and describing the columns by hand is
tedious. DataNyx sits in between:

|  | What the agent gets | What it never gets |
|---|---|---|
| **Structured data**<br>`datanyx schema` | Field names, types, formats, nesting, primary and foreign keys | Values, examples, enum labels, counts or statistics |
| **Free text**<br>`datanyx anonymize` | The text with `<PERSON_1>`, `<EMAIL_1>` and friends in place of identifiers | The identifiers themselves (best effort: [review the result](docs/anonymization.md#how-reliable-text-anonymization-is)) |

## A first look

```console
$ datanyx schema customers.csv --format text
root: array<{
  id: integer
  email: string<email>
  signed_up: string<date>
  plan: string
  lifetime_value: number
  notes: string | null
}>

$ datanyx anonymize call-notes.txt
<PERSON_1> (<EMAIL_1>) called on <DATE_1> about invoice
<ID_1>. Ms. <PERSON_1> lives in <LOCATION_1>. Her manager <PERSON_2> agreed.
```

The agent sees the shape of the table and the story in the text, and none of the values. It can also
try its code on [fake rows](docs/synthetic-data.md) generated from the schema.

## Install

The PyPI package is the recommended way to install DataNyx:

```sh
uv tool install "datanyx[all]"       # or just datanyx, for CSV, JSON, XML and SQLite
datanyx doctor                       # shows which optional packages and models are installed
```

To use it as a library in a project, run `uv add datanyx` instead. To work on DataNyx or run the newest code,
install from a clone: `git clone https://github.com/lud3nb3rg/DataNyx.git && cd DataNyx && uv sync --all-extras`.

Python 3.10 or newer. [Supported sources](docs/sources.md) lists the extra for each format.

## What's in the documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, a first schema, a first anonymized text |
| [Schemas](docs/schemas.md), [synthetic data](docs/synthetic-data.md) | The core: what the agent sees instead of the data |
| [Debugging](docs/debugging.md), [checking code](docs/checking-code.md) | Fixing code without seeing the data |
| [Scanning](docs/scanning.md), [anonymization](docs/anonymization.md), [pseudonymization](docs/pseudonymization.md), [detection quality](docs/detection.md) | Finding and hiding personal data |
| [Using it with an agent](docs/agents.md) | `datanyx protect`, the guard hook and the MCP server |
| [Installing and deploying](docs/deployment.md) | PyPI, Docker, a pre-commit hook, a GitHub Action, a shared MCP server |
| [Team settings](docs/settings.md), [schema diff](docs/schema-diff.md) | Shared policy, and change detection for CI |
| [Privacy model](docs/privacy-model.md) | What is guaranteed, what isn't, and the known limits |
| [Command reference](docs/cli-reference.md), [Python API](docs/python-api.md) | Every option, and use from code |
| [Contributing](docs/contributing.md) | Architecture, adding a source, tests |

To read it as a site: `uv sync --group docs` then `uv run mkdocs serve`.

## License

[MIT](LICENSE) © 2026 ludenberg
