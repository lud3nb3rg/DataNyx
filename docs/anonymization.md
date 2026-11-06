# Text anonymization

```sh
uv sync --all-extras --group models         # spaCy plus en_core_web_lg and fr_core_news_md
# or: pip install -e ".[text]" && python -m spacy download en_core_web_lg   (about 400 MB)

datanyx anonymize notes.txt                 # print to stdout
datanyx anonymize notes.txt -o clean.txt    # write to a file
cat notes.txt | datanyx anonymize -         # read stdin
```

Given this text:

```
Jessica Whitfield (jessica@northwind.example) called on March 3rd about invoice
INV-20419. Ms. Whitfield lives in Portland. Her manager Tomasz Kowalczyk agreed.
```

it produces:

```
<PERSON_1> (<EMAIL_1>) called on <DATE_1> about invoice
<ID_1>. Ms. <PERSON_1> lives in <LOCATION_1>. Her manager <PERSON_2> agreed.
```

A name found once is hidden everywhere it appears, including parts of it like the surname
above. Two people sharing a surname aren't linked by it. Spacing and line endings are
preserved.

| Option | Purpose |
|---|---|
| `-o`, `--output` | Write to a file instead of stdout. It refuses to overwrite the input |
| `--model` | spaCy model, such as `fr_core_news_md` for French text |
| `--hide` | Always hide this term, ignoring case (repeatable) |
| `--keep` | Leave this exact term visible (repeatable) |
| `--keep-entity` | Leave a kind of entity visible, such as `DATE` (repeatable) |
| `--mapping` | Also save each placeholder's real value to a new file ([details](#restoring-real-values)) |

**What it hides**

- **Using the NER model:** `PERSON`, `ORG`, `LOCATION`, `GROUP` (nationalities, religious
  and political groups), `DATE`, `MONEY` and `MISC`
- **Using patterns:** `EMAIL`, `URL`, `IP_ADDRESS`, `MAC_ADDRESS`, `CREDIT_CARD`, `IBAN`,
  `PHONE`, `ADDRESS`, `ID` (long numbers, UUIDs, keys and references), plus common `DATE`
  and `MONEY` formats in several languages

If spaCy or the model isn't installed, it stops with an error and outputs nothing. It
doesn't fall back to patterns alone, because names would pass through unnoticed.

Use `--hide` for names you already know, such as your company or clients: it's the one
reliable way to catch them. Run it yourself rather than through an agent, since the term
is on the command line.

## Folders, documents and streams

`anonymize` takes a folder as well as a file. Every document in it is anonymized with **one set of
placeholders**, so a person in the tenth file is the same `<PERSON_1>` as in the first, and one
mapping file restores the whole folder:

```sh
datanyx anonymize ./reports -o ./clean -r --mapping reports.mapping.json
datanyx restore ./clean -o ./restored --mapping reports.mapping.json
```

Output files are named `file_001.txt`, `file_002.docx`... because a file name can be as sensitive as
what is in it, and the mapping records what each was called. `--keep-names` keeps the real names
when you know they are safe. `-r` reads subfolders. A file DataNyx can't read is skipped and counted,
and one that fails doesn't stop the rest. An output folder inside the input, or one that already
has a file of the same name, is refused.

| Format | Result |
|---|---|
| Text, Markdown, logs, CSV, JSON and similar | The same file, with placeholders. Line endings are kept |
| HTML | The same page: tags and layout stay. Text, comments, strings and comments in scripts and stylesheets, and descriptive attributes (`title`, `alt`, `href`, `src`, `value`, `data-*`, `aria-*`...) change, quoted or not. Text with nothing to hide keeps its original spelling |
| Word (`.docx`) | The same document: styles, tables and layout stay. A paragraph with something to hide loses the formatting *inside* it. Text boxes, tracked deletions, alt text and links out of the document are anonymized, the authors of changes and comments replaced, and author, company, title, custom properties and embedded data cleared |
| Email (`.eml`) | A text file of the headers and body. People become placeholders (a sender is `<PERSON_1> (<EMAIL_1>)`), routing headers and attachments are left out, and attachment names are listed |
| PDF | A text file of the pages, with page markers (needs `.[pdf]`) |
| WhatsApp export | The same chat, each sender one placeholder, so who said what is still clear. Continuation lines and system messages are handled |
| Slack export | The same JSON. People, user ids, emails and message text are replaced, mentions line up with the people, and timestamps and types stay |

