# Python API

Everything the command line does is available from Python. Failures raise `datanyx.SourceError`, with a
message that's safe to show: like the command line's, it never quotes the data.

## Schemas

```python
import datanyx

print(datanyx.schema_of("orders.csv", "typescript"))          # a schema as text, in one call
```

For more control, work with the schema as objects. `KeyPolicy` holds what the command-line options for reading
data set: `map_paths`, `keep_names`, `scale_hints`, `infer_keys`, `column_hints`, `public_columns`, `views`,
`include`, `exclude`, `max_tables`, `max_rows` and `delimiter`.

```python
from datanyx import KeyPolicy, extract, render

policy = KeyPolicy(map_paths=frozenset({"$.users"}), keep_names=frozenset({"report_2024"}))

schema = extract("customers.json", policy=policy)
print(render(schema, "text"))        # json-schema (default), text, typescript, pydantic, sql or summary
print(schema.notes)                  # what was hidden or left out, as the command line prints to stderr

schema = extract("env:DATABASE_URL", namespace="sales")  # namespace is --schema
```

## Fake rows

```python
from datanyx import extract, synthesize
from datanyx.synth import render_table

tables = synthesize(extract("shop.db"), rows=50, seed=1, messy=True)   # see Synthetic data
print(render_table(tables[0], "csv"))                                 # json, jsonl or csv
```

## Text

```python
import datanyx
from datanyx.text import Anonymizer, PatternDetector, SecretDetector, SpacyDetector, anonymize, anonymize_with_mapping, restore

print(datanyx.anonymize_text("Jessica Whitfield called.", hide=["Acme"]))   # the default detectors

detectors = [PatternDetector(), SecretDetector(), SpacyDetector("en_core_web_lg")]
print(anonymize("Jessica Whitfield called.", detectors, hide_terms=["Acme"]))

clean, mapping = anonymize_with_mapping("Jessica Whitfield called.", detectors)
print(restore(clean, mapping))

shared = Anonymizer(detectors)       # one set of placeholders across many texts
print(shared.anonymize("Ana called."), shared.anonymize("Ana called again."))
```

## Everything else

| Module | Entry points | Page |
|---|---|---|
| `datanyx.scrub` | `scrub`, `ScrubPolicy` | [Debugging](debugging.md) |
| `datanyx.check` | `load_tables`, `check_code`, `format_diagnostics` | [Checking code](checking-code.md) |
| `datanyx.pseudonymize` | `Pseudonymizer`, `load_key`, `pseudonymize_file`, `Options` | [Pseudonymizing tables](pseudonymization.md) |
| `datanyx.text` | `RegexDetector`, `DictionaryDetector`, `load_detector_file` for your own detectors | [Detection quality](detection.md) |
| `datanyx.mcp_server` | `create_server`, `run` | [Using it with an agent](agents.md#as-an-mcp-server) |
