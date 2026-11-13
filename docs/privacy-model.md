# Privacy model

What DataNyx guarantees, what is only best effort, and the limits you should know about.

| Output | Guarantee |
|---|---|
| A schema (`schema`, `describe_data`, `check`, `diff`, `run --describe-output`) | No values, by construction. The one exception is a column the data owner declares public |
| Synthetic rows (`synth`, `synthetic_sample`) | Generated from the schema alone, so nothing real is copied |
| Anonymized or scrubbed text (`anonymize`, `scrub`, `run`, `anonymize_file`) | Best effort: the original text minus whatever was detected. Review it |
| A pseudonymized copy (`pseudonymize`) | Names, ids and the like replaced; everything kept is real. Not anonymous |
| A scan report (`scan`, `scan_folder`) | Kinds, counts and line numbers, never a value |

## What protects the data

**Values can't reach a schema.** Each value is reduced to a type and a format label
(`email`, `uuid`, `date`, `date-time`, `uri`, `ipv4`, `ipv6`) and then discarded. The
schema model has one field that can hold values, `Field.values`, and it stays empty unless the owner of the data names
a column with `--public-column` ([public columns](schemas.md#public-columns)). Nothing the agent sends can fill it.
A test plants a canary in every value of every supported file format, and of MongoDB, and fails if it reaches any output; the SQL servers have tests of their own
([how each source is tested](sources.md#how-each-source-is-tested)).

**Keys are treated as possible data.** A JSON key is hidden, and its value's structure
described under `[key: ...]` / `additionalProperties`, when any of these apply:

| Signal | Example |
|---|---|
| Its shape looks like data | emails, UUIDs, dates, URLs, phone numbers, IPs, numbers, tokens (an unbroken run of 20 or more letters and digits, or 16 or more hex digits), keys containing spaces such as `Order Details` |
| It sits among mostly data-like keys and holds the same kind of value | `bob` beside `al@example.com` |
| It rarely recurs across many objects (5 or more seen) | a key found in only 1 of 1,000 records |
| The object has many same-shaped entries | 8+ same-shaped objects, or 32+ values of one scalar type |

**Database names get the same shape check.** A hidden table is left out completely. A
hidden column is described under `[key: ...]`. A primary or foreign key that would name
something hidden is dropped. File names in a folder are judged the same way, and a hidden one becomes `file_N`.

**Database rows are never queried.** The schema comes from the catalog alone, and the
tests record every statement to check this. What enforces it depends on the database:

- **SQLite** files open read-only, with an authorizer that makes SQLite itself refuse
  to read a user table or change anything.
- **DuckDB** files open read-only with external access switched off.
- **Server databases** have no client-side equivalent, so use
  [a login that can't read rows](#a-login-that-cant-read-rows). Then the server
  enforces it.
- **MongoDB** has no catalog, so it is the exception: it samples documents, keeps their types and discards them
  ([details](sources.md#notes-on-the-newer-sources)).

SQLAlchemy's inspector does fetch column defaults, comments and `CREATE` statements,
which can hold literals such as `DEFAULT 'a@example.com'`. They stay in memory inside
the tool and are never copied into the schema. Enum labels are stripped from native
types, so `ENUM('a','b')` shows as `ENUM`. A view's definition is never read.

**Every output channel is covered.** An agent may run the tool itself, so stderr counts
too.

- Parse errors give only a position.
- Database and unexpected errors give only an error type, because their messages can
  quote statements and connection details.
- SQLAlchemy's warnings are silenced during extraction, because they can name hidden
  columns.
- Notes never contain hidden names, and the input filename and database URL aren't
  included in the schema.

## A login that can't read rows

These grants were tested against real servers. In each case the login can get the
schema, and its attempt to `SELECT` from a table is refused.

| Database | Setup |
|---|---|
| PostgreSQL | `CREATE ROLE reader LOGIN PASSWORD '...'` with no further grants |
| MySQL, MariaDB | `CREATE USER`, then `GRANT REFERENCES ON mydb.* TO reader` |
| SQL Server | `CREATE LOGIN` and `CREATE USER`, then `GRANT VIEW DEFINITION TO reader` |
| Oracle | `CREATE USER`, `GRANT CREATE SESSION`, then `GRANT REFERENCES ON each_table TO reader`, and run with `--schema owner` |

On Oracle, a table without the grant doesn't appear in the schema.

## Files at a URL

`datanyx schema` and the other commands that describe data can read a file from `https://` and cloud storage
(see [Files at a URL](sources.md#files-at-a-url)). The URL is the only thing that leaves your machine, and nothing
is sent but the request for the file. Messages never quote it, because it may carry a password or a signed token.
`file://` URLs are not accepted, so a URL can't be used to get around the folders an MCP server is limited to.

When `datanyx mcp` runs with `--allow-urls`, the *server* makes the request the agent asks for, from wherever it runs.
An agent could point it at an internal address (a metadata service, an admin page) and learn from the error
type whether something answered. Run the server with `--allow-urls` only where that is acceptable, or keep
the URLs you want it to use in environment variables and allow only those with `--allow-env`.

## Known limits

- **A public column shows its labels.** That is the point of `--public-column`, and it is the owner's decision: declare
  categories (a country, a status, a plan), never a column that could hold names or identifiers. The safety nets
  (at most 25 labels, each seen at least 3 times, nothing shaped like an email, ID, phone number or long number, and no
  counts) catch a column declared by mistake, such as one of emails. They can't tell that a short list of people's names, or one
  rare diagnosis seen three times, is personal. The agent can't switch it on: `datanyx protect` refuses `--public-column`,
  and an MCP server's public columns are chosen by whoever starts it.
- **Field names are shown.** They're what make the schema useful, so a sensitive name
  that looks ordinary will get through. **Read the schema before sharing it.**
- **Text anonymization can miss things**, as [measured](anonymization.md#how-reliable-text-anonymization-is).
- **Structure can be sensitive in itself.** A field called `hiv_test_result` says
  something even without a value.
- **The opt-in hints say a little.** `--scale-hints`, `--infer-keys` and `--column-hints` give orders of magnitude,
  yes/no guesses and ranges, never a count or a value, but each says something about the data. They are off unless
  the owner turns them on.
- **JSON schemas reflect the sample.** `required` and formats describe the data seen,
  and hidden keys can include real optional fields.
- **CSV has no types.** Types come from the text, so a column of numbers with one `n/a`
  becomes `string | number`.
- **A first row is judged by its look.** A first row of a CSV or Excel sheet is read as data, with columns
  called `column_N`, unless it looks like a header: lowercase, `snake_case` or `camelCase` names, or
  mostly words that head columns. So `Species,Habitat` is taken for data until you confirm it with
  `--keep-name`, and a headerless export whose first row is all lowercase (`jane,doe`) is taken for a header.
  **Read the schema before sharing it.**
- **File names are judged by their look too.** `jane_doe_medical.csv` becomes `file_N`, but `jane_doe.csv`
  looks like any other name, so point the tool at folders whose file names you are willing to show.
- **Large files.** JSON over 64 MB is streamed if `ijson` is installed (`.[streaming]`),
  and otherwise loaded fully into memory. JSON Lines, CSV, TSV and Excel are read row by row. XML and
  YAML are loaded fully into memory. Error messages for streamed JSON have no line number,
  since the parser's own text can quote the data.

### Database limits

- **Coverage is tables, and views with `--views`.** Indexes, triggers and `CHECK` constraints aren't
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
