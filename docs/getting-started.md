# Getting started

## Install

DataNyx isn't published on PyPI yet, so install it from a clone.

```sh
git clone <this repository> datanyx && cd datanyx
uv sync --all-extras                 # or: pip install -e ".[all]"

datanyx schema customers.csv --format text
datanyx doctor                       # shows which optional packages and models are installed
```

Pick only what you need if you'd rather keep it small, for example `pip install -e .` for
CSV, JSON, XML and SQLite, or `pip install -e ".[parquet,excel]"`. The
[sources table](sources.md) lists the extra for each format.

Requires Python 3.10 or newer.

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
[TypeScript, Pydantic models or SQL](schemas.md#output-formats) with `--format`.

To let the agent try its code and see real output, [generate fake rows](synthetic-data.md)
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

## Why it exists

You want an agent to write a report, a query or a data-cleaning script, but the data is
customers, patients or payroll. Pasting rows into a chat is a leak. Describing the
columns by hand is tedious and error-prone.

DataNyx sits in between:

|  | What the agent gets | What it never gets |
|---|---|---|
| **Structured data**<br>`datanyx schema` | Field names, types, formats, nesting, primary and foreign keys | Values, examples, enum labels, counts or statistics |
| **Free text**<br>`datanyx anonymize` | The text with `<PERSON_1>`, `<EMAIL_1>` and friends in place of identifiers | The identifiers themselves (best effort: [review the result](anonymization.md#how-reliable-text-anonymization-is)) |
