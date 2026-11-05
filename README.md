# DataNyx

Gives an AI agent the structure of your data without showing it the data. Point
DataNyx at a file or database and it outputs a schema the agent can write code
against. The schema has no values, examples, enum labels, counts or statistics.

## Supported sources

| Source | Input | Install |
|---|---|---|
| JSON, JSON Lines | `.json`, `.jsonl`, `.ndjson`, or `-` for stdin | `pip install -e .` |
| SQLite | `.db`, `.sqlite`, `.sqlite3`, or `sqlite:///path` | `pip install -e .` |
| PostgreSQL | `postgresql://` | `pip install -e ".[postgres]"` |
| MySQL | `mysql://` | `pip install -e ".[mysql]"` |
| MariaDB | `mariadb://` | `pip install -e ".[mysql]"` |
| SQL Server | `mssql://` | `pip install -e ".[mssql]"` |
| Oracle | `oracle://` | `pip install -e ".[oracle]"` |

Requires Python 3.10+. Use `.[all]` to install every driver. Other SQLAlchemy URLs,
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
```

| Option | Purpose |
|---|---|
| `--format` | `json-schema` (default) or `text` |
| `-o`, `--output` | Write to a file instead of stdout |
| `--schema` | Database schema to describe, instead of the database's default |
| `--source` | Set the input type (`json`, `jsonl`, `sql`) when it can't be detected |
| `--map-path` | Hide all keys or columns at a path ([details](#adjusting-what-gets-hidden)) |
| `--keep-name` | Show a name that was hidden automatically ([details](#adjusting-what-gets-hidden)) |

Prefer `env:NAME` over putting a database URL on the command line. A password in the
command is visible to an agent running it and is kept in shell history.

### Output

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

## Using it with an agent

1. **Run DataNyx yourself**, and read the schema before sharing it.
2. **Give the agent only the schema**, and block its access to the data file or
   database in its permissions. DataNyx produces a schema but doesn't restrict the
   agent: if it can open the file, it can still see the data.
3. **Run the agent's code yourself** against the real data. Code that prints rows or
   logs values would expose them if the agent sees that output.

If the agent runs DataNyx itself, its output is still safe to show, as long as you
accept that `--keep-name` lets it [test guesses](#adjusting-what-gets-hidden).

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
- **Large JSON documents** are loaded fully into memory. JSON Lines is read line by line.

## Python API

```python
from datanyx import KeyPolicy, extract, render

policy = KeyPolicy(map_paths=frozenset({"$.users"}), keep_names=frozenset({"report_2024"}))

schema = extract("customers.json", policy=policy)
print(render(schema, "text"))

schema = extract("env:DATABASE_URL", namespace="sales")  # namespace is --schema
print(render(schema))
```

Failures raise `SourceError`, with a message that's safe to show.

## Architecture

```
Source         --->  DataSchema (schema.py)  --->  Renderer
JSON, JSONL,         no values by design           JSON Schema, text
SQL databases
```

| Module | Purpose |
|---|---|
| `schema.py` | Source-neutral model: `DataSchema` > `Entity` > `Node` |
| `sources/` | `Source` interface, registry and input readers (`sql_source.py` covers every SQL database) |
| `inference.py` | Builds nodes from sampled values, for sources without a catalog |
| `privacy.py` | Decides which names are structure and which are data |
| `formats.py` | Recognises string formats, returning only a label |
| `renderers/` | Output formats |
| `cli.py` | Command line, which reports errors without quoting data |

### Adding a source

Implement `Source` and add it to the registry in `sources/__init__.py`. Documents, API
responses and CSV rows can reuse `Observation` from `inference.py`.

A SQL database with a SQLAlchemy dialect needs no new source. Add its short URL, driver
and connection timeout argument to `sql_source.py`, add the extra to `pyproject.toml`,
and add a server to the live tests. Anything else with a catalog should read it directly
and build `Object` nodes, with one `Entity` per table, so it never has to query any rows.

## Tests

```sh
pip install -e ".[dev]"
pytest
```

The default run needs no services, and the live server tests are skipped. To run those
against real databases, using Docker:

```sh
docker compose -f tests/docker-compose.yml up -d --wait
DATANYX_TEST_SERVERS=1 pytest -m server
docker compose -f tests/docker-compose.yml down
```

In PowerShell, set the variable with `$env:DATANYX_TEST_SERVERS = 1` first.

The images total about 7.4 GB. On a first run, pulling them all at once can overwhelm
Docker Desktop, so start services one at a time if it hangs, for example
`docker compose -f tests/docker-compose.yml up -d --wait postgres`. The live tests use
PostgreSQL 17, MySQL 8.4, MariaDB 11.4, SQL Server 2022 and Oracle Free 23.

| Test file | Covers |
|---|---|
| `test_privacy.py` | Plants a canary string throughout synthetic JSON and checks it never reaches any output, including errors |
| `test_schema.py` | JSON schemas are accurate enough to code against |
| `test_sqlite.py` | SQLite structure, read-only access and refused row reads |
| `test_sql_targets.py` | URLs, `env:NAME`, and failures that never show the password |
| `test_sql_servers.py` | All of the above against real servers, plus logins without row access |
| `test_keep_name.py` | `--keep-name` reveals only the names asked for |

Add a canary check for each new source.
