"""Generated data with a canary planted in every value. No output may contain it.

Keys are only given the canary when their shape means they must be hidden (a space,
an "@"), since a plain-looking key is shown by design.
"""

import csv
import io
import json
from xml.sax.saxutils import escape, quoteattr

import pytest

pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from datanyx import KeyPolicy, extract, render, synthesize
from datanyx.renderers import RENDERERS
from datanyx.synth import render_table

CANARY = "zqcanaryvalue"
FUZZ = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])

TAILS = st.text(alphabet="abc XYZ019-_.@", max_size=8)
PLAIN_KEYS = st.sampled_from(["id", "name", "email", "total", "items", "created_at", "status"])
HIDDEN_KEYS = TAILS.flatmap(
    lambda t: st.sampled_from([f"{CANARY} {t}", f"{CANARY}@{t.strip() or 'x'}.com", f"{CANARY}:{t} x"])
)
KEYS = st.one_of(PLAIN_KEYS, HIDDEN_KEYS)
STRING_VALUES = TAILS.map(lambda t: CANARY + t)
SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**12), max_value=10**12),
    st.floats(allow_nan=False, allow_infinity=False),
    STRING_VALUES,
)
DOCUMENTS = st.recursive(
    SCALARS,
    lambda inner: st.one_of(st.lists(inner, max_size=4), st.dictionaries(KEYS, inner, max_size=5)),
    max_leaves=25,
)


def leaked(path, **options) -> bool:
    policy = KeyPolicy(**options)
    schema = extract(str(path), policy=policy)
    outputs = [render(schema, name) for name in RENDERERS]
    outputs.extend(schema.notes)
    return CANARY in "\n".join(outputs)


@FUZZ
@given(DOCUMENTS)
def test_json_never_leaks(tmp_path_factory, document):
    path = tmp_path_factory.mktemp("j") / "d.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert not leaked(path)


@FUZZ
@given(st.lists(DOCUMENTS, max_size=8))
def test_jsonl_never_leaks(tmp_path_factory, documents):
    path = tmp_path_factory.mktemp("l") / "d.jsonl"
    path.write_text("\n".join(json.dumps(d) for d in documents), encoding="utf-8")
    assert not leaked(path)


@FUZZ
@given(st.lists(KEYS, min_size=1, max_size=5), st.lists(st.lists(STRING_VALUES, max_size=6), max_size=8))
def test_csv_never_leaks(tmp_path_factory, header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    path = tmp_path_factory.mktemp("c") / "d.csv"
    path.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    assert not leaked(path)


@FUZZ
@given(DOCUMENTS)
def test_yaml_never_leaks(tmp_path_factory, document):
    yaml = pytest.importorskip("yaml")
    path = tmp_path_factory.mktemp("y") / "d.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert not leaked(path)


XML_NODES = st.recursive(
    STRING_VALUES.map(escape),
    lambda inner: st.lists(st.tuples(PLAIN_KEYS, STRING_VALUES, inner), max_size=4).map(
        lambda children: "".join(f"<{tag} a={quoteattr(attr)}>{body}</{tag}>" for tag, attr, body in children)
    ),
    max_leaves=15,
)


@FUZZ
@given(XML_NODES)
def test_xml_never_leaks(tmp_path_factory, body):
    path = tmp_path_factory.mktemp("x") / "d.xml"
    path.write_text(f"<root>{body}</root>", encoding="utf-8")
    assert not leaked(path)


@FUZZ
@given(DOCUMENTS)
def test_synthetic_data_never_leaks(tmp_path_factory, document):
    path = tmp_path_factory.mktemp("s") / "d.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    for table in synthesize(extract(str(path)), rows=20):
        for output_format in ("json", "jsonl", "csv"):
            assert CANARY not in render_table(table, output_format)
