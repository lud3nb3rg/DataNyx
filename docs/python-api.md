# Python API

```python
import datanyx

print(datanyx.schema_of("orders.csv", "typescript"))
print(datanyx.anonymize_text("Jessica Whitfield called.", hide=["Acme"]))
```

For more control, work with the schema as objects:

```python
from datanyx import KeyPolicy, extract, render

policy = KeyPolicy(map_paths=frozenset({"$.users"}), keep_names=frozenset({"report_2024"}))

schema = extract("customers.json", policy=policy)
print(render(schema, "text"))

schema = extract("env:DATABASE_URL", namespace="sales")  # namespace is --schema
print(render(schema))
```

```python
from datanyx.text import PatternDetector, SpacyDetector, anonymize, anonymize_with_mapping, restore

detectors = [PatternDetector(), SpacyDetector("en_core_web_lg")]
print(anonymize("Jessica Whitfield called.", detectors, hide_terms=["Acme"]))

clean, mapping = anonymize_with_mapping("Jessica Whitfield called.", detectors)
print(restore(clean, mapping))
```

Failures raise `SourceError`, with a message that's safe to show.
