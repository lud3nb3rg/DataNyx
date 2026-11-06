"""Compressed files are described exactly as their plain originals. All data is synthetic."""

import bz2
import gzip
import json
import lzma

import pytest

from datanyx import KeyPolicy, SourceError, extract, render
from datanyx.cli import main

CANARY = "zqcanaryvalue"


def zstd(data: bytes) -> bytes:
    zstandard = pytest.importorskip("zstandard")
    return zstandard.ZstdCompressor().compress(data)


CODECS = {".gz": gzip.compress, ".bz2": bz2.compress, ".xz": lzma.compress, ".zst": zstd}
PLAIN = {
    "a.csv": b"id,name,email\n" + b"".join(f"{i},n{i},a{i}@example.com\n".encode() for i in range(1, 30)),
    "a.tsv": b"id\tv\n1\tx\n2\ty\n",
    "a.json": json.dumps([{"id": i, "tags": ["a"], "when": "2024-01-02"} for i in range(5)]).encode(),
    "a.jsonl": b"\n".join(json.dumps({"id": i, "ok": True}).encode() for i in range(5)),
    "a.xml": b'<root a="1"><row id="2">text</row></root>',
    "a.hl7": b"MSH|^~\\&|APP|FAC|||20200101||ADT^A01|1|P|2.5\rPID|1||123||Doe^Jane||19700101|M\r",
    "a.fhir.json": json.dumps({"resourceType": "Patient", "id": "1", "birthDate": "1970-01-01"}).encode(),
}


def schema_text(path, **options):
    schema = extract(str(path), policy=KeyPolicy(**options))
    return render(schema, "json-schema"), schema.notes


@pytest.mark.parametrize("suffix", CODECS)
def test_every_text_format_matches_its_plain_original(tmp_path, suffix):
    for name, data in PLAIN.items():
        plain = tmp_path / name
        plain.write_bytes(data)
        packed = tmp_path / (name + suffix)
        packed.write_bytes(CODECS[suffix](data))
        assert schema_text(packed) == schema_text(plain), name


def test_yaml_matches(tmp_path):
    pytest.importorskip("yaml")
    data = b"id: 1\nitems:\n  - k: v\n"
    (tmp_path / "a.yaml").write_bytes(data)
    (tmp_path / "a.yaml.gz").write_bytes(gzip.compress(data))
    assert schema_text(tmp_path / "a.yaml.gz") == schema_text(tmp_path / "a.yaml")


@pytest.mark.parametrize("suffix", CODECS)
def test_a_byte_order_mark_is_skipped_even_where_a_stream_cant_rewind(tmp_path, suffix):
    data = b"\xef\xbb\xbf" + json.dumps([{"id": 1}, {"id": 2}]).encode()
    (tmp_path / "bom.json").write_bytes(data)
    (tmp_path / ("bom.json" + suffix)).write_bytes(CODECS[suffix](data))
    assert schema_text(tmp_path / ("bom.json" + suffix)) == schema_text(tmp_path / "bom.json")


def test_hints_and_key_inference_work_through_compression(tmp_path):
    rows = b"id,name\n" + b"".join(f"{i},n{i}\n".encode() for i in range(1, 40))
    (tmp_path / "k.csv").write_bytes(rows)
    (tmp_path / "k.csv.gz").write_bytes(gzip.compress(rows))
    options = {"scale_hints": True, "infer_keys": True}
    assert schema_text(tmp_path / "k.csv.gz", **options) == schema_text(tmp_path / "k.csv", **options)


def test_a_folder_names_the_file_as_it_is_called(tmp_path):
    (tmp_path / "people.csv.gz").write_bytes(gzip.compress(PLAIN["a.csv"]))
    (tmp_path / "plain.csv").write_bytes(PLAIN["a.csv"])
    names = [entity.name for entity in extract(str(tmp_path)).entities]
    assert names == ["people.csv.gz", "plain.csv"]


def test_a_compressed_file_at_a_url(tmp_path):
    fsspec = pytest.importorskip("fsspec")
    filesystem = fsspec.filesystem("memory")
    filesystem.pipe_file("/data/u.csv.gz", gzip.compress(PLAIN["a.csv"]))
    try:
        (tmp_path / "u.csv").write_bytes(PLAIN["a.csv"])
        assert schema_text("memory://data/u.csv.gz") == schema_text(tmp_path / "u.csv")
    finally:
        filesystem.rm("/data/u.csv.gz")


def test_damaged_data_gives_a_fixed_message(tmp_path, capsys):
    good = gzip.compress(PLAIN["a.csv"])
    (tmp_path / "cut.csv.gz").write_bytes(good[: len(good) // 2])
    (tmp_path / "junk.csv.gz").write_bytes(f"not compressed {CANARY}".encode())
    (tmp_path / "junk.jsonl.bz2").write_bytes(f"not compressed {CANARY}".encode())
    for name in ("cut.csv.gz", "junk.csv.gz", "junk.jsonl.bz2"):
        with pytest.raises(SourceError, match="isn't readable") as raised:
            extract(str(tmp_path / name))
        assert CANARY not in str(raised.value)
        assert main(["schema", str(tmp_path / name)]) == 1
        seen = capsys.readouterr()
        assert CANARY not in seen.out + seen.err


def test_binary_formats_must_be_decompressed_first(tmp_path):
    (tmp_path / "a.parquet.gz").write_bytes(gzip.compress(b"PAR1"))
    with pytest.raises(SourceError, match="can't be read compressed"):
        extract(str(tmp_path / "a.parquet.gz"))


def test_zstd_needs_its_package_and_says_so(tmp_path, monkeypatch):
    (tmp_path / "a.csv.zst").write_bytes(b"x")
    monkeypatch.setitem(__import__("sys").modules, "zstandard", None)
    with pytest.raises(SourceError, match=r"datanyx\[zstd\]"):
        extract(str(tmp_path / "a.csv.zst"))


def test_a_missing_compressed_file_is_reported_like_any_other(tmp_path):
    with pytest.raises(SourceError, match="cannot read input"):
        extract(str(tmp_path / "nothing.csv.gz"))
