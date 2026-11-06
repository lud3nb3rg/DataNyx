# Command reference

Every command and option, generated from the program itself. For what each command is for, see its
own page; `datanyx COMMAND --help` prints the same information in a terminal.

## Options for every command

| Argument | Meaning |
|---|---|
| `--config` `FILE` | settings file to use instead of the nearest datanyx.toml |
| `--no-config` | ignore datanyx.toml |

## `datanyx schema`

print a value-free schema of the input

```
datanyx schema input [options]
```

| Argument | Meaning |
|---|---|
| `input` | file or folder to describe, "-" for stdin, a database URL, or env:NAME to read the URL from an environment variable (keeps passwords off the command line) |
| `-r`, `--recursive` | when the input is a folder, also read its subfolders |
| `--schema` `SCHEMA` | database schema to describe (default: the database's default) |
| `--source` `SOURCE` | input type (default: detect). Choices: `avro`, `csv`, `duckdb`, `excel`, `fhir`, `hl7`, `json`, `jsonl`, `mongodb`, `orc`, `parquet`, `sas`, `spss`, `sql`, `stata`, `tsv`, `xml`, `yaml`. |
| `--delimiter` `CHAR` | the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV) |
| `--scale-hints` | also give each table's size as an order of magnitude ("10^3 to 10^4 rows"), never a number |
| `--infer-keys` | also flag key-looking columns that are unique, and joins between tables, as yes/no guesses (CSV, TSV, JSON Lines, JSON and Excel) |
| `--views` | also describe database views (names and columns only) |
| `--include` `PATTERN` | describe only tables (or files) whose names match this glob, such as 'sales_*' (repeatable) |
| `--exclude` `PATTERN` | leave out tables (or files) whose names match this glob (repeatable) |
| `--max-tables` `N` | describe at most N tables and say how many were left out; use --format summary to list them all |
| `--map-path` `PATH` | hide all keys of the object at PATH, e.g. "$.users" or "$[].tags", or all columns of a table, e.g. "$.users" (repeatable) |
| `--keep-name` `NAME` | show this exact key, table or column name even if it looks like data (repeatable); --map-path still hides it |
| `--format` `FORMAT` | output format (default: json-schema). Choices: `json-schema`, `pydantic`, `sql`, `summary`, `text`, `typescript`. |
| `-o`, `--output` `OUTPUT` | write to this file instead of stdout |

## `datanyx synth`

Write synthetic rows that fit the schema of the input, so code can be tried out without real data. Only the schema is used, so nothing real is copied. Foreign keys point at rows that exist.

```
datanyx synth input [options]
```

| Argument | Meaning |
|---|---|
| `input` | file or folder to describe, "-" for stdin, a database URL, or env:NAME to read the URL from an environment variable (keeps passwords off the command line) |
| `-r`, `--recursive` | when the input is a folder, also read its subfolders |
| `--schema` `SCHEMA` | database schema to describe (default: the database's default) |
| `--source` `SOURCE` | input type (default: detect). Choices: `avro`, `csv`, `duckdb`, `excel`, `fhir`, `hl7`, `json`, `jsonl`, `mongodb`, `orc`, `parquet`, `sas`, `spss`, `sql`, `stata`, `tsv`, `xml`, `yaml`. |
| `--delimiter` `CHAR` | the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV) |
| `--scale-hints` | also give each table's size as an order of magnitude ("10^3 to 10^4 rows"), never a number |
| `--infer-keys` | also flag key-looking columns that are unique, and joins between tables, as yes/no guesses (CSV, TSV, JSON Lines, JSON and Excel) |
| `--views` | also describe database views (names and columns only) |
| `--include` `PATTERN` | describe only tables (or files) whose names match this glob, such as 'sales_*' (repeatable) |
| `--exclude` `PATTERN` | leave out tables (or files) whose names match this glob (repeatable) |
| `--max-tables` `N` | describe at most N tables and say how many were left out; use --format summary to list them all |
| `--map-path` `PATH` | hide all keys of the object at PATH, e.g. "$.users" or "$[].tags", or all columns of a table, e.g. "$.users" (repeatable) |
| `--keep-name` `NAME` | show this exact key, table or column name even if it looks like data (repeatable); --map-path still hides it |
| `--rows` `ROWS` | rows per table (default: 100) |
| `--seed` `SEED` | makes the output repeatable (default: 0) |
| `--format` `FORMAT` | output format (default: json). Choices: `json`, `jsonl`, `csv`, `sqlite`. |
| `-o`, `--output` `OUTPUT` | file to write, or a folder when the input has several tables (one file each); required for sqlite. Existing files are never overwritten |

