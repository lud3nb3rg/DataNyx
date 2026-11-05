<div align="center">

# DataNyx

**Let an AI agent work with your data without ever seeing it.**

DataNyx turns files and databases into value-free schemas an agent can write code against,
and replaces names and identifiers in free text with placeholders.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab.svg)](pyproject.toml)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Types: mypy](https://img.shields.io/badge/types-mypy-2a6db2.svg)](https://mypy-lang.org/)

[Quick start](#quick-start) ·
[Supported sources](#supported-sources) ·
[Schemas](#schemas) ·
[Synthetic data](#synthetic-data) ·
[Debugging](#debugging-without-seeing-the-data) ·
[Code checking](#checking-code-before-it-runs) ·
[Scanning](#scanning-a-folder-before-sharing-it) ·
[Team settings](#team-settings) ·
[Documents](#folders-documents-and-streams) ·
[Pseudonymizing](#pseudonymizing-tables) ·
[Schema diff](#watching-a-data-feed-for-changes) ·
[Text anonymization](#text-anonymization) ·
[Using it with an agent](#using-it-with-an-agent) ·
[Security model](#security-model) ·
[Python API](#python-api)

</div>

---

## Why

You want an agent to write a report, a query or a data-cleaning script, but the data is
customers, patients or payroll. Pasting rows into a chat is a leak. Describing the
columns by hand is tedious and error-prone.

DataNyx sits in between:

|  | What the agent gets | What it never gets |
|---|---|---|
| **Structured data**<br>`datanyx schema` | Field names, types, formats, nesting, primary and foreign keys | Values, examples, enum labels, counts or statistics |
| **Free text**<br>`datanyx anonymize` | The text with `<PERSON_1>`, `<EMAIL_1>` and friends in place of identifiers | The identifiers themselves (best effort: [review the result](#how-reliable-text-anonymization-is)) |

## See it work

Given a `customers.csv` like this:

```csv
id,email,signed_up,plan,lifetime_value,notes
1,ana@example.com,2024-01-02,pro,129.5,called twice
2,li@example.org,2024-02-11,free,0,
3,omar@example.net,2024-03-30,pro,310.25,vip
```

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
```

That is everything the agent sees. The same schema can be written as
[TypeScript, Pydantic models or SQL](#output-formats) with `--format`.

To let the agent try its code and see real output, [generate fake rows](#synthetic-data)
from that schema:

```console
$ datanyx synth customers.csv --rows 3 --format csv
id,email,signed_up,plan,lifetime_value,notes
1,jonas.larsen6@example.com,2022-11-26,meadow juniper,967.8,Ember juniper ember zephyr delta timber indigo.
2,lucas.singh19@example.com,2023-06-24,yarrow,73.74,Ripple delta lantern nectar kestrel timber.
3,elena.rossi71@example.com,2025-05-07,quartz indigo,62.28,Yarrow meadow willow.
```

And for text, given a `call-notes.txt` containing:

```
Jessica Whitfield (jessica@northwind.example) called on March 3rd about invoice
INV-20419. Ms. Whitfield lives in Portland. Her manager Tomasz Kowalczyk agreed.
```

```console
$ datanyx anonymize call-notes.txt
<PERSON_1> (<EMAIL_1>) called on <DATE_1> about invoice
<ID_1>. Ms. <PERSON_1> lives in <LOCATION_1>. Her manager <PERSON_2> agreed.
```

The same person keeps the same placeholder, so the agent can still follow who did what.

## Features

- **Fourteen kinds of input.** CSV, TSV, JSON, JSON Lines, YAML, XML, Excel, Parquet, SQLite,
  PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, and whole folders of files.
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

## Quick start

DataNyx isn't published on PyPI yet, so install it from a clone.

```sh
git clone <this repository> datanyx && cd datanyx
uv sync --all-extras                 # or: pip install -e ".[all]"

datanyx schema customers.csv --format text
datanyx doctor                       # shows which optional packages and models are installed
```

Pick only what you need if you'd rather keep it small, for example `pip install -e .` for
CSV, JSON, XML and SQLite, or `pip install -e ".[parquet,excel]"`. The
[sources table](#supported-sources) lists the extra for each format.

Requires Python 3.10 or newer.

## Supported sources

| Source | Input | Install |
|---|---|---|
| CSV, TSV | `.csv`, `.tsv`, `.tab` | built in |
| JSON, JSON Lines | `.json`, `.jsonl`, `.ndjson`, or `-` for stdin | built in |
| XML | `.xml` | built in |
| SQLite | `.db`, `.sqlite`, `.sqlite3`, or `sqlite:///path` | built in |
| YAML | `.yaml`, `.yml` | `.[yaml]` |
| Excel | `.xlsx`, `.xlsm` | `.[excel]` |
| Parquet | `.parquet`, `.pq` | `.[parquet]` |
| PostgreSQL | `postgresql://` | `.[postgres]` |
| MySQL | `mysql://` | `.[mysql]` |
| MariaDB | `mariadb://` | `.[mysql]` |
| SQL Server | `mssql://` | `.[mssql]` |
| Oracle | `oracle://` | `.[oracle]` |
| A folder | Every readable file in it, with `-r` for subfolders | as for the files in it |
| Text and documents (`anonymize`) | Text, HTML, `.eml`, `.docx`, `.pdf`, WhatsApp and Slack exports, a folder of them, or `-` for stdin | `.[text]`, plus a [model](#text-anonymization); PDF needs `.[pdf]` |

Extras are installed as `pip install -e ".[yaml]"`, and `.[all]` installs every reader and
driver. Large JSON files stream with `.[streaming]`, SQL checking needs `.[check]`, PDF needs `.[pdf]`, and the MCP server needs `.[mcp]`.
Other SQLAlchemy URLs, such as `snowflake://`, should work if you install their dialect, but
only the databases above are tested.

## Schemas

```sh
datanyx schema customers.json                   # JSON Schema (default)
datanyx schema customers.json --format text     # compact, fewer tokens for the agent
datanyx schema events.jsonl -o schema.json      # write to a file
datanyx schema shop.db --format text            # SQLite
datanyx schema env:DATABASE_URL                 # server database, URL read from the environment
datanyx schema env:DATABASE_URL --schema sales  # a schema other than the default
cat customers.json | datanyx schema -           # read stdin
datanyx schema orders.csv                       # CSV, TSV, YAML, XML, Excel and Parquet work the same way
datanyx schema ./exports/ -r                    # every readable file in a folder, subfolders too
datanyx schema orders.csv --format typescript   # or pydantic, or sql
```

| Option | Purpose |
|---|---|
| `--format` | `json-schema` (default), `text`, `typescript`, `pydantic` or `sql` |
| `-r`, `--recursive` | For a folder, also read its subfolders |
| `-o`, `--output` | Write to a file instead of stdout |
| `--schema` | Database schema to describe, instead of the database's default |
| `--source` | Set the input type (`csv`, `tsv`, `json`, `jsonl`, `yaml`, `xml`, `excel`, `parquet`, `sql`) when it can't be detected |
| `--map-path` | Hide all keys or columns at a path ([details](#adjusting-what-gets-hidden)) |
| `--keep-name` | Show a name that was hidden automatically ([details](#adjusting-what-gets-hidden)) |

> **Tip:** prefer `env:NAME` over putting a database URL on the command line. A password in
> the command is visible to an agent running it and is kept in shell history.

### What the output looks like

Given this JSON:

```json
[{ "id": 1, "email": "a@example.com", "joined": "2024-01-02",
   "balances": { "d290f1ee-6c54-4b01-90e6-d701748f0851": 12.5 } }]
```

`--format text` produces:

```
root: array<{
  id: integer
  email: string<email>
  joined: string<date>
  balances: {
    [key: string<uuid>]: number
  }
}>
```

The UUID key was recognised as data, so it's hidden and only the shape of its value is kept.

A database produces one entry per table, with its native types and keys:

```
posts: {
  id: integer  -- INTEGER
  author: integer | null  -- INTEGER
}
  primary key (id)
  foreign key (author) -> users (id)

users: {
  id: integer  -- INTEGER
  email: string  -- TEXT
  age: integer | null  -- INTEGER
}
  primary key (id)
```

In JSON Schema output, each table is an entry under `$defs`, with `x-native-type`,
`x-primary-key` and `x-foreign-keys` carrying the database details. Notes about what was
hidden go to stderr, so they stay out of the schema.

### Output formats

| `--format` | Produces |
|---|---|
| `json-schema` | JSON Schema, draft 2020-12 |
| `text` | A compact TypeScript-like form that costs an agent few tokens |
| `typescript` | `export interface` and `export type` declarations |
| `pydantic` | Pydantic v2 models, as Python source |
| `sql` | Portable `CREATE TABLE` statements |

None carry values, and keys that were hidden stay hidden: they become `Record<string, T>`,
`extra="allow"` or a note. Nested data becomes `JSON` columns in SQL, and a database's own
column types are kept as they are. Names that aren't valid identifiers get a Pydantic
`alias`, so the model still reads and writes the real keys.

<details>
<summary><b>How each format is read</b></summary>

| Format | How it's described |
|---|---|
| CSV, TSV | The first row is the header. Cell text is typed by what it plainly is, but `007` stays a string. A short row makes its missing columns optional. |
| Excel | One entry per sheet, first row as the header, cached formula results. A sheet name that looks like data becomes `sheet_N`. |
| Parquet | From the file footer alone: no row is read, and the file's metadata is ignored. Columns, structs, lists and maps are typed from the Arrow schema. |
| YAML | Read with the safe loader, so no tag can run code. Several documents are described as an array. |
| XML | Attributes are keys starting with `-` and text is `$text`. An element that repeats becomes an array. Files with a DOCTYPE or entity declarations are refused. |
| Folder | One entry per file, named after it. A file name that looks like data becomes `file_N`. Fields shared between files are listed in the notes, since they're likely join keys. Links and dotfiles are skipped. |

Files with unusual extensions can be read with `--source csv`, `--source tsv`, `--source xml`
and so on.

</details>

### Adjusting what gets hidden

**`--map-path`** hides everything at a path, for names the automatic rules can't catch.
The main gap is a small map with plain keys, like `{"alice": {...}, "bob": {...}}`,
which looks exactly like a record.

```sh
datanyx schema customers.json --map-path '$.users'   # all keys of an object
datanyx schema customers.json --map-path '$[].tags'  # inside each array item
datanyx schema shop.db --map-path '$.users'          # all columns of a table
```

Paths use `[]` for array items and `.{*}` for the values of a map.

**`--keep-name`** shows a name the rules hid. They favour hiding, so ordinary names like
`Order Details` or `report_2024` get hidden too.

```sh
datanyx schema shop.db --keep-name 'Order Details' --keep-name report_2024
```

The name must match exactly, including case, and is shown everywhere it appears.
`--map-path` still wins if both apply. Only use it for names you've checked. An agent
that can run the tool could also use it to test guesses, such as whether
`alice@example.com` is a key.

## Synthetic data

An agent that can't see results can't debug its code. `datanyx synth` writes fake data that
matches a schema, so the agent can run and test against it, and you run the finished code on
the real data.

```sh
datanyx synth customers.csv --rows 200 --format csv -o fake.csv
datanyx synth shop.db --rows 50 --format sqlite -o fake.db      # a whole database
datanyx synth ./exports/ --format jsonl -o fake/                # a folder: one file per table
datanyx synth events.json --seed 7                              # to stdout, as JSON
```

It reads the input the same way `schema` does and takes the same options (`--map-path`,
`--keep-name`, `-r`, `--schema`, `--source`). Then it generates from the schema alone, so
no real value is copied. Hidden keys stay hidden: they come out as invented ones.

| Option | Purpose |
|---|---|
| `--rows` | Rows per table (default 100) |
| `--seed` | Makes the output repeatable (default 0). Same seed, same data |
| `--format` | `json` (default), `jsonl`, `csv` or `sqlite` |
| `-o`, `--output` | A file, or a folder when there are several tables. Needed for `sqlite`. Existing files are never overwritten |

- **Databases hold together.** Primary keys are unique and a foreign key always points at a
  row that exists, including composite keys and join tables. Nullable foreign keys are
  sometimes null. The `sqlite` output has the same schema as the source database, and passes
  `PRAGMA foreign_key_check`.
- **Shapes match.** Types, formats (emails, dates, UUIDs...), nesting, optional fields, nulls and
  arrays follow the schema. Reading the synthetic file back gives the same schema.
- **Values look plausible.** Field names are used as hints: `city` gets cities, `age` gets
  ages, `price` gets amounts, and a plain `id` counts up. Emails use `example.com`, IPs use
  the documentation range and phone numbers use the fictional `555-01xx` block.
- **Limits.** Values are random, so they don't follow real distributions or relationships
  between columns (the city doesn't match the country). Hidden columns have no counterpart
  in CSV or SQLite and are left out, with a note.

```python
from datanyx import extract, synthesize
from datanyx.synth import render_table

tables = synthesize(extract("shop.db"), rows=50, seed=1)
print(render_table(tables[0], "jsonl"))
```

## Debugging without seeing the data

Even a correct-looking script fails on real data, and its error can hold that data:
`KeyError: 'jane@example.com'`, a path with a username in it, a printed row. `datanyx run`
runs the command for you and shows only a scrubbed view:

```console
$ datanyx run --names-from customers.csv -- python report.py
Traceback (most recent call last):
  File "./report.py", line 5, in <module>
    raise RuntimeError(f"no billing record for {r['email']} (customer Li, balance {r['lifetime_value']}0000)")
RuntimeError: no billing record for <EMAIL_1> (customer <PERSON_1>, balance <ID_1>)
datanyx: exit code 1; stdout hidden (no output)
```

The exception type, the file and line, and the code stay, so the agent can still fix the
bug. The values are gone. The command's exit code is passed on.

```sh
datanyx run -- python report.py                    # stderr scrubbed, stdout reduced to its size
datanyx run --stdout scrubbed -- python report.py  # also show a scrubbed copy of stdout
datanyx run --save-stdout out.txt -- python report.py   # you read the real output, the agent doesn't
datanyx scrub error.log                            # the same filter for text you already have
```

Pass `--names-from customers.csv` (or a database, folder or `env:NAME`) so that column names
in errors survive: `KeyError: 'plan'` stays readable, while `KeyError: 'Plan'`, which isn't a
name the schema shows, is hidden.

| Option | Purpose |
|---|---|
| `--names-from INPUT` | Keep quoted names that this input's schema shows (repeatable) |
| `--allow NAME` | Keep one more quoted name (repeatable) |
| `--hide TERM` | Always remove this term (repeatable) |
| `--stdout` | `shape` (default: only its size), `scrubbed` or `none` |
| `--timeout SECONDS` | Stop the command after this long (exit code 124) |
| `--save-stdout FILE`, `--save-stderr FILE` | Also write the real output to a new file, for you |
| `--keep-numbers` | Don't replace long numbers |
| `--no-ner` | Skip the name-finding model: faster, but names in unquoted text get through |

**How text is cleaned**, in order:

1. Secrets go: the values of environment variables that look like credentials, and the
   password inside a URL.
2. Your home folder, working folder and username are shortened to `~`, `.` and `<USER>`.
3. In a Python traceback the `File` lines and the code under them are left alone. They are
   your script, not the data. Every other line is a message.
4. In a message, anything in quotes is replaced unless it is a name from the schema. Errors
   quote values far more often than they quote names.
5. The same detectors as [text anonymization](#text-anonymization) replace emails, IDs and,
   with the model, names. One person keeps one placeholder.
6. Long numbers and decimals are replaced. Short integers stay, since they are mostly line
   numbers and counts.

**Limits.** It's best effort, like anonymization. A value that is neither quoted nor
recognised, such as a bare name the model misses, gets through. A script that prints a table
of numbers shows them under `--stdout scrubbed` unless they are long. `run` doesn't sandbox
anything: the command can read whatever you can, so `--stdout shape` (the default) is what keeps
printed rows from the agent. Like `anonymize`, it stops if the spaCy model is missing unless
you pass `--no-ner`.

```python
from datanyx.scrub import ScrubPolicy, scrub

print(scrub(error_text, ScrubPolicy(allowed_names=frozenset({"plan", "email"}))))
```

## Checking code before it runs

Most of an agent's mistakes against data are about names: a column spelled `emial`, a join
on the wrong table, `plan = 3` against a text column. `datanyx check` finds these from the
schema, without running anything and without the code touching any data.

```console
$ datanyx check query.sql --against customers.csv
query.sql:1:8: error: unknown column 'emial'
    hint: did you mean 'email'?
query.sql:3:25: warning: comparing text column plan with the number 3
    hint: quote it, or cast the column
query.sql:3:38: warning: comparing with NULL using = or <> is never true
    hint: use IS NULL
query.sql:5:44: error: unknown table 'orders'
    hint: known tables: 'customers'
2 errors, 2 warnings
```

```sh
pip install -e ".[check]"                                  # SQL checking needs sqlglot
datanyx check query.sql --against shop.db                  # a database: dialect and column types come from it
datanyx check analyze.py --against customers.csv           # pandas code
datanyx check report.sql --against ./exports/ --strict     # a folder of files; warnings fail too
datanyx check report.sql --against export.csv --table sales  # name the table your SQL uses
datanyx check - --language sql --against env:DATABASE_URL < query.sql
```

It exits with 1 if there are errors (or any finding with `--strict`), so it works as a gate in
a script or CI. `--format json` gives the findings as data.

**SQL** (any dialect sqlglot knows: `--dialect postgres`, `mysql`, `tsql`, `oracle`, `sqlite`):

- unknown tables and columns, with a close match suggested; columns qualified by the wrong
  table or alias; ambiguous columns in a join
- CTEs, subqueries, select aliases, `*` and correlated subqueries are understood
- columns in `UPDATE`, `DELETE` and `INSERT`
- type mistakes: a number compared with text (or the reverse), a date with a non-date, `SUM` or
  `AVG` over text, `LIKE` on a number, a join between a number and text, and `= NULL`

**Python and pandas.** A DataFrame is followed only while it clearly is one: read straight from a
file the schema covers, then copied, filtered, sorted or subset. Anything else (a merge, a
function's result) ends the tracking, so the checker stays quiet when it can't be sure.

- column names in `df["a"]`, `df[["a", "b"]]` and `df.a` (the last needs pandas installed)
- names passed to `groupby`, `sort_values`, `merge`, `pivot_table`, `drop`, `rename`, `set_index`
  and similar, with case-sensitivity hints (`'Plan'` vs `'plan'`)
- columns the script adds itself are known afterwards
- pandas mistakes that don't raise errors: `.mean()` on a text column, `.dt` on a column pandas
  read as text, `.str` on numbers, a text column compared with a number
- SQL in string literals passed to `execute`, `read_sql` and similar is checked too

**What it can't tell you.** It doesn't know your data, only the schema: a column that was
hidden isn't known, so a name that isn't in the schema is a warning, not an error, when the
schema hides some names. File-based schemas are sampled, so a type can be wrong for rows it
didn't see. And it doesn't run the code: it finds likely mistakes, not every mistake.

**It can't be used to find hidden names.** A name the schema hides is treated exactly like one
that doesn't exist, in the message, the severity and the suggestions, so asking about a name
reveals nothing. A file whose name looks like data is called `data`, though your code can still
use its real name.

```python
from datanyx.check import check_code, format_diagnostics, load_tables

tables, dialect = load_tables(["shop.db"])
print(format_diagnostics(check_code("SELECT emial FROM users", tables, "sql", dialect)))
```

## Scanning a folder before sharing it

Before pointing an agent at a repository or an export folder, find out what is in it.
`datanyx scan` looks through the files for personal data and credentials and reports where they
are, without printing a single value.

```console
$ datanyx scan ./project
Scanned 214 files (3 binary skipped).

4 files with findings:
  data/customers.csv   EMAIL 1,204 · PHONE 311 · ADDRESS 18
  config/prod.env      SECRET 3
                       SECRET on lines 4, 9, 12
  src/seed.py          EMAIL 2
                       EMAIL on lines 41, 58
  file_7               EMAIL 3
                       EMAIL on lines 2, 3, 4

Total: EMAIL 1,209 · PHONE 311 · ADDRESS 18 · SECRET 3

Not scanned, but these binary data files may hold personal data: exports/people.xlsx, db/app.sqlite

note: 1 file name looked like data, so they are shown as file_N. Use --show-paths to see real paths,
and run it yourself rather than through an agent.

Names of people, companies and places are only found with --ner.
```

```sh
datanyx scan ./project                         # patterns for emails, phones, cards, IBANs, IPs, addresses, secrets
datanyx scan ./project --ner                   # also names, organisations and places (slow)
datanyx scan ./project --kinds EMAIL --kinds SECRET
datanyx scan ./project --format json           # for scripts
datanyx scan ./project --fail-on-findings      # exit 1 if anything is found: a CI step or pre-commit hook
datanyx scan ./project --exclude 'fixtures' --exclude '*.lock'
```

| Option | Purpose |
|---|---|
| `--kinds` | What to look for (repeatable). The default is `EMAIL`, `PHONE`, `CREDIT_CARD`, `IBAN`, `IP_ADDRESS`, `MAC_ADDRESS`, `ADDRESS` and `SECRET`. `all` adds dates, money, URLs and bare IDs, which match too much ordinary code to be useful by default |
| `--ner` | Also find names, organisations and places with the NER model. Slow, so it is opt-in |
| `--exclude GLOB` | Skip names matching a pattern (repeatable). `.git`, `node_modules`, virtual environments and caches are skipped already |
| `--max-size MB` | Skip larger files (default 5) |
| `--include-examples` | Also count `example.com` emails and loopback or documentation IP addresses, which are ignored by default |
| `--show-paths` | Show real file names, including ones that look like data |
| `--fail-on-findings` | Exit with 1 if anything is found |

**Secrets** are found by their shape: private key headers, AWS, GitHub, Slack, Google and Stripe
keys, JWTs, the password in a `scheme://user:password@host` URL, and values assigned to names
like `password`, `api_key` or `client_secret`. Placeholders (`changeme`, `${SECRET}`,
`os.environ[...]`, a function call) are not counted. `SecretDetector` is also usable on its own
in Python and with `anonymize`.

**What a report reveals.** Counts, kinds and line numbers, never values. A file name is shown only
if it passes the same test as a column name, since `jane_doe_medical.csv` is itself sensitive;
others are `file_N` until you pass `--show-paths`. Run it yourself, or let an agent run it
through the MCP server's `scan_folder` tool, which always hides such names.

**Limits.** It finds what patterns find, so it misses names (without `--ner`), free-text
descriptions of a person, and anything in a binary file: spreadsheets, Parquet files and databases
are listed as not scanned, because they can't be read as text. Phone numbers are matched more
strictly than in anonymization (dates, version numbers and IPs are not counted), so some real
ones in unusual formats can be missed. Expect some false positives, such as test fixtures; the
scan of this repository finds the fake cards and keys in its own tests.

## Team settings

Repeating flags on every command doesn't scale, and a policy that lives in someone's shell history
can't be reviewed. Put it in `datanyx.toml` at the root of the project and commit it:

```sh
datanyx init            # a starter file with every setting commented out
datanyx init --local    # datanyx.local.toml, for what must stay private (added to .gitignore)
datanyx config          # the settings in effect, and the files they came from
```

```toml
# datanyx.toml
[schema]
map_paths = ["$.users"]                  # hide every key under these paths
keep_names = ["Order Details"]           # show names the rules hid

[anonymize]
model = "en_core_web_lg"
keep_entities = ["DATE"]
detectors = ["detectors/internal.toml"]  # custom detectors (below)

[scrub]
allow = ["plan"]                         # quoted names to leave readable in errors
names_from = ["data/customers.csv"]      # ...and the names this input's schema shows
ner = false                              # skip the name model

[scan]
exclude = ["fixtures", "*.lock"]
kinds = ["EMAIL", "SECRET"]
max_size = 5

[mcp]
roots = ["data"]
audit_log = "datanyx-audit.jsonl"
```

| Section | Used by |
|---|---|
| `[schema]` | `schema`, `synth`, `check`, `diff`, and `scrub --names-from` |
| `[anonymize]` | `anonymize`, `scrub` (the `hide` terms), and the MCP server's `anonymize_file` |
| `[scrub]` | `scrub` and `run` |
| `[scan]` | `scan` |
| `[mcp]` | `datanyx mcp` |

- **Where it's found.** In the current folder or the nearest parent, like git. `--config FILE` or the
  `DATANYX_CONFIG` variable names one instead, and `--no-config` ignores it.
- **Command line wins.** Lists from the command line are added to the file's, and single values such as
  `--model` or `--max-size` override it.
- **Mistakes are errors.** An unknown section or setting is reported with a suggestion
  (`unknown setting 'hid' in [anonymize]; did you mean 'hide'?`), because a misspelled `hide` would
  otherwise protect nothing. A value is never repeated in an error message.
- **Paths** in the file (`detectors`, `names_from`, `roots`, `audit_log`) are relative to the file.
- **Terms to hide stay private.** `datanyx.local.toml` is merged on top of `datanyx.toml`: lists are
  joined and single values in the local file win. Put the client and company names you are protecting
  there, and keep it out of git. `datanyx config` counts them and shows them only with `--reveal`.
  [`datanyx protect`](#enforcing-it-with-datanyx-protect) stops an agent reading that file or editing
  either one.

### Custom detectors

For formats and names that are yours alone, describe a detector instead of writing Python. Put the
entries in `datanyx.toml`, or in a separate file named by `detectors = [...]` or `--detectors FILE`:

```toml
[[pattern]]
kind = "EMPLOYEE_ID"          # the placeholder becomes <EMPLOYEE_ID_1>
regex = 'EMP-\d{6}'
flags = "i"                   # optional: any of i, m, s
group = 1                     # optional: mark only this part of the match

[[dictionary]]
kind = "CLIENT"
terms = ["Acme", "Northwind"]
terms_file = "clients.txt"    # optional: one term per line, # for comments
ignore_case = true            # the default
word_boundaries = true        # the default: "Acme" doesn't match inside "Acmeville"
```

- Kinds are capital letters and underscores. They are found by `anonymize`, `scrub`, the MCP server's
  `anonymize_file`, and `scan`, which counts them by default and accepts them in `--kinds`.
- A custom kind wins a tie over a built-in one on the same text, so a ticket number is
  `<TICKET_1>` rather than a generic `<ID_1>`. `--keep-entity TICKET` leaves a kind visible, and
  `restore` puts custom placeholders back like any other.
- A bad regex, an unknown key or a missing terms file is an error that never repeats a term. Terms
  can be sensitive, so a dictionary of real client names belongs in `datanyx.local.toml` or another
  file you don't commit.

## Watching a data feed for changes

Code written against a data feed breaks when the feed changes shape. Save its schema once, then
compare against it whenever a new export arrives:

```sh
datanyx schema feed.csv -o schema.snapshot.json     # once; commit the snapshot
datanyx diff schema.snapshot.json feed.csv          # on every new export, in CI
```

```console
$ datanyx diff schema.snapshot.json feed.csv
2 breaking, 1 compatible

breaking:
  ~ $[].email: string<email> -> string<email> | null  (can now be null)
  - $[].notes: removed

compatible:
  + $[].score: added  (number, required)
```

Either side can be a saved schema or any input DataNyx reads: another file, a folder, a database.

| Change | Counted as |
|---|---|
| A field or table removed | **breaking** |
| A type widened: a number may now be text, a field may now be null, an integer may now have decimals | **breaking** |
| A format lost, or changed (`email` to none, `date` to `date-time`) | **breaking** |
| A field that was always there is now optional | **breaking** |
| A field or table added | compatible |
| A type narrowed, a format gained, an optional field now always present | compatible |
| A database type changed, a primary or foreign key changed, key names newly hidden | info |

`--fail-on breaking` (the default) exits with 1 when anything breaking changed, `--fail-on any` for any
difference and `--fail-on never` to just report. `--format json` is for scripts, and the options for
reading inputs (`--source`, `--schema`, `-r`, `--map-path`, `--keep-name`) work as in `datanyx schema`.
Nested changes have paths such as `$.user.address.zip` or `users[].tags[]`. The MCP server has a
`schema_diff` tool.

A schema built from a file is built from what the file contains, so a difference can come from the
data in a new export, not only from its design: a column that was never empty can show up as nullable.

## Text anonymization

```sh
uv sync --all-extras --group models         # spaCy plus en_core_web_lg and fr_core_news_md
# or: pip install -e ".[text]" && python -m spacy download en_core_web_lg   (about 400 MB)

datanyx anonymize notes.txt                 # print to stdout
datanyx anonymize notes.txt -o clean.txt    # write to a file
cat notes.txt | datanyx anonymize -         # read stdin
```

Given this text:

```
Jessica Whitfield (jessica@northwind.example) called on March 3rd about invoice
INV-20419. Ms. Whitfield lives in Portland. Her manager Tomasz Kowalczyk agreed.
```

it produces:

```
<PERSON_1> (<EMAIL_1>) called on <DATE_1> about invoice
<ID_1>. Ms. <PERSON_1> lives in <LOCATION_1>. Her manager <PERSON_2> agreed.
```

A name found once is hidden everywhere it appears, including parts of it like the surname
above. Two people sharing a surname aren't linked by it. Spacing and line endings are
preserved.

| Option | Purpose |
|---|---|
| `-o`, `--output` | Write to a file instead of stdout. It refuses to overwrite the input |
| `--model` | spaCy model, such as `fr_core_news_md` for French text |
| `--hide` | Always hide this term, ignoring case (repeatable) |
| `--keep` | Leave this exact term visible (repeatable) |
| `--keep-entity` | Leave a kind of entity visible, such as `DATE` (repeatable) |
| `--mapping` | Also save each placeholder's real value to a new file ([details](#restoring-real-values)) |

**What it hides**

- **Using the NER model:** `PERSON`, `ORG`, `LOCATION`, `GROUP` (nationalities, religious
  and political groups), `DATE`, `MONEY` and `MISC`
- **Using patterns:** `EMAIL`, `URL`, `IP_ADDRESS`, `MAC_ADDRESS`, `CREDIT_CARD`, `IBAN`,
  `PHONE`, `ADDRESS`, `ID` (long numbers, UUIDs, keys and references), plus common `DATE`
  and `MONEY` formats in several languages

If spaCy or the model isn't installed, it stops with an error and outputs nothing. It
doesn't fall back to patterns alone, because names would pass through unnoticed.

Use `--hide` for names you already know, such as your company or clients: it's the one
reliable way to catch them. Run it yourself rather than through an agent, since the term
is on the command line.

### Folders, documents and streams

`anonymize` takes a folder as well as a file. Every document in it is anonymized with **one set of
placeholders**, so a person in the tenth file is the same `<PERSON_1>` as in the first, and one
mapping file restores the whole folder:

```sh
datanyx anonymize ./reports -o ./clean -r --mapping reports.mapping.json
datanyx restore ./clean -o ./restored --mapping reports.mapping.json
```

Output files are named `file_001.txt`, `file_002.docx`... because a file name can be as sensitive as
what is in it, and the mapping records what each was called. `--keep-names` keeps the real names
when you know they are safe. `-r` reads subfolders. A file DataNyx can't read is skipped and counted,
and one that fails doesn't stop the rest. An output folder inside the input, or one that already
has a file of the same name, is refused.

| Format | Result |
|---|---|
| Text, Markdown, logs, CSV, JSON and similar | The same file, with placeholders. Line endings are kept |
| HTML | The same page: tags and layout stay, text and descriptive attributes (`title`, `alt`, `href`, `value`...) change. Text with nothing to hide keeps its original spelling |
| Word (`.docx`) | The same document: styles, tables and layout stay. A paragraph with something to hide loses the formatting *inside* it, and author, company and title are cleared |
| Email (`.eml`) | A text file of the headers and body. People become placeholders (a sender is `<PERSON_1> (<EMAIL_1>)`), routing headers and attachments are left out, and attachment names are listed |
| PDF | A text file of the pages, with page markers (needs `.[pdf]`) |
| WhatsApp export | The same chat, each sender one placeholder, so who said what is still clear. Continuation lines and system messages are handled |
| Slack export | The same JSON. People, user ids, emails and message text are replaced, mentions line up with the people, and timestamps and types stay |

Anything else is read as plain text if you name the file, and skipped inside a folder.

**Streams.** `--stream` anonymizes stdin a line at a time as it arrives, so it works on a live log.
The same names keep the same placeholders for as long as it runs, and a `--mapping` file is rewritten
as it grows, so it survives a stopped stream:

```sh
tail -f app.log | datanyx anonymize - --stream --mapping app.mapping.json
```

**What these don't do.** Each line of a stream is judged on its own, so a name split across lines isn't
joined. Inside HTML the contents of `<script>` and `<style>` are left alone, so an email in a script
stays: check scripts yourself. Images in Word files and the pictures in a PDF aren't read, and a
scanned PDF has no text to find; the notes printed after a run say when that happened. Changed HTML
text is re-escaped, so `&nbsp;` becomes a plain non-breaking space. WhatsApp's two usual layouts are
recognised, and other chat apps aren't. A person is matched by exact name across documents: "Whitfield"
alone in a later file isn't linked to "Jessica Whitfield" from an earlier one.

### Restoring real values

If an agent writes something using placeholders, you can put the real values back. This
is opt-in, and no mapping is saved unless you ask for one:

```sh
datanyx anonymize notes.txt -o clean.txt --mapping notes.mapping.json
# ...the agent works from clean.txt and writes summary.txt...
datanyx restore summary.txt --mapping notes.mapping.json -o summary.real.txt
```

> **The mapping file holds the data you're hiding, in plain text.** Treat it like the
> original: keep it where the agent can't read it, and delete it when you're finished. Don't
> run `restore` through an agent either. It requires `-o`, so real values don't appear in
> the command's output, but an agent could still open the result.

- `*.mapping.json` is ignored by git here, so use that ending.
- An existing mapping is never replaced, since placeholders only mean something for the run
  that made them. Use a new file for each run.
- A placeholder covers every form of a name and restores to the longest, so `Ms. <PERSON_1>`
  becomes `Ms. Jessica Whitfield` even if the original said `Ms. Whitfield`.
- Placeholders not in the mapping, such as ones an agent invented, are left as they are and
  listed in a note.
- The mapping file is made readable by its owner only, on Linux, macOS and Windows.

### How reliable text anonymization is

> **Treat the output as a draft to review, not a guarantee.** A schema contains no data by
> construction, but anonymized text is your original text minus whatever got detected.

These are results for `en_core_web_lg` on 72 invented names from 12 regions in 6 sentence
templates. It's a small synthetic test, not a real-world accuracy figure:

| Name appears as | Hidden |
|---|---|
| First name only | 98.6% |
| Full name in ALL CAPS | 97.0% |
| Full name, capitalized normally | 95.6% |
| Whole line in lowercase | 94.2% |
| Whole line in ALL CAPS | 93.1% |
| Full name in lowercase, in a normally capitalized sentence | 83.3% |

- **Unfamiliar names get missed more often.** Most misses were East Asian and Pacific
  names, or names with accents. Don't rely on this for one population without testing it.
- **Context isn't detected.** "The CEO's daughter" or a rare diagnosis in a small town can
  identify someone without containing a name, and nothing here catches that.
- **It over-hides sometimes.** Words like `quarterly` count as dates, and capitalized words
  like `Branch` can be taken for organisations.
- **Model labels differ.** French models have no `DATE` or `MONEY` label, so those rely on
  patterns. `fr_core_news_md` is the only non-English model tested.

## Pseudonymizing tables

Text anonymization turns names into placeholders. For tables, that would break joins and make emails
stop looking like emails. `datanyx pseudonymize` writes a copy in which each real value becomes a
**consistent fake one**: the same customer is the same fake customer in every row, table and file,
ids still match across tables, and the data stays realistic enough to develop against or share.

```console
$ datanyx pseudonymize customers.csv -o fake.csv --key-file pseudonyms.key
Plan:
  customer_id  id          the name ends in id
  name         name        the name says name
  email        email       the name says email
  phone        phone       the name says phone
  city         city        the name says city
  plan         keep        nothing personal was recognised   <- kept: check it
  salary       keep        nothing personal was recognised
  notes        text        the name says free text
  joined       keep        dates are kept, except birth dates

Kept as they are, and holding text: plan. Check them, and use --column NAME=KIND or --strict.
Wrote 3 rows to fake.csv. Replaced: 3 id, 3 name, 3 email, 3 phone, 3 city, 1 text.
```

```sh
datanyx pseudonymize customers.csv --plan                     # see the decisions, write nothing
datanyx pseudonymize shop.db -o shop-fake.db --key-file pseudonyms.key   # a database, rebuilt in a new file
datanyx pseudonymize events.jsonl -o fake.jsonl --column notes=text --keep-column region
datanyx pseudonymize customers.csv -o fake.csv --strict       # replace text that wasn't recognised too
```

It reads CSV, TSV, JSON, JSON Lines and SQLite files. **It reads the real values, so run it yourself
rather than through an agent**; `datanyx protect` refuses to run it for an agent. Keep the key and
mapping files inside a path you protect, or outside the project, since the guard doesn't know about them.

| Option | Purpose |
|---|---|
| `-o`, `--output` | The new file. Existing files are never overwritten, and a database is rebuilt, not edited |
| `--plan` | Show what each column would become, and stop |
| `--key-file FILE` | A secret key, created if missing. **With the same key, the same value gets the same fake in every run**, so a fresh export lines up with the last. Without it, a new random key is used and the fakes won't match another run's |
| `--mapping FILE` | Also save which fake stands for which real value. It holds real data |
| `--column NAME=KIND` | Treat a column as this kind (repeatable); `TABLE.COLUMN=KIND` for one table of a database |
| `--keep-column NAME` | Leave a column as it is |
| `--strict` | Replace text columns that weren't recognised, instead of keeping them |
| `--source` | The input type, if the extension doesn't say |

**What becomes what.** A column's kind comes from your override, `--keep-column`, being a key in a
database, its name, then what its values look like. The kinds are `name`, `first_name`, `last_name`,
`email`, `phone`, `address`, `city`, `company`, `postal`, `id`, `token`, `date`, `text`, `ip`, `url`,
`category` (distinct words), `keep` and `drop`.

- **Names, emails, companies, cities and addresses** come from word lists, so they look real. Two
  different real names never get the same fake. Emails are `first.last42@example.com`.
- **Whole-number ids** go through a keyed permutation of their own range, so the result has the same
  number of digits and **two ids can never become one**. That is what keeps foreign keys valid: the
  `PRAGMA foreign_key_check` of a pseudonymized database is clean, and joins return the same row counts.
- **Phone numbers, postal codes, account numbers and other codes** keep their shape: a digit becomes a
  digit, a letter a letter, `+44` and the punctuation stay.
- **Dates** are kept, except birth dates, which move by one secret number of days, so the gaps between
  them survive. A date not written as `YYYY-MM-DD` is kept and counted.
- **Free text** goes through [text anonymization](#text-anonymization), so names inside it become
  placeholders like `<PERSON_1>` (these don't match the fake name in the `name` column).
- **JSON keys** work the same as columns, wherever the key appears. A key that is itself data, such as
  an email used as a key, is replaced, and agrees with the same email used as a value.
- **SQLite**: tables, keys and indexes are copied, and views too. **Triggers are left out**, since they
  can hold data or change values, and virtual tables are skipped; the notes at the end say so.

**What it can't promise.** *Pseudonymized is not anonymous.* Look at the plan before you share the copy.

- Anything kept is exactly as it was: numbers (a salary), categories (a diagnosis in a `plan` column) and
  most dates. A few such columns together, such as postcode, birth year and sex, can identify a person
  even when every name is gone. Columns that hold text but weren't recognised are listed with
  "check it"; `--strict` replaces them.
- A column is recognised by its name and its values, which can be wrong in both directions. That is why
  the plan is printed, and `--column` exists.
- Anyone with the key file can test a guess ("is this the fake for Ana Silva?"), so keep it with the
  real data, not with the copy. The key file is readable by its owner only.
- A phone number or postal code made by changing digits can, by chance, be a real one.

```python
from datanyx.pseudonymize import Options, Pseudonymizer, load_key, pseudonymize_file

pseudo = Pseudonymizer(load_key("pseudonyms.key"))
pseudonymize_file("customers.csv", "fake.csv", "csv", pseudo, Options())
print(pseudo.apply("email", "jane@acme-corp.io"))      # the same fake every time
```

## Using it with an agent

1. **Run DataNyx yourself**, and read the schema before sharing it.
2. **Give the agent only the schema**, and block its access to the data file or
   database. DataNyx produces a schema but doesn't restrict the agent on its own: if it can
   open the file, it can still see the data. [`datanyx protect`](#enforcing-it-with-datanyx-protect)
   sets this up for Claude Code.
3. **Run the agent's code yourself** against the real data. Code that prints rows or
   logs values would expose them if the agent sees that output.

If the agent runs DataNyx itself, its output is still safe to show, as long as you
accept that `--keep-name` lets it [test guesses](#adjusting-what-gets-hidden).

### Enforcing it with `datanyx protect`

```sh
datanyx protect ./data                      # shows what it would change
datanyx protect ./data --write              # writes it, then checks that the guard works
datanyx protect ./data --write --command "uv run datanyx"   # if datanyx isn't on Claude Code's PATH
datanyx protect --remove --write            # takes it all out again
```

For Claude Code this writes, and only with `--write`:

- **`.claude/settings.json`** (or `settings.local.json` with `--local`): deny rules for `Read` and
  `Edit` on the paths, plus a `PreToolUse` guard hook. Anything already in the file is kept.
- **`.mcp.json`**: a `datanyx` MCP server limited to those paths (`--no-mcp` skips it).
- **`.datanyx/protect.json`**: the protected paths, which the hook reads.

Running it again replaces its own entries rather than adding more, and a settings file that
isn't valid JSON is left alone. Paths outside the project are covered by the hook only, since
the deny rules are relative to the project.

**What the guard refuses.** Reading, searching or listing a protected path with Read, Grep or
Glob. Shell commands that name one: `cat data/x.csv`, `ls data`, `cd data && ...`, `cat data/*.csv`,
`python -c "open('data/x.csv')"`, and absolute or Windows-style paths. Recursive searches from a
folder that has protected data inside it (`grep -r foo .`, `rg foo`, `find . -exec ...`); pass
`--allow-broad-search` to allow those. Changes to the protected data and to the files that set
the protection up. Every refusal says what to run instead.

**What it lets through**, because they work with the data without showing it: `datanyx schema`,
`synth`, `check`, `scrub`, `anonymize`, `run`, `scan`, `diff`, `audit` and `config`. `run` is refused if it saves raw output to a
file or if the command after `--` names the data, and `anonymize --mapping`, `restore`, `pseudonymize`, `init` and
`protect` are refused. So are the options that loosen what you set up, since each shows something
hidden or works as a way to test guesses: `--keep-name`, `--keep`, `--keep-entity`, `--allow`,
`--keep-numbers`, `--no-ner`, `--show-paths`, `--include-examples`, `--reveal`, `--allow-urls`,
`--config` and `--no-config`. The agent can't edit `datanyx.toml` or `datanyx.local.toml`, and can't
read the local one, which lists the terms you hide. Running a script that reads the data, like
`datanyx run -- python report.py`, is the intended way to work.

**It fails closed.** The hook is written with `onFailure: "block"`, so if it can't run, tool calls
are blocked instead of slipping through. `--write` runs the hook once and tells you if the
command doesn't start. A bad command would block everything, so fix it before using Claude
Code in that folder. The hook uses the program-and-arguments form of a hook command, which
needs a recent Claude Code.

**What it isn't.** A guardrail, not a sandbox. It can't see inside a script (one that prints rows
is not caught), or follow a path built at run time such as `$DATA/x.csv`, and it matches shell
commands by their text. It governs Claude Code's tools only: other agents, a terminal, or someone
who edits the settings themselves aren't stopped. For hard isolation, use a separate account or
container with no access to the data, or [a database login that can't read rows](#a-login-that-cant-read-rows).

### As an MCP server

```sh
datanyx mcp --root ./exports        # stdio; needs the `mcp` extra
```

Add it to your agent's MCP configuration:

```json
{
  "mcpServers": {
    "datanyx": {
      "command": "datanyx",
      "args": ["mcp", "--root", "./exports"]
    }
  }
}
```

The agent gets six tools: `describe_data` (a schema for a file, folder or `env:NAME`
database), `synthetic_sample` (up to 200 fake rows that match it), `check_code` (checks its
SQL or pandas code against that schema), `scan_folder` (which files hold personal data or
secrets, by kind and count), `schema_diff` (what changed between two schemas) and
`anonymize_file` (a text file with placeholders).
It's limited by design:

- It can only read under `--root` (default: the current folder), and database URLs are
  refused unless you start the server with `--allow-urls`. Use `env:NAME` instead.
- There's no `keep` option, because keeping a name visible tests whether it exists. Choose
  `--keep-entity` when you start the server instead.
- There's no restore tool and no mapping, since both hold the real values.
- Errors are fixed messages, never the library's own text.

**An audit log.** `--audit-log FILE` (or `audit_log` under `[mcp]`) appends one line per tool call:

```sh
datanyx mcp --root ./data --audit-log audit.jsonl
datanyx audit audit.jsonl --since 2025-01-31         # who asked about what, and what was refused
```

```console
$ datanyx audit audit.jsonl
42 calls in 3 sessions, 2025-01-31T09:12:03+00:00 to 2025-02-02T16:40:51+00:00

By tool: describe_data 20 · check_code 15 · scan_folder 5 · synthetic_sample 2

Paths asked about:
  data/customers.csv  ×27
  ../secrets  ×2

2 calls failed or were refused:
  describe_data: that path is outside the folders this server may read  ×2
```

Each record has the time, a session id, the tool, the paths and settings it was called with, whether it
worked, and sizes. It never has a value or anything the agent was given back: the SQL an agent asked to
have checked is recorded as a length, the terms it asked to hide as a count, and a password in a database
URL is replaced. The file is readable by its owner only. Keep it where the agent can't change it.

The server doesn't stop the agent from opening the files itself. Block that in its
permissions, as above.

## Security model

<details open>
<summary><b>What protects the data</b></summary>

**Values can't reach the output.** Each value is reduced to a type and a format label
(`email`, `uuid`, `date`, `date-time`, `uri`, `ipv4`, `ipv6`) and then discarded. The
schema model has no field that could hold a value.

**Keys are treated as possible data.** A JSON key is hidden, and its value's structure
described under `[key: ...]` / `additionalProperties`, when any of these apply:

| Signal | Example |
|---|---|
| Its shape looks like data | emails, UUIDs, dates, URLs, phone numbers, IPs, numbers, tokens, keys containing spaces |
| It sits among mostly data-like keys and holds the same kind of value | `bob` beside `al@example.com` |
| It rarely recurs across many objects (5 or more seen) | a key found in only 1 of 1,000 records |
| The object has many same-shaped entries | 8+ same-shaped objects, or 32+ values of one scalar type |

**Database names get the same shape check.** A hidden table is left out completely. A
hidden column is described under `[key: ...]`. A primary or foreign key that would name
something hidden is dropped.

**Database rows are never queried.** The schema comes from the catalog alone, and the
tests record every statement to check this. What enforces it depends on the database:

- **SQLite** files open read-only, with an authorizer that makes SQLite itself refuse
  to read a user table or change anything.
- **Server databases** have no client-side equivalent, so use
  [a login that can't read rows](#a-login-that-cant-read-rows). Then the server
  enforces it.

SQLAlchemy's inspector does fetch column defaults, comments and `CREATE` statements,
which can hold literals such as `DEFAULT 'a@example.com'`. They stay in memory inside
the tool and are never copied into the schema. Enum labels are stripped from native
types, so `ENUM('a','b')` shows as `ENUM`.

**Every output channel is covered.** An agent may run the tool itself, so stderr counts
too.

- Parse errors give only a position.
- Database and unexpected errors give only an error type, because their messages can
  quote statements and connection details.
- SQLAlchemy's warnings are silenced during extraction, because they can name hidden
  columns.
- Notes never contain hidden names, and the input filename and database URL aren't
  included in the schema.

</details>

### A login that can't read rows

These grants were tested against real servers. In each case the login can get the
schema, and its attempt to `SELECT` from a table is refused.

| Database | Setup |
|---|---|
| PostgreSQL | `CREATE ROLE reader LOGIN PASSWORD '...'` with no further grants |
| MySQL, MariaDB | `CREATE USER`, then `GRANT REFERENCES ON mydb.* TO reader` |
| SQL Server | `CREATE LOGIN` and `CREATE USER`, then `GRANT VIEW DEFINITION TO reader` |
| Oracle | `CREATE USER`, `GRANT CREATE SESSION`, then `GRANT REFERENCES ON each_table TO reader`, and run with `--schema owner` |

On Oracle, a table without the grant doesn't appear in the schema.

## Known limits

- **Field names are shown.** They're what make the schema useful, so a sensitive name
  that looks ordinary will get through. **Read the schema before sharing it.**
- **Text anonymization can miss things**, as [measured above](#how-reliable-text-anonymization-is).
- **Structure can be sensitive in itself.** A field called `hiv_test_result` says
  something even without a value.
- **JSON schemas reflect the sample.** `required` and formats describe the data seen,
  and hidden keys can include real optional fields.
- **CSV has no types.** Types come from the text, so a column of numbers with one `n/a`
  becomes a string, and a header row is assumed.
- **Large files.** JSON over 64 MB is streamed if `ijson` is installed (`.[streaming]`),
  and otherwise loaded fully into memory. JSON Lines and CSV are read row by row. XML and
  YAML are loaded fully into memory. Error messages for streamed JSON have no line number,
  since the parser's own text can quote the data.

<details>
<summary><b>Database limits</b></summary>

- **Coverage is tables only.** Views, indexes, triggers and `CHECK` constraints aren't
  described, and each run covers one schema. A foreign key into another schema is left out.
- **SQLite virtual tables are skipped** with a note, because inspecting one (FTS5, rtree)
  runs its module's queries. The shadow tables behind them do get listed.
- **Types are what the database reports.** MySQL's `TINYINT(1)` shows as an integer, not a
  boolean. A UUID stored as `CHAR(32)` shows as a string.
- **SQLite types are declarations.** SQLite only enforces them in `STRICT` tables, so a
  column can hold other types. Date columns show as `unknown` plus their declared type,
  because the catalog can't tell text dates from epoch numbers. A declared type
  SQLAlchemy doesn't recognise, such as `ANY`, shows as `NUMERIC`.
- **Connections time out after 10 seconds** so an unreachable server can't stall a session.

</details>

## Python API

```python
import datanyx

print(datanyx.schema_of("orders.csv", "typescript"))
print(datanyx.anonymize_text("Jessica Whitfield called.", hide=["Acme"]))
```

For more control, work with the schema as objects:

```python
from datanyx import KeyPolicy, extract, render

policy = KeyPolicy(map_paths=frozenset({"$.users"}), keep_names=frozenset({"report_2024"}))

schema = extract("customers.json", policy=policy)
print(render(schema, "text"))

schema = extract("env:DATABASE_URL", namespace="sales")  # namespace is --schema
print(render(schema))
```

```python
from datanyx.text import PatternDetector, SpacyDetector, anonymize, anonymize_with_mapping, restore

detectors = [PatternDetector(), SpacyDetector("en_core_web_lg")]
print(anonymize("Jessica Whitfield called.", detectors, hide_terms=["Acme"]))

clean, mapping = anonymize_with_mapping("Jessica Whitfield called.", detectors)
print(restore(clean, mapping))
```

Failures raise `SourceError`, with a message that's safe to show.

## Contributing

### Architecture

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
| `text/custom.py` | Detectors described in a file: regular expressions and dictionaries of terms |
| `text/secrets.py` | Finds credentials by their shape: API keys, tokens, private keys, passwords |
| `text/anonymizer.py` | Merges spans, finds repeat mentions and assigns placeholders. `Anonymizer` keeps them across many texts |
| `cli.py` | Command line, which reports errors without quoting data |

### Adding a source

Implement `Source` and add it to the registry in `sources/__init__.py`. Documents, API
responses and CSV rows can reuse `Observation` from `inference.py`, and sources that
know their types up front (like Parquet) can use `build_object` from `sources/catalog.py`.

A SQL database with a SQLAlchemy dialect needs no new source. Add its short URL, driver
and connection timeout argument to `sql_source.py`, add the extra to `pyproject.toml`,
and add a server to the live tests. Anything else with a catalog should read it directly
and build `Object` nodes, with one `Entity` per table, so it never has to query any rows.

### Development

The project uses [uv](https://docs.astral.sh/uv/). `uv sync` creates `.venv` from
`uv.lock`, so everyone gets the same versions:

```sh
uv sync --all-extras                 # every reader and database driver, plus the dev tools
uv sync --all-extras --group models  # also the large NER models (about 500 MB)
uv run datanyx doctor                # shows what's installed
```

### Tests

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

See the [changelog](CHANGELOG.md) for what's new.

## License

[MIT](LICENSE) © 2026 RunningToOblivion