Anything else is read as plain text if you name the file, and skipped inside a folder.

**Streams.** `--stream` anonymizes stdin a line at a time as it arrives, so it works on a live log.
The same names keep the same placeholders for as long as it runs, and a `--mapping` file is rewritten
as it grows, so it survives a stopped stream:

```sh
tail -f app.log | datanyx anonymize - --stream --mapping app.mapping.json
```

**What these don't do.** Each line of a stream is judged on its own, so a name split across lines isn't
joined. Inside HTML only the strings and comments of a `<script>` or `<style>` are read, so a value written
into the code itself stays: check scripts yourself. Images and embedded objects in Word files and the pictures in a PDF aren't read, and a
scanned PDF has no text to find; the notes printed after a run say when that happened. Changed HTML
text is re-escaped, so `&nbsp;` becomes a plain non-breaking space. WhatsApp's two usual layouts are
recognised, and other chat apps aren't. A person is matched by exact name across documents: "Whitfield"
alone in a later file isn't linked to "Jessica Whitfield" from an earlier one.

## Restoring real values

If an agent writes something using placeholders, you can put the real values back. This
is opt-in, and no mapping is saved unless you ask for one:

```sh
datanyx anonymize notes.txt -o clean.txt --mapping notes.mapping.json
# ...the agent works from clean.txt and writes summary.txt...
datanyx restore summary.txt --mapping notes.mapping.json -o summary.real.txt
```

> **The mapping file holds the data you're hiding, in plain text.** Treat it like the
> original: keep it where the agent can't read it, and delete it when you're finished. Don't
> run `restore` through an agent either. It requires `-o`, so real values don't appear in
> the command's output, but an agent could still open the result.

- `*.mapping.json` is ignored by git here, so use that ending.
- An existing mapping is never replaced, since placeholders only mean something for the run
  that made them. Use a new file for each run.
- A placeholder covers every form of a name and restores to the longest, so `Ms. <PERSON_1>`
  becomes `Ms. Jessica Whitfield` even if the original said `Ms. Whitfield`.
- Placeholders not in the mapping, such as ones an agent invented, are left as they are and
  listed in a note.
- The mapping file is made readable by its owner only, on Linux, macOS and Windows.

## How reliable text anonymization is

> **Treat the output as a draft to review, not a guarantee.** A schema contains no data by
> construction, but anonymized text is your original text minus whatever got detected.

These are results for `en_core_web_lg` on 72 invented names from 12 regions in 6 sentence
templates. It's a small synthetic test, not a real-world accuracy figure:

| Name appears as | Hidden |
|---|---|
| First name only | 98.6% |
| Full name in ALL CAPS | 97.0% |
| Full name, capitalized normally | 95.6% |
| Whole line in lowercase | 94.2% |
| Whole line in ALL CAPS | 93.1% |
| Full name in lowercase, in a normally capitalized sentence | 83.3% |

- **Unfamiliar names get missed more often.** Most misses were East Asian and Pacific
  names, or names with accents. Don't rely on this for one population without testing it.
- **Context isn't detected.** "The CEO's daughter" or a rare diagnosis in a small town can
  identify someone without containing a name, and nothing here catches that.
- **It over-hides sometimes.** Words like `quarterly` count as dates, and capitalized words
  like `Branch` can be taken for organisations.
- **Model labels differ.** French models have no `DATE` or `MONEY` label, so those rely on
  patterns. `fr_core_news_md` is the only non-English model tested.
