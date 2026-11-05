# Debugging without seeing the data

Even a correct-looking script fails on real data, and its error can hold that data:
`KeyError: 'jane@example.com'`, a path with a username in it, a printed row. `datanyx run`
runs the command for you and shows only a scrubbed view:

```console
$ datanyx run --names-from customers.csv -- python report.py
Traceback (most recent call last):
  File "./report.py", line 5, in <module>
    raise RuntimeError(f"no billing record for {r['email']} (customer Li, balance {r['lifetime_value']}0000)")
RuntimeError: no billing record for <EMAIL_1> (customer <PERSON_1>, balance <ID_1>)
datanyx: exit code 1; stdout hidden (no output)
```

The exception type, the file and line, and the code stay, so the agent can still fix the
bug. The values are gone. The command's exit code is passed on.

```sh
datanyx run -- python report.py                    # stderr scrubbed, stdout reduced to its size
datanyx run --stdout scrubbed -- python report.py  # also show a scrubbed copy of stdout
datanyx run --save-stdout out.txt -- python report.py   # you read the real output, the agent doesn't
datanyx scrub error.log                            # the same filter for text you already have
```

Pass `--names-from customers.csv` (or a database, folder or `env:NAME`) so that column names
in errors survive: `KeyError: 'plan'` stays readable, while `KeyError: 'Plan'`, which isn't a
name the schema shows, is hidden.

| Option | Purpose |
|---|---|
| `--names-from INPUT` | Keep quoted names that this input's schema shows (repeatable) |
| `--allow NAME` | Keep one more quoted name (repeatable) |
| `--hide TERM` | Always remove this term (repeatable) |
| `--stdout` | `shape` (default: only its size), `scrubbed` or `none` |
| `--timeout SECONDS` | Stop the command after this long (exit code 124) |
| `--save-stdout FILE`, `--save-stderr FILE` | Also write the real output to a new file, for you |
| `--keep-numbers` | Don't replace long numbers |
| `--no-ner` | Skip the name-finding model: faster, but names in unquoted text get through |

**How text is cleaned**, in order:

1. Secrets go: the values of environment variables that look like credentials, and the
   password inside a URL.
2. Your home folder, working folder and username are shortened to `~`, `.` and `<USER>`.
3. In a Python traceback the `File` lines and the code under them are left alone. They are
   your script, not the data. Every other line is a message.
4. In a message, anything in quotes is replaced unless it is a name from the schema. Errors
   quote values far more often than they quote names.
5. The same detectors as [text anonymization](anonymization.md#text-anonymization) replace emails, IDs and,
   with the model, names. One person keeps one placeholder.
6. Long numbers and decimals are replaced. Short integers stay, since they are mostly line
   numbers and counts.

**Limits.** It's best effort, like anonymization. A value that is neither quoted nor
recognised, such as a bare name the model misses, gets through. A script that prints a table
of numbers shows them under `--stdout scrubbed` unless they are long. `run` doesn't sandbox
anything: the command can read whatever you can, so `--stdout shape` (the default) is what keeps
printed rows from the agent. Like `anonymize`, it stops if the spaCy model is missing unless
you pass `--no-ner`.

```python
from datanyx.scrub import ScrubPolicy, scrub

print(scrub(error_text, ScrubPolicy(allowed_names=frozenset({"plan", "email"}))))
```
