# Security model

**What protects the data**

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

# Known limits

- **Field names are shown.** They're what make the schema useful, so a sensitive name
  that looks ordinary will get through. **Read the schema before sharing it.**
- **Text anonymization can miss things**, as [measured above](anonymization.md#how-reliable-text-anonymization-is).
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

**Database limits**

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