## `datanyx pseudonymize`

Write a copy of a CSV, TSV, JSON, JSON Lines or SQLite file in which names, emails, phone numbers, addresses, ids and free text are replaced by fake values. The same real value is the same fake value everywhere, so joins keep working, and emails still look like emails. This reads the real values, so run it yourself, not through an agent. Pseudonymized is not anonymous: check the plan, and see the documentation (pseudonymization page) for what it can't do.

```
datanyx pseudonymize input [options]
```

| Argument | Meaning |
|---|---|
| `input` | the file to copy: .csv, .tsv, .json, .jsonl or a SQLite database |
| `-o`, `--output` `OUTPUT` | the new file to write (never overwritten) |
| `--source` `SOURCE` | input type (default: from the extension). Choices: `csv`, `tsv`, `json`, `jsonl`, `sqlite`. |
| `--delimiter` `CHAR` | the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV) |
| `--key-file` `FILE` | a secret key, created if missing: with it the same value gets the same fake in every run. Keep it with the real data |
| `--mapping` `FILE` | also save which fake stands for which real value, to a new file. It holds real data: keep it away from agents |
| `--column` `NAME=KIND` | treat a column or key as this kind, overriding the guess (repeatable; TABLE.COLUMN for one table of a database) |
| `--keep-column` `NAME` | leave this column as it is (repeatable) |
| `--strict` | also replace text columns that weren't recognised, instead of keeping them |
| `--plan` | show what each column would become, and stop without writing anything |
| `--hide` `TERM` | for free text: always hide this term (repeatable) |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |

## `datanyx eval`

Run the anonymizer, with the same models, packs, backends and settings `anonymize` would use, over documents you have labelled, and report recall by kind. Run it again with other settings to compare them. See the documentation (detection page) for the file format.

```
datanyx eval cases [options]
```

| Argument | Meaning |
|---|---|
| `cases` | a JSON or JSON Lines file of labelled documents |
| `--kind` `KIND` | only measure this kind (repeatable) |
| `--no-ner` | leave out the name-finding models and backends, to see what patterns alone find |
| `--show-misses` | list the strings that weren't fully hidden (they come from your file: read them yourself) |
| `--min-recall` `FRACTION` | exit with 1 if recall, overall or for any kind, is below this (0 to 1) |
| `--format` `FORMAT` | output format (default: text). Choices: `text`, `json`. |
| `--hide` `TERM` | always hide this term (repeatable) |
| `--keep-entity` `KIND` | leave this kind visible (repeatable) |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |

## `datanyx scan`

Look through a folder for emails, phone numbers, card and IBAN numbers, IP addresses, addresses and credentials, and report where they are. No value is ever printed. Run it before pointing an agent at a folder. Names of people and companies need --ner.

```
datanyx scan PATH [options]
```

