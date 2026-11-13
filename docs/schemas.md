# Schemas

```sh
datanyx schema customers.json                   # JSON Schema (default)
datanyx schema customers.json --format text     # compact, fewer tokens for the agent
datanyx schema events.jsonl -o schema.json      # write to a file
datanyx schema shop.db --format text            # SQLite
datanyx schema env:DATABASE_URL                 # server database, URL read from the environment
datanyx schema env:DATABASE_URL --schema sales  # a schema other than the default
cat customers.json | datanyx schema -           # read stdin
datanyx schema orders.csv                       # every format in the sources table works the same way
datanyx schema ./exports/ -r                    # every readable file in a folder, subfolders too
datanyx schema orders.csv --format typescript   # or pydantic, sql, summary
```

| Option | Purpose |
|---|---|
| `--format` | `json-schema` (default), `text`, `typescript`, `pydantic`, `sql` or `summary` ([output formats](#output-formats)) |
| `-r`, `--recursive` | For a folder, also read its subfolders |
| `-o`, `--output` | Write to a file instead of stdout |
| `--schema` | Database schema to describe, instead of the database's default |
| `--source` | Set the input type (`csv`, `json`, `parquet`, `fhir`, ...) when the name doesn't say; the [command reference](cli-reference.md#datanyx-schema) lists them all |
| `--delimiter` | The separator of a CSV or TSV file, such as `;`, `\|` or `tab`, when it isn't a comma (CSV) or a tab (TSV) |
| `--map-path`, `--keep-name` | Hide more, or show a name that was hidden automatically ([details](#adjusting-what-gets-hidden)) |
| `--scale-hints`, `--infer-keys` | Add sizes and guessed keys and joins ([details](#size-and-key-hints)) |
| `--column-hints` | Add ranges for each column's distinct values, missing values and text length ([details](#column-hints)) |
| `--public-column` | List the labels of a column you declare safe to show ([details](#public-columns)) |
| `--views` | Describe database views too ([details](#views)) |
| `--max-rows` | Read only the first N rows of a big file ([details](#huge-files)) |
| `--include`, `--exclude`, `--max-tables` | Choose which tables to describe ([details](#big-databases)) |

Settings that a project always wants go under `[schema]` in [`datanyx.toml`](settings.md); `synth`, `check`, `diff`,
`run --describe-output` and the MCP server read them too.

> **Tip:** prefer `env:NAME` over putting a database URL on the command line. A password in
> the command is visible to an agent running it and is kept in shell history.

## What the output looks like

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

## Output formats

| `--format` | Produces |
|---|---|
| `json-schema` | JSON Schema, draft 2020-12 |
| `text` | A compact TypeScript-like form that costs an agent few tokens |
| `typescript` | `export interface` and `export type` declarations |
| `pydantic` | Pydantic v2 models, as Python source |
| `sql` | Portable `CREATE TABLE` statements |
| `summary` | One line per table: its column count, keys and size. A first look at a database too big to read in full |

None carry values, and keys that were hidden stay hidden: they become `Record<string, T>`,
`extra="allow"` or a note. Nested data becomes `JSON` columns in SQL, and a database's own
column types are kept as they are. Names that aren't valid identifiers get a Pydantic
`alias`, so the model still reads and writes the real keys.

**How each format is read**

| Format | How it's described |
|---|---|
| CSV, TSV | The first row is the header, unless it looks like data: then it counts as a record and the columns are `column_N` ([how it decides](privacy-model.md#known-limits)). Cell text is typed by what it plainly is, but `007` stays a string. A short row makes its missing columns optional. |
| Excel | One entry per sheet, first row as the header unless it looks like data, cached formula results. A sheet name that looks like data becomes `sheet_N`. |
| Parquet | From the file footer alone: no row is read, and the file's metadata is ignored. Columns, structs, lists and maps are typed from the Arrow schema. |
| YAML | Read with the safe loader, so no tag can run code. Several documents are described as an array. |
| XML | Attributes are keys starting with `-` and text is `$text`. An element that repeats becomes an array. Files with a DOCTYPE or entity declarations are refused. |
| Folder | One entry per file, named after it (a sheet or table inside it becomes `file#sheet`). A file name that looks like data becomes `file_N`. Fields shared between files are listed in the notes, since they're likely join keys. Links and dotfiles are skipped, a file that can't be read is named in a note, and it stops after 200 readable files. |

Files with unusual extensions can be read with `--source csv`, `--source tsv`, `--source xml`
and so on. [Supported sources](sources.md) has the notes for every other format.

## Adjusting what gets hidden

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

## Size and key hints

A schema from a file says nothing about how big the data is or how its tables relate. Two options add that,
and both are **off by default** because each says a little about the data:

```sh
datanyx schema ./exports --scale-hints --infer-keys --format text
```

```
customers.csv: array<{
  id: integer  -- unique (inferred)
  email: string<email>
}>
  scale: 10^4 to 10^5 rows

orders.csv: array<{
  order_id: integer  -- unique (inferred)
  customer_id: integer
}>
  foreign key (customer_id) -> customers.csv (id)  -- inferred from the values
  scale: 10^5 to 10^6 rows
```

**`--scale-hints`** gives each table's size as an order of magnitude, so an agent can choose between loading
a table and streaming it. It is never a number: everything under a thousand rows is "fewer than 1,000 rows",
and above that only the power of ten is given. CSV, TSV, JSON, JSON Lines, YAML and Excel report it from
the rows they read. Parquet, ORC and Arrow files take it from the file footer or metadata, Delta Lake from
the log's file statistics, Iceberg from the current snapshot's summary, DuckDB from its catalog estimate, MongoDB
from the collection's metadata, and PostgreSQL, MySQL, MariaDB, SQL Server and Oracle from the server's own
statistics (a table that PostgreSQL, MySQL, MariaDB or Oracle has never analysed has none). Other sources say that no hint is available.

**`--infer-keys`** marks key-looking columns that are unique, and guesses joins between tables (two CSVs
in a folder, the sheets of a workbook, a JSON array and a JSON Lines file). It reads CSV, TSV, JSON Lines,
JSON arrays and Excel; databases already declare their keys. What comes out is a yes or no, marked as a
guess, never a count or a value. To keep it from saying more than it should:

- only columns *named* like keys (`id`, `user_id`, `uuid`, `code`, ...) are examined;
- two such columns that are unique only *together* (an order and its line number) are reported as a composite key, and a
  table that holds the same two columns, with every pair of values in that key, is a composite join. The two names must
  be the same in both tables;
- a table needs at least 20 rows, and the column no nulls and no repeats, to be called unique;
- a join needs the names to match (`orders.customer_id` with `customers.id`, or the same specific name in both
  tables) and every value to be contained, because small integer ids sit inside each other all the time;
- two tables that each hold the other's key under the same name are a tie, so neither is called the parent.

The hashes of the values are held in memory while the file is read and thrown away; a column with more than
200,000 distinct values is given up on. The inferred joins appear in `x-foreign-keys` with `"inferred": true`,
and as comments rather than constraints in the SQL output, so nothing pretends the database enforces them.
`datanyx synth` uses them, so fake rows keep the joins and the unique columns.

Set both once for a project with `scale_hints = true` and `infer_keys = true` under `[schema]` in
`datanyx.toml`. For the MCP server they are options of whoever starts it (`datanyx mcp --scale-hints
--infer-keys`, or the same settings file), and an agent can't turn them on or off.

## Keys the database declares

For SQL databases, a column the database itself keeps unique (a `UNIQUE` constraint or a unique index) is shown as
`unique`, with no "inferred" next to it, because it is a fact and not a guess. A set of columns that are unique
together appears as `unique (a, b)`, and in the SQL output as a real `UNIQUE` constraint. The primary key is
listed as before. A unique key that involves a hidden column is left out, as a partial key would mislead, and so is a
partial index (PostgreSQL's `WHERE`), which only makes some rows unique. `datanyx synth` keeps declared unique columns
unique.

## Views

`--views` also describes a database's views (and PostgreSQL's materialized views): their names and columns, marked
`view`, and in JSON Schema `x-view`. A view's definition is never read, and it has no keys of its own. They are off by
default so existing output doesn't change; set `views = true` under `[schema]` to turn them on for a project.

## Column hints

`--column-hints` says a little about the *shape* of each column, as ranges and never as numbers or values. It is
off by default, because each hint says something about the data:

```sh
datanyx schema ./exports/customers.csv --column-hints --format text
```

```
root: array<{
  id: integer  -- 100 to 999 distinct values, never missing
  status: string  -- 2 to 9 distinct values, never missing, text up to 9 characters
  nickname: string | null  -- 100 to 999 distinct values, often missing, text up to 9 characters
}>
```

| Hint | Values it can take |
|---|---|
| How many different values | `one value only`, `2 to 9`, `10 to 99`, `100 to 999`, `1,000 or more distinct values` |
| How often it is missing (empty or null) | `never`, `rarely` (under a tenth), `often` (up to half), `mostly missing` |
| How long the longest text is | `text up to 9`, `99` or `999 characters`, or `text of 1,000 characters or more` |

A column with 3 values is a category an agent can group by; one with 1,000 or more is not. A column that is mostly
missing is a poor join key. These are the questions the hints are for. Things to know:

- A table needs at least 20 rows, and a table that was cut short by `--max-rows` gets none. Columns with a hidden
  name get none either.
- It reads CSV, TSV, JSON Lines, JSON arrays and Excel, as `--infer-keys` does. Databases and the formats that carry
  their own schema say that no hint is available.
- Counting distinct values keeps a hash of each value in memory while the file is read, up to 1,000 per column, and throws
  them away.
- Set `column_hints = true` under `[schema]` to make it the project's default. For the MCP server it is an option of
  whoever starts it (`datanyx mcp --column-hints`), and an agent can't turn it on or off.

## Public columns

A schema can say a column is a string, but not that `country` is spelled `US`, `USA` and `United States`. For columns like that
the labels *are* the information, and the person who owns the data knows which ones are safe to show. `--public-column`
lets them say so:

```sh
datanyx schema orders.csv --public-column country --public-column status --format text
```

```
country: string  -- values: "CA" | "Canada" | "DE" | "FR" | "France" | "US" | "USA" | "United States" | "us"
status: string  -- values: "cancelled" | "completed" | "refunded"
```

`--public-column customers.country` limits it to one table: `customers`, `customers.csv`, a sheet's name, or the file you passed.
A plain name applies to every table that has a column of that name, so qualify it when the name is common (`status`, `name`) and only
one table's should be shown. A name that matches nothing is reported in a note.

This is the one option that shows values, so it works only for the columns you name, and it has safety nets:

| Rule | Why |
|---|---|
| Only the columns you name: `country` (in every table that has one) or `customers.country` (only in that table) | Nothing else is ever listed |
| A label must be seen at least 3 times; rarer values are left out, and a note says some were | A one-off value is the one most likely to identify someone |
| At most 25 labels, else the column isn't listed | A real category is short; a long list is data |
| A value must look like a plain label: up to 40 characters of letters, digits, spaces and `. , & + ( ) / ' -`, and not shaped like an email, ID, phone number, long number or token | Catches a column declared by mistake, and keeps a value from breaking a comment in the output |
| Labels are sorted and never counted | The order and counts would say more than the labels |
| A file read in part (`--max-rows`), or a source that can't be read row by row, lists nothing, and says so | A partial list would look complete |

What it can't do: tell that a short list of *names* is personal. Declare categories, not people. The agent can't turn this on:
`datanyx protect` refuses `--public-column`, and for the MCP server it is an option of whoever starts it
(`datanyx mcp --public-column country`). Fake rows from `datanyx synth` use the listed labels for those columns, so code
meets the real spellings (`us` as well as `US`) before it meets the real data.

In the [end-to-end demo](https://github.com/lud3nb3rg/DataNyx/blob/main/examples/demo/README.md#public-columns-the-data-owner-lists-the-labels-of-a-few-columns),
listing the labels of three columns took agents from 13 to 15 of 15 right on a deliberately messy dataset, and from 8 to 13 of 15 when
they weren't told the data was messy. It fixed every failure that came from not knowing the spellings, and none of the ones that
didn't (duplicate rows, text amounts).

Set `public_columns = ["country", "status"]` under `[schema]` to make it a project's default. It reads CSV, TSV, JSON Lines,
JSON and Excel, like `--column-hints`, and not databases, whose rows the tool never reads.

## Huge files

CSV, TSV, JSON Lines and Excel files are read one row at a time, so memory stays small however big the file is.
Time still grows with it. `--max-rows N` reads only the first N rows (of each sheet, for Excel), and a large
JSON array is cut the same way when it is streamed (`.[streaming]`):

```sh
datanyx schema events.csv --max-rows 100000
```

A file that was cut short says so in a note, and then **gets no size hint and no key guesses**, because neither
can be known from a part. Fields that are rare, or that only appear later, may be missing, and a field that looks
required in the first rows may not be. A file with exactly N rows, or fewer, is read whole and nothing is
different. Set `max_rows` under `[schema]` in `datanyx.toml` to make it the project's default. YAML and XML
are read whole, and so is a JSON file below the streaming size.

## Big databases

A database with thousands of tables would flood an agent. These options keep the answer a useful size:

```sh
datanyx schema $DATABASE_URL --format summary                  # one line per table
datanyx schema $DATABASE_URL --include 'sales_*' --exclude '*_old'
datanyx schema $DATABASE_URL --max-tables 50                   # and a note says how many were left out
```

`--include` and `--exclude` take glob patterns on table names (on file names for a folder), and can be repeated.
`--max-tables` stops at N tables and the note says how many more there are. Foreign keys to a table outside the
selection are left out. These work on names that may be shown only, so a pattern can't be used to find out whether a
hidden name exists, and the counts in the notes never include one.
