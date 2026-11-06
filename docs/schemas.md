# Schemas

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

None carry values, and keys that were hidden stay hidden: they become `Record<string, T>`,
`extra="allow"` or a note. Nested data becomes `JSON` columns in SQL, and a database's own
column types are kept as they are. Names that aren't valid identifiers get a Pydantic
`alias`, so the model still reads and writes the real keys.

**How each format is read**

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
the rows they read. Parquet and ORC take it from the file footer, DuckDB from its catalog estimate, MongoDB
from the collection's metadata, and PostgreSQL, MySQL and MariaDB from the server's own statistics (a table
the server has never analysed has none). Other sources say that no hint is available.

**`--infer-keys`** marks key-looking columns that are unique, and guesses joins between tables (two CSVs
in a folder, the sheets of a workbook, a JSON array and a JSON Lines file). It reads CSV, TSV, JSON Lines,
JSON arrays and Excel; databases already declare their keys. What comes out is a yes or no, marked as a
guess, never a count or a value. To keep it from saying more than it should:

- only columns *named* like keys (`id`, `user_id`, `uuid`, `code`, ...) are examined;
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
--infer-keys`), and an agent can't turn them on or off.
