"""`--delimiter` lets CSV and TSV input use another separator."""

import argparse

import pytest

from datanyx import extract
from datanyx.cli import main
from datanyx.cli.common import _delimiter_arg
from datanyx.privacy import KeyPolicy


def test_semicolon_csv_has_its_columns(tmp_path):
    path = tmp_path / "eu.csv"
    path.write_text("id;name;total\n1;a;2,5\n2;b;3,5\n", encoding="utf-8")
    fields = extract(str(path), policy=KeyPolicy(delimiter=";")).entities[0].node.items.fields
    assert list(fields) == ["id", "name", "total"]


def test_cli_accepts_a_name_or_a_character(tmp_path, capsys):
    path = tmp_path / "pipes.txt"
    path.write_text("id|name\n1|a\n2|b\n", encoding="utf-8")
    assert main(["schema", str(path), "--source", "csv", "--delimiter", "pipe", "--format", "text"]) == 0
    assert "name: string" in capsys.readouterr().out


@pytest.mark.parametrize("value, expected", [(";", ";"), ("tab", "\t"), ("Semicolon", ";")])
def test_delimiter_names(value, expected):
    assert _delimiter_arg(value) == expected


@pytest.mark.parametrize("value", ["", "ab", '"', "\n"])
def test_bad_delimiters_are_refused(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _delimiter_arg(value)


def test_decimals_are_not_phone_numbers():
    from datanyx.text.detectors import _is_phone

    assert not _is_phone("1234.50000")
    assert _is_phone("415.555.2671") and _is_phone("+44 20 7946 0958")
