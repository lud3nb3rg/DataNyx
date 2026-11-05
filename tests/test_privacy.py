"""The core promise: nothing from the data reaches any output channel.

All data here is synthetic. Sensitive strings contain CANARY so a leak of any
fragment is caught by searching the output for it.
"""

import json
import random

import pytest

from datanyx import KeyPolicy, extract, render
from datanyx.cli import main
from datanyx.privacy import looks_like_data
from datanyx.renderers import RENDERERS

CANARY = "CANARY"


def write(tmp_path, data, name="data.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def all_outputs(path, policy=None):
    schema = extract(path, policy=policy)
    outputs = [render(schema, name) for name in RENDERERS]
    outputs.extend(schema.notes)
    return "\n".join(outputs)


def run_cli(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_string_values_never_appear(tmp_path):
    data = {
        "name": f"{CANARY} Person",
        "email": f"{CANARY}@example.com",
        "nested": {"notes": [f"{CANARY} note", {"secret": CANARY}]},
    }
    assert CANARY not in all_outputs(write(tmp_path, data))


def test_numeric_values_never_appear(tmp_path):
    data = {"salary": 918273.5, "age": 7654321, "items": [111222333, 444555666]}
    output = all_outputs(write(tmp_path, data))
    for fragment in ("918273", "7654321", "111222333", "444555666"):
        assert fragment not in output


def test_record_count_and_low_cardinality_values_are_not_revealed(tmp_path):
    # Values repeat, which an enum-inferring tool would surface.
    data = [{"diagnosis": "CANARY_A"} for _ in range(137)] + [{"diagnosis": "CANARY_B"}]
    output = all_outputs(write(tmp_path, data))
    assert CANARY not in output
    assert "137" not in output and "138" not in output


def test_filename_is_not_revealed(tmp_path):
    path = write(tmp_path, {"a": 1}, name=f"{CANARY}_medical.json")
    assert CANARY not in all_outputs(path)


@pytest.mark.parametrize(
    "key",
    [
        "CANARY@example.com",
        "CANARY Person",
        "Person, CANARY",
        "line\nCANARY",
        "https://example.com/CANARY",
        "2024-01-02T03:04:05",
        "d290f1ee-6c54-4b01-90e6-d701748f0851",
        "+1 (555) 010-9999",
        "192.168.1.20",
        "424242",
        "cust_00918273",
        "a1b2c3d4e5f6a7b8c9d0e1f2",
        "x" * 65,
    ],
)
def test_data_shaped_keys_are_hidden(tmp_path, key):
    output = all_outputs(write(tmp_path, {"id": "v", key: {"balance": 1}}))
    assert key not in output
    assert json.dumps(key)[1:-1] not in output
    assert "balance" in output  # The value's structure is still described.


@pytest.mark.parametrize(
    "key",
    ["id", "first_name", "lastName", "address2", "created-at", "$ref", "Name", "x.y"],
)
def test_ordinary_field_names_are_kept(key):
    assert not looks_like_data(key)


def test_plain_names_beside_data_keys_are_hidden(tmp_path):
    data = {"CANARY@example.com": {"age": 1}, "CANARYbob": {"age": 2}}
    assert CANARY not in all_outputs(write(tmp_path, data))


def test_keys_that_vary_per_record_are_hidden(tmp_path):
    # Names shaped like ordinary fields; only their failure to recur gives them away.
    data = [{"prefs": {f"CANARY{chr(97 + i)}": True}} for i in range(10)]
    assert CANARY not in all_outputs(write(tmp_path, data))


def test_keys_repeating_a_few_times_among_per_record_keys_are_hidden(tmp_path):
    data = [{"prefs": {f"CANARY{chr(97 + i)}": True}} for i in range(10)]
    data[1]["prefs"]["CANARYa"] = True  # Seen twice, so it clears the support floor.
    assert CANARY not in all_outputs(write(tmp_path, data))


def test_large_map_of_same_shaped_objects_is_hidden(tmp_path):
    data = {f"CANARY{chr(97 + i)}": {"age": i, "city": "x"} for i in range(8)}
    assert CANARY not in all_outputs(write(tmp_path, data))


def test_large_map_of_scalars_is_hidden(tmp_path):
    data = {f"CANARY{chr(97 + a)}{chr(97 + b)}": 1 for a in range(6) for b in range(6)}
    assert CANARY not in all_outputs(write(tmp_path, data))


def test_configured_map_path_hides_undetectable_keys(tmp_path):
    # Three plain names is the known blind spot; --map-path is the remedy.
    data = {"users": {"CANARYalice": {"age": 1}, "CANARYbob": {"age": 2}}}
    path = write(tmp_path, data)
    assert CANARY in all_outputs(path)
    assert CANARY not in all_outputs(path, KeyPolicy(map_paths=frozenset({"$.users"})))


def test_randomised_documents_never_leak(tmp_path):
    rng = random.Random(1234)

    def scalar():
        return rng.choice(
            [f"{CANARY} {rng.random()}", f"{CANARY}@example.com", f"https://{CANARY}.example.com"]
        )

    def value(depth):
        if depth == 0 or rng.random() < 0.3:
            return scalar()
        if rng.random() < 0.5:
            return [value(depth - 1) for _ in range(rng.randint(0, 3))]
        keys = ["alpha", "beta", "gamma", f"{CANARY}@x.org", f"{CANARY} key"]
        return {key: value(depth - 1) for key in rng.sample(keys, rng.randint(1, 5))}

    for index in range(50):
        document = {"root": value(5)}
        path = write(tmp_path, document, name=f"doc{index}.json")
        assert CANARY not in all_outputs(path), json.dumps(document)


def test_cli_success_output_has_no_data(tmp_path, capsys):
    data = {"CANARY@example.com": {"secret": CANARY}, "name": CANARY}
    code, output = run_cli(["schema", write(tmp_path, data)], capsys)
    assert code == 0
    assert CANARY not in output


def test_parse_error_does_not_quote_the_document(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text('{"secret": "CANARY" "next": 1}', encoding="utf-8")
    code, output = run_cli(["schema", str(path)], capsys)
    assert code == 1
    assert "line 1" in output
    assert CANARY not in output


def test_jsonl_parse_error_does_not_quote_the_line(tmp_path, capsys):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"a": 1}\n{"secret": CANARY}\n', encoding="utf-8")
    code, output = run_cli(["schema", str(path)], capsys)
    assert code == 1
    assert "line 2" in output
    assert CANARY not in output


def test_encoding_error_does_not_quote_bytes(tmp_path, capsys):
    path = tmp_path / "latin.json"
    path.write_bytes('{"name": "CANARY caf\xe9"}'.encode("latin-1"))
    code, output = run_cli(["schema", str(path)], capsys)
    assert code == 1
    assert CANARY not in output and "xe9" not in output


def test_unexpected_failure_reports_only_the_error_type(tmp_path, capsys, monkeypatch):
    def explode(*_args, **_kwargs):
        raise ValueError(f"{CANARY} leaked into an exception message")

    monkeypatch.setattr("datanyx.cli.extract", explode)
    code, output = run_cli(["schema", write(tmp_path, {"a": 1})], capsys)
    assert code == 1
    assert "ValueError" in output
    assert CANARY not in output
