# DataNyx

Lets an AI agent work with your data without seeing what's sensitive in it.

- **Structured data:** `datanyx schema` turns a file or database into a schema the agent
  can write code against, with no values, examples, enum labels, counts or statistics.
- **Free text:** `datanyx anonymize` replaces names, emails and other identifiers with
  placeholders like `<PERSON_1>`. This is best effort and **can miss things**, so unlike
  a schema, [the result needs reviewing](#how-reliable-text-anonymization-is).

## Supported sources

| Source | Input | Install |
|---|---|---|
| Plain text (`anonymize`) | Any UTF-8 file, or `-` for stdin | `pip install -e ".[text]"`, plus a [model](#text-anonymization) |
| JSON, JSON Lines | `.json`, `.jsonl`, `.ndjson`, or `-` for stdin | `pip install -e .` |
| CSV, TSV | `.csv`, `.tsv`, `.tab` | `pip install -e .` |
| XML | `.xml` | `pip install -e .` |
| YAML | `.yaml`, `.yml` | `pip install -e ".[yaml]"` |
| Excel | `.xlsx`, `.xlsm` | `pip install -e ".[excel]"` |
| Parquet | `.parquet`, `.pq` | `pip install -e ".[parquet]"` |
| A folder | Every readable file in it, with `-r` for subfolders | as for the files in it |
| SQLite | `.db`, `.sqlite`, `.sqlite3`, or `sqlite:///path` | `pip install -e .` |
| PostgreSQL | `postgresql://` | `pip install -e ".[postgres]"` |
| MySQL | `mysql://` | `pip install -e ".[mysql]"` |
| MariaDB | `mariadb://` | `pip install -e ".[mysql]"` |
| SQL Server | `mssql://` | `pip install -e ".[mssql]"` |
| Oracle | `oracle://` | `pip install -e ".[oracle]"` |

Requires Python 3.10+. Use `.[all]` to install every driver and reader. Other SQLAlchemy URLs,
such as `snowflake://`, should work if you install their dialect, but only the
databases above are tested.

## Usage

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
| `--source` | Set the input type (`json`, `jsonl`, `sql`) when it can't be detected |
| `--map-path` | Hide all keys or columns at a path ([details](#adjusting-what-gets-hidden)) |
| `--keep-name` | Show a name that was hidden automatically ([details](#adjusting-what-gets-hidden)) |

Prefer `env:NAME` over putting a database URL on the command line. A password in the
command is visible to an agent running it and is kept in shell history.

### Schema output

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
`x-primary-key` and `x-foreign-keys` carrying the database details.

Notes about what was hidden go to stderr, so they stay out of the schema.

### Output formats

`--format typescript` writes declarations, `--format pydantic` writes Pydantic v2 models
(Python source), and `--format sql` writes portable `CREATE TABLE` statements. All three
carry no values, and keys that were hidden stay hidden: they become `Record<string, T>`,
`extra="allow"` or a note. Nested data becomes `JSON` columns in SQL, and a database's own
column types are kept as they are. Names that aren't valid identifiers get a Pydantic
`alias`, so the model still reads and writes the real keys.

### How each format is read

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

## Text anonymization

```sh
pip install -e ".[text]"
python -m spacy download en_core_web_lg     # the default model, about 400 MB
datanyx doctor                              # shows which optional packages and models are installed

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

The same person keeps the same placeholder, so the agent can still follow who did what.
A name found once is hidden everywhere it appears, including parts of it like the
surname above. Two people sharing a surname aren't linked by it. Spacing and line endings
are preserved.

| Option | Purpose |
|---|---|
| `-o`, `--output` | Write to a file instead of stdout. It refuses to overwrite the input |
| `--model` | spaCy model, such as `fr_core_news_md` for French text |
| `--hide` | Always hide this term, ignoring case (repeatable) |
| `--keep` | Leave this exact term visible (repeatable) |
| `--keep-entity` | Leave a kind of entity visible, such as `DATE` (repeatable) |
| `--mapping` | Also save each placeholder's real value to a new file ([details](#restoring-real-values)) |

**What it hides:**

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

### Restoring real values

If an agent writes something using placeholders, you can put the real values back. This
is opt-in, and no mapping is saved unless you ask for one:

```sh
datanyx anonymize notes.txt -o clean.txt --mapping notes.mapping.json
# ...the agent works from clean.txt and writes summary.txt...
datanyx restore summary.txt --mapping notes.mapping.json -o summary.real.txt
```

**The mapping file holds the data you're hiding, in plain text.** Treat it like the
original: keep it where the agent can't read it, and delete it when you're finished. Don't
run `restore` through an agent either. It requires `-o`, so real values don't appear in
the command's output, but an agent could still open the result.

- `*.mapping.json` is ignored by git here, so use that ending.
- An existing mapping is never replaced, since placeholders only mean something for the run
  that made them. Use a new file for each run.
- A placeholder covers every form of a name and restores to the longest, so `Ms. <PERSON_1>`
  becomes `Ms. Jessica Whitfield` even if the original said `Ms. Whitfield`.
- Placeholders not in the mapping, such as ones an agent invented, are left as they are and
  listed in a note. The mapping file is made readable by its owner only, on Linux, macOS and Windows.

### How reliable text anonymization is

**Treat the output as a draft to review, not a guarantee.** A schema contains no data by
construction, but anonymized text is your original text minus whatever got detected.

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

## Using it with an agent

1. **Run DataNyx yourself**, and read the schema before sharing it.
2. **Give the agent only the schema**, and block its access to the data file or
   database in its permissions. DataNyx produces a schema but doesn't restrict the
   agent: if it can open the file, it can still see the data.
3. **Run the agent's code yourself** against the real data. Code that prints rows or
   logs values would expose them if the agent sees that output.

If the agent runs DataNyx itself, its output is still safe to show, as long as you
accept that `--keep-name` lets it [test guesses](#adjusting-what-gets-hidden).

### As an MCP server

```sh
pip install -e ".[mcp]"
datanyx mcp --root ./exports        # stdio; add to your agent's MCP configuration
```

The agent gets two tools: `describe_data` (a schema for a file, folder or `env:NAME`
database) and `anonymize_file` (a text file with placeholders). It's limited by design:

- It can only read under `--root` (default: the current folder), and database URLs are
  refused unless you start the server with `--allow-urls`. Use `env:NAME` instead.
- There's no `keep` option, because keeping a name visible tests whether it exists. Choose
  `--keep-entity` when you start the server instead.
- There's no restore tool and no mapping, since both hold the real values.
- Errors are fixed messages, never the library's own text.

The server doesn't stop the agent from opening the files itself. Block that in its
permissions, as above.

## What protects the data

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

## Known limits

- **Field names are shown.** They're what make the schema useful, so a sensitive name
  that looks ordinary will get through. **Read the schema before sharing it.**
- **Text anonymization can miss things**, as [measured above](#how-reliable-text-anonymization-is).
- **Structure can be sensitive in itself.** A field called `hiv_test_result` says
  something even without a value.
- **JSON schemas reflect the sample.** `required` and formats describe the data seen,
  and hidden keys can include real optional fields.
- **Database coverage is tables only.** Views, indexes, triggers and `CHECK` constraints
  aren't described, and each run covers one schema. A foreign key into another schema is
  left out.
- **SQLite virtual tables are skipped** with a note, because inspecting one (FTS5, rtree)
  runs its module's queries. The shadow tables behind them do get listed.
- **Types are what the database reports.** MySQL's `TINYINT(1)` shows as an integer, not a
  boolean. A UUID stored as `CHAR(32)` shows as a string.
- **SQLite types are declarations.** SQLite only enforces them in `STRICT` tables, so a
  column can hold other types. Date columns show as `unknown` plus their declared type,
  because the catalog can't tell text dates from epoch numbers. A declared type
  SQLAlchemy doesn't recognise, such as `ANY`, shows as `NUMERIC`.
- **Connections time out after 10 seconds** so an unreachable server can't stall a session.
- **Large JSON documents** over 64 MB are streamed if `ijson` is installed (`pip install -e ".[streaming]"`); otherwise they're loaded fully into memory. JSON Lines and CSV are read row by row. Error messages for streamed JSON have no line number, since the parser's own text can quote the data.
- **CSV has no types.** Types come from the text, so a column of numbers with one `n/a` becomes a string, and a header row is assumed.
- **XML and YAML are loaded fully into memory.**

## Python API

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

Two shortcuts return text directly:

```python
import datanyx

print(datanyx.schema_of("orders.csv", "typescript"))
print(datanyx.anonymize_text("Jessica Whitfield called.", hide=["Acme"]))
```

Failures raise `SourceError`, with a message that's safe to show.

## Architecture

```
Source         --->  DataSchema (schema.py)  --->  Renderer
JSON, JSONL, CSV,    no values by design           JSON Schema, text,
YAML, XML, Excel,                                  TypeScript, Pydantic, SQL
Parquet, SQL, folders

Text  --->  Detectors (patterns, NER)  --->  Placeholders
```

| Module | Purpose |
|---|---|
| `schema.py` | Source-neutral model: `DataSchema` > `Entity` > `Node` |
| `sources/` | `Source` interface, registry and input readers (`sql_source.py` covers every SQL database) |
| `inference.py` | Builds nodes from sampled values, for sources without a catalog |
| `privacy.py` | Decides which names are structure and which are data |
| `formats.py` | Recognises string formats, returning only a label |
| `sources/directory.py` | Describes a folder: one entry per file, with file names held to the same rules |
| `renderers/` | Output formats |
| `mcp_server.py` | MCP server that only exposes schemas and anonymized text |
| `text/detectors.py` | Finds sensitive spans: patterns, and spaCy NER through `Detector` |
| `text/anonymizer.py` | Merges spans, finds repeat mentions and assigns placeholders |
| `cli.py` | Command line, which reports errors without quoting data |

### Adding a source

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
uv sync --all-extras                # every reader and database driver, plus the dev tools
uv sync --all-extras --group models # also the large NER models (about 500 MB)
uv run datanyx doctor               # shows what's installed
```

## Tests

```sh
uv run pytest                       # needs no services; model tests skip without the models
uv run ruff check src tests
uv run mypy
uv run pytest --cov                 # coverage report
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

## License

[MIT](LICENSE)
