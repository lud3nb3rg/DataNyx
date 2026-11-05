# Synthetic data

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
