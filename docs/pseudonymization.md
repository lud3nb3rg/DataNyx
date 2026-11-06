# Pseudonymizing tables

Text anonymization turns names into placeholders. For tables, that would break joins and make emails
stop looking like emails. `datanyx pseudonymize` writes a copy in which each real value becomes a
**consistent fake one**: the same customer is the same fake customer in every row, table and file,
ids still match across tables, and the data stays realistic enough to develop against or share.

```console
$ datanyx pseudonymize customers.csv -o fake.csv --key-file pseudonyms.key
Plan:
  customer_id  id          the name ends in id
  name         name        the name says name
  email        email       the name says email
  phone        phone       the name says phone
  city         city        the name says city
  plan         keep        nothing personal was recognised   <- kept: check it
  salary       keep        nothing personal was recognised
  notes        text        the name says free text
  joined       keep        dates are kept, except birth dates

Kept as they are, and holding text: plan. Check them, and use --column NAME=KIND or --strict.
Wrote 3 rows to fake.csv. Replaced: 3 id, 3 name, 3 email, 3 phone, 3 city, 1 text.
```

```sh
datanyx pseudonymize customers.csv --plan                     # see the decisions, write nothing
datanyx pseudonymize shop.db -o shop-fake.db --key-file pseudonyms.key   # a database, rebuilt in a new file
datanyx pseudonymize events.jsonl -o fake.jsonl --column notes=text --keep-column region
datanyx pseudonymize customers.csv -o fake.csv --strict       # replace text that wasn't recognised too
```

It reads CSV, TSV, JSON, JSON Lines and SQLite files. **It reads the real values, so run it yourself
rather than through an agent**; `datanyx protect` refuses to run it for an agent. Keep the key and
mapping files inside a path you protect, or outside the project, since the guard doesn't know about them.

| Option | Purpose |
|---|---|
| `-o`, `--output` | The new file. Existing files are never overwritten, and a database is rebuilt, not edited |
| `--plan` | Show what each column would become, and stop |
| `--key-file FILE` | A secret key, created if missing. **With the same key, the same value gets the same fake in every run**, so a fresh export lines up with the last. Without it, a new random key is used and the fakes won't match another run's |
| `--mapping FILE` | Also save which fake stands for which real value, for every kind. It holds real data |
| `--column NAME=KIND` | Treat a column as this kind (repeatable); `TABLE.COLUMN=KIND` for one table of a database |
| `--keep-column NAME` | Leave a column as it is |
| `--strict` | Replace text columns that weren't recognised, instead of keeping them |
| `--source` | The input type, if the extension doesn't say |

**What becomes what.** A column's kind comes from your override, `--keep-column`, being a key in a
database, its name, then what its values look like. The kinds are `name`, `first_name`, `last_name`,
`email`, `phone`, `address`, `city`, `company`, `postal`, `id`, `token`, `date`, `text`, `ip`, `url`,
`category` (distinct words), `keep` and `drop`.

- **Names, emails, companies, cities and addresses** are made of invented, pronounceable words, such as
  `Brealkos Daventi`, in a space large enough that two real values practically never draw the same fake.
  So a value's fake doesn't depend on what else is in the file, and a fresh export lines up with the last.
  Two different real values never share a fake: should they clash, the later one is drawn again, and with
  `--key-file` a note says that fake may differ in another run. Emails are `first.last42@example.com`.
- **Whole-number ids** go through a keyed permutation of their own range, so the result has the same
  number of digits and **two ids can never become one**. That is what keeps foreign keys valid: the
  `PRAGMA foreign_key_check` of a pseudonymized database is clean, and joins return the same row counts.
- **Phone numbers, postal codes, account numbers and other codes** keep their shape: a digit becomes a
  digit, a letter a letter, `+44` and the punctuation stay. In `+14155552671` nothing shows where the
  country code ends, so only `+1` stays. A number stored as a number stays one.
- **Dates** are kept, except birth dates, which move by one secret number of days, so the gaps between
  them survive. A date not written as `YYYY-MM-DD` is kept and counted.
- **Free text** goes through [text anonymization](anonymization.md#text-anonymization), so names inside it become
  placeholders like `<PERSON_1>` (these don't match the fake name in the `name` column).
- **JSON keys** work the same as columns, wherever the key appears, even first seen after the sample. A key
  that is itself data, such as an email used as a key, is replaced, and agrees with the same email used as a
  value. Text under such a key, or with no key at all, is replaced going by what it looks like.
- **CSV**: blank lines before the header are fine, columns are told apart by position even if two share a
  name, and a cell past the last column is replaced, as there's no name to judge it by.
- **SQLite**: tables, keys and indexes are copied, and views too. **Triggers are left out**, since they
  can hold data or change values, and virtual tables are skipped with what they keep their contents in. So are column defaults, `CHECK` constraints,
  comments, and any view or index with a literal in it, as those are text that can hold real values. Binary
  values are emptied unless you name the column with `--keep-column`. The notes at the end say what happened.

**What it can't promise.** *Pseudonymized is not anonymous.* Look at the plan before you share the copy.

- Anything kept is exactly as it was: numbers (a salary), categories (a diagnosis in a `plan` column) and
  most dates. A few such columns together, such as postcode, birth year and sex, can identify a person
  even when every name is gone. Columns that hold text but weren't recognised are listed with
  "check it"; `--strict` replaces them.
- A column is recognised by its name and its values, which can be wrong in both directions. That is why
  the plan is printed, and `--column` exists.
- Anyone with the key file can test a guess ("is this the fake for Ana Silva?"), so keep it with the
  real data, not with the copy. The key file is readable by its owner only.
- A phone number or postal code made by changing digits can, by chance, be a real one.
- Fakes from before this format changed won't match: re-export everything that has to line up.

```python
from datanyx.pseudonymize import Options, Pseudonymizer, load_key, pseudonymize_file

pseudo = Pseudonymizer(load_key("pseudonyms.key"))
pseudonymize_file("customers.csv", "fake.csv", "csv", pseudo, Options())
print(pseudo.apply("email", "jane@acme-corp.io"))      # the same fake every time
```