| Argument | Meaning |
|---|---|
| `PATH` | folders or files to scan |
| `--kinds` `KIND` | what to look for (repeatable; default: EMAIL, PHONE, CREDIT_CARD, IBAN, IP_ADDRESS, MAC_ADDRESS, ADDRESS, SECRET and your custom kinds; 'all' for every built-in kind) |
| `--ner` | also find names, organisations and places with the NER model (slow) |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |
| `--exclude` `GLOB` | skip names matching this pattern (repeatable) |
| `--max-size` `MB` | skip files larger than this (default: the config's, else 5) |
| `--include-examples` | also count example.com emails and loopback or documentation IPs |
| `--show-paths` | show real file names, even ones that look like data (run it yourself) |
| `--format` `FORMAT` | output format (default: text). Choices: `text`, `json`. |
| `--fail-on-findings` | exit with 1 if anything is found or a file that may hold data couldn't be read, for CI or a pre-commit hook |
| `--allow-unscanned` | with --fail-on-findings, don't fail because a file was too large or couldn't be read |

## `datanyx protect`

Set up Claude Code so the agent can't read the named files and folders with its own tools, and can only work with them through DataNyx: schema, synth, check, scrub and run. By default this only shows what it would change; pass --write to apply it. It is a guardrail, not a sandbox: a script the agent runs can still read the data.

```
datanyx protect PATH [options]
```

| Argument | Meaning |
|---|---|
| `PATH` | files or folders to protect |
| `--dir` `PROJECT` | the project folder (default: the current folder) |
| `--command` `LAUNCHER` | how Claude Code starts datanyx, such as 'uv run datanyx' (default: datanyx) |
| `--local` | use .claude/settings.local.json, which isn't shared, instead of settings.json |
| `--allow-broad-search` | don't refuse searches of a folder that has protected data inside it |
| `--no-mcp` | don't write .mcp.json |
| `--remove` | take protection out again |
| `--write` | apply the changes (the default is to show them) |

## `datanyx hook`

Used by Claude Code, not by hand: reads a PreToolUse payload and refuses calls that touch protected data.

```
datanyx hook [options]
```

| Argument | Meaning |
|---|---|
| `--require-config` | refuse everything if the protection settings are missing; `protect` installs the hook with this |

## `datanyx check`

Find misspelled columns, unknown tables, ambiguous joins and type mistakes in SQL or pandas code, using the schema of the data it works on. Nothing is run and no data is read by the code. Exits with 1 if there are errors.

```
datanyx check code [options]
```

| Argument | Meaning |
|---|---|
| `code` | file with the code (.sql or .py), or "-" for stdin |
| `--against` `INPUT` | what the code works on: a file, folder, database URL or env:NAME (repeatable) |
| `--table` `NAME` | the table name for the matching --against file, in order; a file has no name of its own in a schema |
| `--language` `LANGUAGE` | sql or python (default: from the file extension). Choices: `sql`, `python`. |
| `--dialect` `DIALECT` | SQL dialect, such as postgres, mysql, tsql, sqlite (default: guessed from --against) |
| `--format` `FORMAT` | output format (default: text). Choices: `text`, `json`. |
| `--strict` | exit with 1 for warnings too |
| `-r`, `--recursive` | when an input is a folder, also read its subfolders |
| `--schema` `SCHEMA` | database schema to use (default: the database's default) |
| `--source` `SOURCE` | input type for --against (default: detect). Choices: `avro`, `csv`, `duckdb`, `excel`, `fhir`, `hl7`, `json`, `jsonl`, `mongodb`, `orc`, `parquet`, `sas`, `spss`, `sql`, `stata`, `tsv`, `xml`, `yaml`. |
| `--delimiter` `CHAR` | the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV) |
| `--views` | also know the database's views (names and columns only) |
| `--map-path` `PATH` | hide all keys or columns at a path (repeatable) |
| `--keep-name` `NAME` | show this name even if it looks like data (repeatable) |

## `datanyx scrub`

Filter text so that values are gone but the diagnosis stays: exception types, line numbers and code are kept, quoted values, secrets, emails and long numbers are not. Best effort: review the result before sharing it.

```
datanyx scrub input [options]
```

| Argument | Meaning |
|---|---|
| `input` | text file to scrub (default: "-", stdin) |
| `-o`, `--output` `OUTPUT` | write to this file instead of stdout |
| `--names-from` `INPUT` | keep quoted names that this file, folder or database's schema shows, such as column names (repeatable) |
| `--allow` `NAME` | keep this quoted name (repeatable) |
| `--hide` `TERM` | always remove this term (repeatable) |
| `--keep-numbers` | don't replace long numbers and decimals |
| `--no-ner` | don't use the name-finding model: faster, but names in unquoted text get through |
| `--ner` | use the name-finding model even if the config turns it off |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |

## `datanyx run`

Run a command, usually a script that uses real data, and show scrubbed stderr and (by default) only the size of stdout. This doesn't sandbox the command: it can still read whatever you can.

```
datanyx run COMMAND [options]
```

| Argument | Meaning |
|---|---|
| `--names-from` `INPUT` | keep quoted names that this file, folder or database's schema shows, such as column names (repeatable) |
| `--allow` `NAME` | keep this quoted name (repeatable) |
| `--hide` `TERM` | always remove this term (repeatable) |
| `--keep-numbers` | don't replace long numbers and decimals |
| `--no-ner` | don't use the name-finding model: faster, but names in unquoted text get through |
| `--ner` | use the name-finding model even if the config turns it off |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |
| `--stdout` `STDOUT` | what to show of stdout: only its size (default), a scrubbed copy, or nothing. Choices: `shape`, `scrubbed`, `none`. |
| `--timeout` `SECONDS` | stop the command after this long |
| `--save-stdout` `FILE` | also save the real stdout to this new file, for you to read |
| `--save-stderr` `FILE` | also save the real stderr to this new file, for you to read |
| `COMMAND` | the command to run, after -- |

## `datanyx anonymize`

Replace sensitive text with placeholders like <PERSON_1>. Detection can miss things, so review the result before sharing it.

```
datanyx anonymize input [options]
```

| Argument | Meaning |
|---|---|
| `input` | a file or a folder to anonymize, or "-" for stdin: text, Markdown, logs, HTML, email (.eml), Word (.docx), PDF, WhatsApp and Slack exports |
| `-o`, `--output` `OUTPUT` | write to this file instead of stdout; for a folder, the new folder to write to |
| `-r`, `--recursive` | when the input is a folder, also read its subfolders |
| `--keep-names` | when the input is a folder, keep the file names; by default they become file_001, file_002... |
| `--stream` | anonymize stdin a line at a time as it arrives, for `tail -f app.log \| datanyx anonymize - --stream` |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |
| `--keep-entity` `KIND` | leave this kind of entity visible (repeatable): PERSON, ORG, LOCATION, GROUP, DATE, MONEY, MISC, EMAIL, URL, IP_ADDRESS, MAC_ADDRESS, CREDIT_CARD, IBAN, PHONE, ADDRESS, ID, or a custom kind |
| `--hide` `TERM` | always hide this term, ignoring case (repeatable) |
| `--keep` `TERM` | leave this exact term visible even if detected (repeatable) |
| `--mapping` `FILE` | also save each placeholder's real value to this new JSON file, for use with `datanyx restore`. It holds the sensitive data: keep it away from agents |

## `datanyx restore`

Put real values back. The result is sensitive, so don't run this through an agent.

```
datanyx restore input [options]
```

| Argument | Meaning |
|---|---|
| `input` | UTF-8 text containing placeholders, or "-" for stdin |
| `--mapping` `FILE` | file saved by `anonymize --mapping` |
| `-o`, `--output` `OUTPUT` | file to write the restored text to |

## `datanyx init`

create a datanyx.toml of commented settings in this folder

```
datanyx init [options]
```

| Argument | Meaning |
|---|---|
| `--local` | create datanyx.local.toml instead, for terms to hide that shouldn't be committed |

## `datanyx diff`

Compare two schemas, each a saved one (from `datanyx schema -o schema.json`) or any input DataNyx reads, and list what was added, removed or changed, sorted by whether code written against the old one could break. Exits with 1 if anything breaking changed.

```
datanyx diff old new [options]
```

| Argument | Meaning |
|---|---|
| `old` | the earlier schema: a saved JSON Schema file, or any input |
| `new` | the later schema, in the same forms |
| `--fail-on` `FAIL_ON` | when to exit with 1 (default: breaking). Choices: `breaking`, `any`, `never`. |
| `--format` `FORMAT` | output format (default: text). Choices: `text`, `json`. |
| `-r`, `--recursive` | when an input is a folder, also read its subfolders |
| `--schema` `SCHEMA` | database schema to use (default: the database's default) |
| `--source` `SOURCE` | input type for live inputs (default: detect). Choices: `avro`, `csv`, `duckdb`, `excel`, `fhir`, `hl7`, `json`, `jsonl`, `mongodb`, `orc`, `parquet`, `sas`, `spss`, `sql`, `stata`, `tsv`, `xml`, `yaml`. |
| `--delimiter` `CHAR` | the separator of CSV and TSV input, such as ';' or 'tab' (default: comma for CSV, tab for TSV) |
| `--views` | also compare the database's views (names and columns only) |
| `--map-path` `PATH` | hide all keys or columns at a path (repeatable) |
| `--keep-name` `NAME` | show this name even if it looks like data (repeatable) |

## `datanyx audit`

summarise the audit log an MCP server wrote: which tools were called, on what, and what failed

```
datanyx audit log [options]
```

| Argument | Meaning |
|---|---|
| `log` | the file given to `datanyx mcp --audit-log` |
| `--since` `TIME` | only calls from then on, such as 2025-01-31 or 2025-01-31T09:30:00 |
| `--format` `FORMAT` | output format (default: text). Choices: `text`, `json`. |

## `datanyx config`

show the settings in effect and which files they come from

```
datanyx config [options]
```

| Argument | Meaning |
|---|---|
| `--reveal` | also show the terms to hide, which are otherwise only counted |

## `datanyx doctor`

check which optional packages and models are installed

```
datanyx doctor [options]
```

| Argument | Meaning |
|---|---|
| `--model` `MODEL` | spaCy NER model to look for (default: en_core_web_lg) |

## `datanyx mcp`

Serve `describe_data` and `anonymize_file` over MCP (stdio, or HTTP with --http). The agent can only read files under --root, and never receives values, a mapping, or a restore tool.

```
datanyx mcp [options]
```

| Argument | Meaning |
|---|---|
| `--root` `DIR` | folder the agent may read (repeatable; default: current folder) |
| `--allow-urls` | accept database URLs from the agent (they may contain passwords) |
| `--allow-env` `NAME` | let the agent use this environment variable as a database URL, as env:NAME (repeatable); no other variable can be used |
| `--model` `MODEL` | spaCy NER model (default: the config's, else en_core_web_lg). Repeat it, or separate with commas, to use several, such as one per language |
| `--pack` `NAME` | a pack of detectors for a field: finance, healthcare (repeatable) |
| `--backend` `SPEC` | another detector to run as well: presidio[:LANG[:MODEL]], gliner[:MODEL] or ollama[:MODEL[@URL]] (repeatable) |
| `--detectors` `FILE` | a file of custom detectors for your own formats and names (repeatable) |
| `--keep-entity` `KIND` | leave this kind of entity visible in anonymized text (repeatable) |
| `--scale-hints` | give agents each table's size as an order of magnitude |
| `--infer-keys` | give agents yes/no guesses about unique columns and joins |
| `--views` | let agents see database views (names and columns only) |
| `--max-tables` `N` | most tables describe_data returns at once, so a huge database can't flood the agent (default: 200) |
| `--http` `HOST:PORT` | serve over HTTP at /mcp instead of stdio, so a team can share one server; needs a token (--token-env) |
| `--token-env` `NAME` | environment variable holding the bearer token HTTP clients must send (default: DATANYX_MCP_TOKEN) |
| `--audit-log` `FILE` | append a record of every tool call, never values, to this file |
