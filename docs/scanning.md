# Scanning a folder before sharing it

Before pointing an agent at a repository or an export folder, find out what is in it.
`datanyx scan` looks through the files for personal data and credentials and reports where they
are, without printing a single value.

```console
$ datanyx scan ./project
Scanned 214 files (3 binary skipped).

4 files with findings:
  data/customers.csv   EMAIL 1,204 · PHONE 311 · ADDRESS 18
  config/prod.env      SECRET 3
                       SECRET on lines 4, 9, 12
  src/seed.py          EMAIL 2
                       EMAIL on lines 41, 58
  file_7               EMAIL 3
                       EMAIL on lines 2, 3, 4

Total: EMAIL 1,209 · PHONE 311 · ADDRESS 18 · SECRET 3

Not scanned, but these binary data files may hold personal data: exports/people.xlsx, db/app.sqlite

note: 1 file name looked like data, so they are shown as file_N. Use --show-paths to see real paths,
and run it yourself rather than through an agent.

Names of people, companies and places are only found with --ner.
```

```sh
datanyx scan ./project                         # patterns for emails, phones, cards, IBANs, IPs, addresses, secrets
datanyx scan ./project --ner                   # also names, organisations and places (slow)
datanyx scan ./project --kinds EMAIL --kinds SECRET
datanyx scan ./project --format json           # for scripts
datanyx scan ./project --fail-on-findings      # exit 1 if anything is found or couldn't be read: a CI step or pre-commit hook
datanyx scan ./project --exclude 'fixtures' --exclude '*.lock'
```

| Option | Purpose |
|---|---|
| `--kinds` | What to look for (repeatable). The default is `EMAIL`, `PHONE`, `CREDIT_CARD`, `IBAN`, `IP_ADDRESS`, `MAC_ADDRESS`, `ADDRESS` and `SECRET`. `all` adds dates, money, URLs and bare IDs, which match too much ordinary code to be useful by default |
| `--ner` | Also find names, organisations and places with the NER model. Slow, so it is opt-in |
| `--exclude GLOB` | Skip names matching a pattern (repeatable), in a folder or among files named one by one, as a pre-commit hook passes them. `.git`, `node_modules`, virtual environments and caches are skipped already |
| `--max-size MB` | Skip larger files (default 5) |
| `--include-examples` | Also count `example.com` emails and loopback or documentation IP addresses, which are ignored by default |
| `--show-paths` | Show real file names, including ones that look like data |
| `--fail-on-findings` | Exit with 1 if anything is found, or if a file that could hold data wasn't read: too large, unreadable, or a binary data file. Use `--exclude` or `--max-size` for ones you've decided about. Images and other binary files don't count |

**Secrets** are found by their shape: private key headers, AWS, GitHub, Slack, Google and Stripe
keys, JWTs, the password in a `scheme://user:password@host` URL, and values assigned to names
like `password`, `api_key` or `client_secret`. Placeholders (`changeme`, `${SECRET}`,
`os.environ[...]`, a function call) are not counted. `SecretDetector` is also usable on its own
in Python and with `anonymize`.

**What a report reveals.** Counts, kinds and line numbers, never values. A file name is shown only
if it passes the same test as a column name, since `jane_doe_medical.csv` is itself sensitive;
others are `file_N` until you pass `--show-paths`. Run it yourself, or let an agent run it
through the MCP server's `scan_folder` tool, which always hides such names.

**Limits.** It finds what patterns find, so it misses names (without `--ner`), free-text
descriptions of a person, and anything in a binary file: spreadsheets, Parquet files and databases
are listed as not scanned, because they can't be read as text, and the report then doesn't claim
the folder is clean. UTF-16 and UTF-32 text files are read. Phone numbers are matched more
strictly than in anonymization (dates, version numbers and IPs are not counted), so some real
ones in unusual formats can be missed. Expect some false positives, such as test fixtures; the
scan of this repository finds the fake cards and keys in its own tests.
