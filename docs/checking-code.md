# Checking code before it runs

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
uv tool install "datanyx[check]"                           # SQL checking needs sqlglot
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
