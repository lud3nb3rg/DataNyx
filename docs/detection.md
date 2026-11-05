# Detection quality and custom detectors

## Custom detectors

For formats and names that are yours alone, describe a detector instead of writing Python. Put the
entries in `datanyx.toml`, or in a separate file named by `detectors = [...]` or `--detectors FILE`:

```toml
[[pattern]]
kind = "EMPLOYEE_ID"          # the placeholder becomes <EMPLOYEE_ID_1>
regex = 'EMP-\d{6}'
flags = "i"                   # optional: any of i, m, s
group = 1                     # optional: mark only this part of the match

[[dictionary]]
kind = "CLIENT"
terms = ["Acme", "Northwind"]
terms_file = "clients.txt"    # optional: one term per line, # for comments
ignore_case = true            # the default
word_boundaries = true        # the default: "Acme" doesn't match inside "Acmeville"
```

- Kinds are capital letters and underscores. They are found by `anonymize`, `scrub`, the MCP server's
  `anonymize_file`, and `scan`, which counts them by default and accepts them in `--kinds`.
- A custom kind wins a tie over a built-in one on the same text, so a ticket number is
  `<TICKET_1>` rather than a generic `<ID_1>`. `--keep-entity TICKET` leaves a kind visible, and
  `restore` puts custom placeholders back like any other.
- A bad regex, an unknown key or a missing terms file is an error that never repeats a term. Terms
  can be sensitive, so a dictionary of real client names belongs in `datanyx.local.toml` or another
  file you don't commit.

## Measuring and improving detection

The accuracy table above is for invented names in invented sentences. What matters is *your* text, your
names and your languages, so measure that. Label a sample (a few dozen documents is enough to see the
pattern) and run `datanyx eval`:

```json
[
  {"name": "ticket 12",
   "text": "Jessica Whitfield wrote from jw@acme.io about the Leeds branch.",
   "sensitive": {"PERSON": ["Jessica Whitfield"], "EMAIL": ["jw@acme.io"], "LOCATION": ["Leeds"]}},
  {"text": "Call Ana on 555 0100", "spans": [{"start": 5, "end": 8, "kind": "PERSON"}]}
]
```

```console
$ datanyx eval labelled.json
Evaluated 7 documents, 60 labelled strings.

Kind             Strings  Hidden  Partly  Missed   Recall
ADDRESS                2       2       0       0   100.0%
EMAIL                  2       2       0       0   100.0%
LOCATION               7       7       0       0   100.0%
ORG                    6       6       0       0   100.0%
PERSON                24      24       0       0   100.0%
...                  (more kinds, abridged here)
Overall               60      60       0       0   100.0%

Replaced but not in your labels: 9 (DATE 7, ORG 2). That is over-hiding, or something the labels left out.
```

Each document is anonymized on its own, as `anonymize` does, with the same models, packs, backends,
custom detectors and settings it would use, so a result is what you would really get. A string counts as
**hidden** when none of its identifying words survives, **partly hidden** when some do, and **missed**
when all do. Titles ("Dr.") and joining words aren't identifying, and matching is by exact case, so the
name "Will Turner" isn't leaked by an ordinary "will". Labels can be strings or character spans, in a JSON
list, an object with `documents`, or JSON Lines.

| Option | Purpose |
|---|---|
| `--show-misses` | List what wasn't fully hidden. These are strings from your file: read them yourself |
| `--kind KIND` | Only measure this kind (repeatable) |
| `--no-ner` | Leave out the name models and backends, to see what patterns alone find |
| `--min-recall 0.95` | Exit with 1 if recall, overall or for any kind, is lower: a gate for CI |
| `--format json` | For scripts |

Run it again with another `--model`, a `--backend` or a `--pack` to compare. The same label file is
the fair way to choose between them. A few dozen strings can't promise a rate, but 0% and 100% are
informative, and `--show-misses` shows what the gaps look like.

**Several languages.** `--model en_core_web_lg --model fr_core_news_md` (or `models = [...]` in the
config) runs several spaCy models and merges what they find. `xx_ent_wiki_sm` is a small multilingual
model, with less accuracy than the language-specific ones.

**Packs** add detectors for fields whose formats have rules. Use `--pack healthcare`, `--pack finance`
or `packs = ["healthcare"]` in the config. Where a format has a check digit it is verified, so a random
run of digits isn't mistaken for an account, and labelled fields need a label or a digit:
"member id: pending" isn't a member id.

| Pack | Finds |
|---|---|
| `healthcare` | The HIPAA Safe Harbor identifiers that have a form: patient and provider names after "Patient:" or "Dr.", SSN, medical record numbers, health plan and member numbers, NPI and DEA numbers (check digits verified), vehicle (VIN) and device identifiers, ZIP codes after a state, ages over 89, fax numbers |
| `finance` | Account numbers, routing numbers (checksum verified), SWIFT/BIC codes, sort codes, ISINs (check digit verified), EIN and tax ids, UK National Insurance numbers, VAT numbers, SSNs, crypto wallet addresses |

Dates, phone numbers, emails, URLs, IP addresses, card numbers and IBANs are found by the general detectors.
A pack helps with de-identification but isn't a certification that a text meets any standard, and it
finds identifiers, not meaning: a diagnosis, or a description that points at one person, isn't something a
pattern sees. Scanning with a pack counts its kinds by default, and `--keep-entity SSN` leaves one visible.

**Backends** run another detector beside the built-in ones and merge the results. Choose them with
`--backend` (repeatable) or `backends = [...]`:

| Backend | What it adds |
|---|---|
| `presidio[:LANG[:MODEL]]` | Microsoft Presidio's recognisers for many national ids and formats. Needs `.[presidio]` and a spaCy model for the language (`presidio:de:de_core_news_lg`) |
| `gliner[:MODEL]` | A GLiNER model for personal data, useful for names the spaCy models miss and for languages they don't cover. Needs `pip install gliner`; the model downloads once from Hugging Face, and the text itself never leaves your machine |
| `ollama[:MODEL[@URL]]` | A language model running on this computer through Ollama, asked to quote what is sensitive in each passage |

The Ollama backend **refuses any server that isn't on this computer**: sending sensitive text to a remote
model would defeat the purpose. A language model can also invent findings or miss real ones, and a backend
that is slow or wrong costs you time, so compare each with `datanyx eval` before relying on it. `datanyx
doctor` shows which backends are installed.
