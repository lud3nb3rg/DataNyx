"""The schema must stay accurate enough for an agent to write working code from it."""

import json

import pytest

from datanyx import SourceError, extract, render
from datanyx.cli import main
from datanyx.formats import detect_format


def schema_for(tmp_path, data, name="data.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return json.loads(render(extract(str(path))))


def test_scalar_types(tmp_path):
    schema = schema_for(tmp_path, {"s": "x", "i": 1, "f": 1.5, "b": True, "n": None})
    assert schema["properties"] == {
        "s": {"type": "string"},
        "i": {"type": "integer"},
        "f": {"type": "number"},
        "b": {"type": "boolean"},
        "n": {"type": "null"},
    }


def test_array_of_records_merges_required_and_optional(tmp_path):
    schema = schema_for(tmp_path, [{"id": 1, "nick": "a"}, {"id": 2}])
    assert schema["type"] == "array"
    assert schema["items"]["required"] == ["id"]
    assert set(schema["items"]["properties"]) == {"id", "nick"}


def test_nullable_and_mixed_types(tmp_path):
    schema = schema_for(tmp_path, [{"a": "x", "b": 1}, {"a": None, "b": "y"}])
    props = schema["items"]["properties"]
    assert props["a"] == {"type": ["string", "null"]}
    assert props["b"] == {"type": ["string", "integer"]}


def test_integers_and_floats_widen_to_number(tmp_path):
    schema = schema_for(tmp_path, [{"a": 1}, {"a": 1.5}])
    assert schema["items"]["properties"]["a"] == {"type": "number"}


def test_empty_array_has_no_item_schema(tmp_path):
    assert schema_for(tmp_path, {"a": []})["properties"]["a"] == {"type": "array"}


def test_format_kept_only_when_every_value_matches(tmp_path):
    schema = schema_for(
        tmp_path,
        [{"good": "a@example.com", "bad": "a@example.com"}, {"good": "b@example.com", "bad": "nope"}],
    )
    props = schema["items"]["properties"]
    assert props["good"] == {"type": "string", "format": "email"}
    assert props["bad"] == {"type": "string"}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a@example.com", "email"),
        ("d290f1ee-6c54-4b01-90e6-d701748f0851", "uuid"),
        ("https://example.com/x", "uri"),
        ("2024-02-29", "date"),
        ("2024-02-30", None),
        ("2024-01-02T03:04:05Z", "date-time"),
        ("192.168.0.1", "ipv4"),
        ("::1", "ipv6"),
        ("plain words", None),
        ("", None),
    ],
)
def test_detect_format(text, expected):
    assert detect_format(text) == expected


def test_map_keeps_value_schema_and_key_format(tmp_path):
    data = {
        "d290f1ee-6c54-4b01-90e6-d701748f0851": {"age": 1},
        "e290f1ee-6c54-4b01-90e6-d701748f0852": {"age": 2},
    }
    schema = schema_for(tmp_path, data)
    assert "properties" not in schema
    assert schema["propertyNames"] == {"format": "uuid"}
    assert schema["additionalProperties"]["properties"] == {"age": {"type": "integer"}}


def test_record_with_map_field_keeps_named_fields(tmp_path):
    data = {
        "id": 1,
        "name": "x",
        "active": True,
        "history": {"2024-01-01": 5, "2024-01-02": 6},
    }
    schema = schema_for(tmp_path, data)
    assert set(schema["properties"]) == {"id", "name", "active", "history"}
    assert schema["properties"]["history"]["propertyNames"] == {"format": "date"}


def test_mixed_object_does_not_constrain_named_keys_by_key_format(tmp_path):
    schema = schema_for(tmp_path, {"id": "x", "2024-01-01": 5, "2024-01-02": 6})
    assert set(schema["properties"]) == {"id"}
    assert schema["additionalProperties"] == {"type": "integer"}
    assert "propertyNames" not in schema


@pytest.mark.parametrize("count", [8, 40])
def test_flat_record_with_recurring_keys_stays_a_record(tmp_path, count):
    records = [{f"field_{chr(97 + i % 26)}{i}": "x" for i in range(count)} for _ in range(3)]
    schema = schema_for(tmp_path, records)
    assert len(schema["items"]["properties"]) == count


def test_single_record_with_many_string_fields_stays_a_record(tmp_path):
    record = {f"field_{chr(97 + i)}": "x" for i in range(20)}
    assert len(schema_for(tmp_path, record)["properties"]) == 20


def test_differently_shaped_sections_stay_a_record(tmp_path):
    config = {f"section_{chr(97 + i)}": {f"option_{chr(97 + i)}": 1} for i in range(10)}
    assert len(schema_for(tmp_path, config)["properties"]) == 10


def test_few_same_shaped_objects_stay_named(tmp_path):
    data = {"billing": {"city": "x"}, "shipping": {"city": "y"}}
    assert set(schema_for(tmp_path, data)["properties"]) == {"billing", "shipping"}


def test_common_field_absent_from_some_records_stays_named(tmp_path):
    records = [{"id": i, "note": "x"} if i % 2 else {"id": i} for i in range(20)]
    assert set(schema_for(tmp_path, records)["items"]["properties"]) == {"id", "note"}


def test_jsonl_is_an_array_of_records(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2, "b": "x"}\n', encoding="utf-8")
    schema = json.loads(render(extract(str(path))))
    assert schema["type"] == "array"
    assert schema["items"]["required"] == ["a"]


def test_utf8_bom_is_accepted(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b'\xef\xbb\xbf{"a": 1}')
    assert json.loads(render(extract(str(path))))["properties"]["a"] == {"type": "integer"}


def test_text_format(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(
        json.dumps([{"id": 1, "email": "a@example.com", "tags": ["x"], "nick": None}, {"id": 2, "email": "b@example.com", "tags": []}]),
        encoding="utf-8",
    )
    assert render(extract(str(path)), "text") == (
        "root: array<{\n"
        "  id: integer\n"
        "  email: string<email>\n"
        "  tags: array<string>\n"
        "  nick?: null\n"
        "}>"
    )


def test_missing_file_is_a_source_error(tmp_path):
    with pytest.raises(SourceError):
        extract(str(tmp_path / "missing.json"))


def test_unknown_extension_asks_for_source(tmp_path):
    with pytest.raises(SourceError, match="--source"):
        extract(str(tmp_path / "data.bin"))


def test_explicit_source_overrides_extension(tmp_path):
    path = tmp_path / "data.bin"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert json.loads(render(extract(str(path), source="json")))["properties"]["a"]


def test_cli_writes_output_file(tmp_path, capsys):
    source = tmp_path / "data.json"
    source.write_text('{"a": 1}', encoding="utf-8")
    target = tmp_path / "schema.json"
    assert main(["schema", str(source), "-o", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["properties"]["a"]
    assert capsys.readouterr().out == ""


def test_deep_nesting_fails_cleanly(tmp_path, capsys):
    path = tmp_path / "deep.json"
    path.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    assert main(["schema", str(path)]) == 1
    assert "too deeply nested" in capsys.readouterr().err
