# Synthetic data

An agent that can't see results can't debug its code. `datanyx synth` writes fake data that
matches a schema, so the agent can run and test against it, and you run the finished code on
the real data.

```sh
datanyx synth customers.csv --rows 200 --format csv -o fake.csv
datanyx synth shop.db --rows 50 --format sqlite -o fake.db      # a whole database
datanyx synth ./exports/ --format jsonl -o fake/                # a folder: one file per table
datanyx synth events.json --seed 7                              # to stdout, as JSON
datanyx synth customers.csv --messy --format csv -o messy.csv   # with the flaws real data has
```

It reads the input exactly as `datanyx schema` does and takes the same options for reading it
(`--map-path`, `--keep-name`, `--public-column`, `--infer-keys`, `-r`, `--schema`, `--source`, `--delimiter`
and the rest; see the [command reference](cli-reference.md#datanyx-synth)). Then it generates from the schema alone, so
no real value is copied. Hidden keys stay hidden: they come out as invented ones.

| Option | Purpose |
|---|---|
| `--rows` | Rows per table (default 100, at most 1,000,000) |
| `--seed` | Makes the output repeatable (default 0). Same seed, same data |
| `--messy` | Give the rows the flaws real data has ([below](#messy-rows)) |
| `--format` | `json` (default), `jsonl`, `csv` or `sqlite` |
| `-o`, `--output` | A file, or a folder when there are several tables. Needed for `sqlite`. Existing files are never overwritten |

## What the rows look like

- **Databases hold together.** Primary keys are unique and a foreign key always points at a
  row that exists, including composite keys and join tables. Nullable foreign keys are
  sometimes null. The `sqlite` output has the same schema as the source database, and passes
  `PRAGMA foreign_key_check`. Columns the database declares unique stay unique, and so do the unique
  columns and joins that `--infer-keys` finds in files.
- **Shapes match.** Types, formats (emails, dates, UUIDs...), nesting, optional fields, nulls and
  arrays follow the schema. Reading the synthetic file back gives the same schema.
- **Values look plausible.** Field names are used as hints: `city` gets cities, `age` gets
  ages, `price` gets amounts, and a plain `id` counts up. Emails use `example.com`, IPs use
  the documentation range and phone numbers use the fictional `555-01xx` block. A
  [public column](schemas.md#public-columns) gets its listed labels, so code meets the real spellings.
- **Limits.** Values are random, so they don't follow real distributions or relationships
  between columns (the city doesn't match the country). Hidden columns have no counterpart
  in CSV or SQLite and are left out, with a note.

## Messy rows

Clean fake rows let code pass that the real data will break: a total that silently skips `"n/a"`, a
group-by that splits `"Pro"` from `"pro "`, a count inflated by a row exported twice. `--messy` puts
those flaws in, so they show up while the agent can still see the output:

```console
$ datanyx synth customers.csv --rows 8 --format csv --messy
id,email,signed_up,plan,lifetime_value,notes
1,jonas.larsen6@example.com,2022-11-26,meadow juniper,358.05,Ember zephyr delta timber indigo.
4,priya.rossi31@example.com,2021-08-06,cobalt cobalt,875.09,
3,omar.larsen91@example.com,2020-01-05,harbor yarrow,870.47,
4,priya.rossi31@example.com,2021-08-06,cobalt cobalt,875.09,
...
7,amara.singh15@example.com,2025-06-15,garnet harbor,-0.5,
8,li.novak55@example.com,2020-09-11,WILLOW,0.5,
```

About 15% of values are flawed, as far as the schema allows:

| In a column of | Flaws |
|---|---|
| Plain text (no format) | Leading, trailing or doubled spaces, a tab, ALL CAPS, lowercase or Title Case, empty strings |
| Plain text whose name says date (`created`, `dob`, `updated_at`...) | Dates in several styles: `03/04/2024`, `04.03.2024`, `2024/03/04`, `04 Mar 2024`, `Mar 04, 2024`, `20240304` |
| Numbers | Extremes: `0`, `-1`, negatives down to -1,000, and numbers in the millions to billions. A plain `id` still counts up |
| Text and numbers mixed (`string \| number`) | Its text is never prose: a marker for missing (`n/a`, `NA`, `-`, `null`, `none`, `unknown`, `?`, empty) or a number written as text (`1,234.50`, `1234,50`, `$1,234.50`, `1234%`, padded with spaces) |
| Anything nullable | Twice as many nulls |
| Whole rows | About 3% of rows are copies of others, in a table with no primary key and no unique column |

Like the rest, all of it is invented: the markers and styles are fixed lists in the code, and nothing
comes from the data. Primary keys, unique columns and foreign keys are left clean, so a messy database
still passes `PRAGMA foreign_key_check` and its tables still join. A file's `id` column is only a key if
the schema says so (`--infer-keys`), so without that it can repeat, as in the rows above. The same
`--seed` gives the same mess, and without `--messy` the output is exactly what it was.

The MCP server's `synthetic_sample` tool takes `messy` too, and its instructions tell the agent to try its
code on messy rows before calling it finished.

## From Python

```python
from datanyx import extract, synthesize
from datanyx.synth import render_table

tables = synthesize(extract("shop.db"), rows=50, seed=1, messy=False)
print(render_table(tables[0], "jsonl"))     # or "json", "csv"
```
