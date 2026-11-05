"""Text anonymization without an NER model, using a stand-in detector. Synthetic data only."""

import json
import re
import sys

import pytest

from datanyx import SourceError
from datanyx.cli import main
from datanyx.text import (
    PatternDetector,
    Span,
    SpacyDetector,
    anonymize,
    anonymize_with_mapping,
    restore,
    unknown_placeholders,
)
from datanyx.text.detectors import _CHUNK_CHARS, _split_chunks, recase_for_ner


class FakeNer:
    """Reports the given strings as entities, as an NER model would."""

    def __init__(self, **strings_by_kind):
        self.strings = [(kind, s) for kind, strings in strings_by_kind.items() for s in strings]

    def find(self, text):
        for kind, string in self.strings:
            for match in re.finditer(re.escape(string), text):
                yield Span(match.start(), match.end(), kind)


def patterns(text, **options):
    return anonymize(text, [PatternDetector()], **options)


@pytest.mark.parametrize(
    "value, placeholder",
    [
        ("jane.doe+x@mail.example.org", "EMAIL"),
        ("https://intranet.example/u/42?token=abc", "URL"),
        ("www.example.org/jane", "URL"),
        ("192.168.1.20", "IP_ADDRESS"),
        ("2001:db8::8a2e:370:7334", "IP_ADDRESS"),
        ("00:1A:2B:3C:4D:5E", "MAC_ADDRESS"),
        ("d290f1ee-6c54-4b01-90e6-d701748f0851", "ID"),
        ("4539 1488 0343 6467", "CREDIT_CARD"),
        ("4539148803436467", "CREDIT_CARD"),
        ("FR14 2004 1010 0505 0001 3M02 606", "IBAN"),
        ("GB82WEST12345698765432", "IBAN"),
        ("+1 503 555 0142", "PHONE"),
        ("06 12 34 56 78", "PHONE"),
        ("(415) 555-0199", "PHONE"),
        ("2024-01-02", "DATE"),
        ("04/17/1982", "DATE"),
        ("2024-05-06T10:15:30Z", "DATE"),
        ("3 mars 2021", "DATE"),
        ("1er janvier 2024", "DATE"),
        ("March 3rd, 2024", "DATE"),
        ("15 de mayo de 2020", "DATE"),
        ("3. Oktober 1990", "DATE"),
        ("$1,240.50", "MONEY"),
        ("€ 500", "MONEY"),
        ("1 240,50 €", "MONEY"),
        ("1500 EUR", "MONEY"),
        ("742 Maple Avenue", "ADDRESS"),
        ("18 rue des Lilas", "ADDRESS"),
        ("MRN-00482", "ID"),
        ("00482913", "ID"),
        ("AB1234", "ID"),
    ],
)
def test_fixed_formats_are_hidden(value, placeholder):
    output = patterns(f"before {value} after")
    assert value not in output
    assert f"<{placeholder}_1>" in output
    assert output.startswith("before ") and output.endswith(" after")


@pytest.mark.parametrize(
    "ordinary",
    ["The meeting ran long.", "Version 3 of COVID-19 guidance", "Top 10 items, 25% off", "see page 12"],
)
def test_ordinary_text_is_left_alone(ordinary):
    assert patterns(ordinary) == ordinary


def test_invalid_checksums_are_not_reported_as_cards_but_stay_hidden():
    output = patterns("ref 4539 1488 0343 6468 end")
    assert "4539" not in output and "CREDIT_CARD" not in output


def test_a_phone_number_stays_on_its_line():
    output = patterns("ip=203.0.113.42\n2024-05-06 next line\n555 010 9999\nend")
    assert output == "ip=<IP_ADDRESS_1>\n<DATE_1> next line\n<PHONE_1>\nend"


def test_part_of_a_known_name_stays_a_person_when_a_model_calls_it_something_else():
    ner = FakeNer(PERSON=["Mark Bell"], ORG=["Bell"])
    output = anonymize("Mark Bell left. Bell agreed.", [ner])
    assert output == "<PERSON_1> left. <PERSON_1> agreed."


def test_trailing_punctuation_stays_visible():
    assert patterns("Mail a@b.example, or see https://x.example/a.") == "Mail <EMAIL_1>, or see <URL_1>."


def test_same_value_gets_the_same_placeholder():
    output = patterns("a@b.example wrote to c@d.example, then a@b.example again")
    assert output == "<EMAIL_1> wrote to <EMAIL_2>, then <EMAIL_1> again"


def test_a_name_found_once_is_hidden_everywhere():
    ner = FakeNer(PERSON=["Jane Doe"])
    # Stands in for a model that only recognised the first mention.
    ner.find = lambda text: [Span(0, 8, "PERSON")]
    output = anonymize("Jane Doe called. JANE DOE and jane  doe agreed.", [ner])
    assert output == "<PERSON_1> called. <PERSON_1> and <PERSON_1> agreed."


def test_parts_of_a_name_share_its_placeholder():
    ner = FakeNer(PERSON=["Jane Doe"])
    output = anonymize("Jane Doe called. Ms. Doe left, then Jane rang.", [ner])
    assert output == "<PERSON_1> called. Ms. <PERSON_1> left, then <PERSON_1> rang."


def test_a_shared_surname_is_hidden_without_linking_the_two_people():
    ner = FakeNer(PERSON=["Jane Doe", "John Doe"])
    output = anonymize("Jane Doe met John Doe. Doe spoke.", [ner])
    assert "Doe" not in output
    first, second, third = re.findall(r"<PERSON_\d+>", output)
    assert len({first, second, third}) == 3


def test_ordinary_words_matching_a_name_are_kept():
    ner = FakeNer(PERSON=["Will Turner", "May"])
    output = anonymize("Will Turner will agree. May may come. Will left.", [ner])
    assert "will agree" in output and "may come" in output
    assert "Turner" not in output and "Will" not in output and "May" not in output


def test_name_particles_are_not_hidden_elsewhere():
    ner = FakeNer(PERSON=["Charles de Gaulle"])
    output = anonymize("Charles de Gaulle est parti de Paris. Gaulle aussi.", [ner])
    assert output == "<PERSON_1> est parti de Paris. <PERSON_1> aussi."


def test_titles_are_not_treated_as_name_parts():
    ner = FakeNer(PERSON=["Dr. Jane Doe"])
    assert anonymize("Dr. Jane Doe and Dr. Lee", [ner]) == "<PERSON_1> and Dr. Lee"


def test_overlapping_spans_leave_no_fragment():
    ner = FakeNer(ORG=["Acme Corp mail"], EMAIL=["mail bob@x.example"])
    output = anonymize("Acme Corp mail bob@x.example today", [ner])
    assert output.endswith(" today") and output.count("<") == 1
    for fragment in ("Acme", "mail", "bob"):
        assert fragment not in output


def test_hide_terms_ignore_case_and_whitespace():
    assert patterns("Project  BLUEBIRD and project\nbluebird", hide_terms=["Project Bluebird"]) == (
        "<TERM_1> and <TERM_1>"
    )


def test_hide_terms_match_whole_words():
    assert patterns("Ann announced", hide_terms=["Ann"]) == "<TERM_1> announced"


def test_blank_terms_are_ignored():
    assert patterns("unchanged", hide_terms=["", "  "]) == "unchanged"


def test_keep_terms_stay_visible():
    ner = FakeNer(ORG=["Paris", "Acme"])
    assert anonymize("Paris and Acme", [ner], keep_terms=["Paris"]) == "Paris and <ORG_1>"


def test_keep_kinds_stay_visible():
    output = patterns("On 2024-01-02 a@b.example wrote", keep_kinds=["DATE"])
    assert output == "On 2024-01-02 <EMAIL_1> wrote"


def test_blank_entity_from_a_model_is_harmless():
    class Blank:
        def find(self, _text):
            yield Span(1, 2, "MISC")

    assert anonymize("a b", [Blank()]) == "a<MISC_1>b"


def test_line_endings_and_spacing_are_preserved():
    assert patterns("a\r\n\r\n  b\t c\n") == "a\r\n\r\n  b\t c\n"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Contact JANE DOE today", "Contact Jane Doe today"),
        ("did you see jane doe", "Did You See Jane Doe"),
        ("HEADER\nA normal line\nlower line\n", "Header\nA normal line\nLower Line\n"),
        ("A normal Sentence stays.", "A normal Sentence stays."),
        ("MIN-JUN", "Min-Jun"),
        ("12345 --", "12345 --"),
    ],
)
def test_recasing_for_ner(text, expected):
    assert recase_for_ner(text) == expected


@pytest.mark.parametrize("text", ["STRASSE ß straße", "ŉ ǰ ﬁ", "İSTANBUL ıi", "ÉLODIE\r\nélodie\n"])
def test_recasing_never_changes_offsets(text):
    assert len(recase_for_ner(text)) == len(text)


def test_chunks_cover_all_text_and_split_on_whitespace():
    text = ("word " * (_CHUNK_CHARS // 2)) + "end"
    chunks = list(_split_chunks(text))
    assert len(chunks) > 1
    assert "".join(chunk for _, chunk in chunks) == text
    assert all(text[offset:offset + len(chunk)] == chunk for offset, chunk in chunks)
    assert all(chunk.endswith(" ") for _, chunk in chunks[:-1])


def test_unbroken_text_still_splits():
    text = "x" * (_CHUNK_CHARS * 2 + 5)
    assert "".join(chunk for _, chunk in _split_chunks(text)) == text


def test_missing_spacy_is_explained_rather_than_falling_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "spacy", None)
    with pytest.raises(SourceError, match=r"datanyx\[text\]"):
        SpacyDetector()


def test_cli_produces_nothing_when_the_model_is_missing(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "spacy", None)
    path = tmp_path / "a.txt"
    path.write_text("Jane Doe, jane@x.example", encoding="utf-8")
    assert main(["anonymize", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Jane" not in captured.err and "jane" not in captured.err


@pytest.fixture
def fake_model(monkeypatch):
    ner = FakeNer(PERSON=["Jane Doe"])
    monkeypatch.setattr("datanyx.cli.SpacyDetector", lambda model: ner)


def test_cli_writes_to_stdout(tmp_path, capsys, fake_model):
    path = tmp_path / "a.txt"
    path.write_bytes(b"Jane Doe: jane@x.example\n")
    assert main(["anonymize", str(path)]) == 0
    assert capsys.readouterr().out == "<PERSON_1>: <EMAIL_1>\n"


def test_cli_writes_to_a_file(tmp_path, capsys, fake_model):
    path = tmp_path / "a.txt"
    path.write_bytes(b"\xef\xbb\xbfJane Doe\r\n")
    target = tmp_path / "b.txt"
    assert main(["anonymize", str(path), "-o", str(target)]) == 0
    assert target.read_bytes() == b"<PERSON_1>\r\n"
    assert capsys.readouterr().out == ""


def test_cli_options(tmp_path, capsys, fake_model):
    path = tmp_path / "a.txt"
    path.write_text("Jane Doe, Acme, 2024-01-02", encoding="utf-8")
    code = main(["anonymize", str(path), "--hide", "Acme", "--keep-entity", "DATE", "--keep", "Jane Doe"])
    assert code == 0
    assert capsys.readouterr().out == "Jane Doe, <TERM_1>, 2024-01-02"


def test_cli_refuses_to_overwrite_the_input(tmp_path, capsys, fake_model):
    path = tmp_path / "a.txt"
    path.write_text("Jane Doe", encoding="utf-8")
    assert main(["anonymize", str(path), "-o", str(path)]) == 1
    assert "different files" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "Jane Doe"


@pytest.mark.parametrize("data", [b"caf\xe9 CANARY", b"\xff\xfeC\x00"])
def test_cli_encoding_errors_do_not_quote_the_input(tmp_path, capsys, fake_model, data):
    path = tmp_path / "a.txt"
    path.write_bytes(data)
    assert main(["anonymize", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CANARY" not in captured.err and "xe9" not in captured.err


def test_cli_failures_report_only_the_error_type(tmp_path, capsys, monkeypatch):
    class Exploding:
        def find(self, _text):
            raise ValueError("CANARY quoted in an exception")

    monkeypatch.setattr("datanyx.cli.SpacyDetector", lambda model: Exploding())
    path = tmp_path / "a.txt"
    path.write_text("text", encoding="utf-8")
    assert main(["anonymize", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ValueError" in captured.err and "CANARY" not in captured.err


def test_mapping_holds_each_placeholders_real_value():
    ner = FakeNer(PERSON=["Jane Doe"])
    output, mapping = anonymize_with_mapping("Jane Doe, jane@x.example. Ms. Doe left.", [PatternDetector(), ner])
    assert output == "<PERSON_1>, <EMAIL_1>. Ms. <PERSON_1> left."
    assert mapping == {"<PERSON_1>": "Jane Doe", "<EMAIL_1>": "jane@x.example"}


def test_restore_round_trip():
    ner = FakeNer(PERSON=["Jane Doe"])
    text = "Jane Doe wrote from jane@x.example on 2024-01-02.\n"
    output, mapping = anonymize_with_mapping(text, [PatternDetector(), ner])
    assert restore(output, mapping) == text


def test_restore_works_on_text_an_agent_rewrote():
    mapping = {"<PERSON_1>": "Jane Doe", "<ORG_1>": "Acme"}
    edited = "Summary: <PERSON_1> left <ORG_1>. <PERSON_1>'s manager agreed."
    assert restore(edited, mapping) == "Summary: Jane Doe left Acme. Jane Doe's manager agreed."


def test_restore_leaves_unknown_placeholders_and_reports_them():
    text = "<PERSON_1> met <PERSON_9>"
    mapping = {"<PERSON_1>": "Jane"}
    assert restore(text, mapping) == "Jane met <PERSON_9>"
    assert unknown_placeholders(text, mapping) == ["<PERSON_9>"]


def test_restore_does_not_expand_placeholders_inside_values():
    assert restore("<TERM_1>", {"<TERM_1>": "<TERM_2>", "<TERM_2>": "x"}) == "<TERM_2>"


def test_placeholder_numbers_avoid_ones_already_in_the_text():
    text = "Literal <EMAIL_1> and a@b.example"
    output, mapping = anonymize_with_mapping(text, [PatternDetector()])
    assert output == "Literal <EMAIL_1> and <EMAIL_2>"
    assert "<EMAIL_1>" not in mapping
    assert restore(output, mapping) == text


def write_input(tmp_path, data=b"Jane Doe, jane@x.example\n"):
    path = tmp_path / "in.txt"
    path.write_bytes(data)
    return str(path)


def test_cli_saves_a_mapping_and_restores_from_it(tmp_path, capsys, fake_model):
    source = write_input(tmp_path)
    clean = tmp_path / "clean.txt"
    saved = tmp_path / "a.mapping.json"
    back = tmp_path / "back.txt"
    assert main(["anonymize", source, "-o", str(clean), "--mapping", str(saved)]) == 0
    assert json.loads(saved.read_text(encoding="utf-8")) == {
        "<PERSON_1>": "Jane Doe",
        "<EMAIL_1>": "jane@x.example",
    }
    assert main(["restore", str(clean), "--mapping", str(saved), "-o", str(back)]) == 0
    assert back.read_bytes() == b"Jane Doe, jane@x.example\n"
    captured = capsys.readouterr()
    assert "Jane" not in captured.out + captured.err


def test_cli_writes_no_mapping_unless_asked(tmp_path, fake_model):
    source = write_input(tmp_path)
    before = set(tmp_path.iterdir())
    assert main(["anonymize", source, "-o", str(tmp_path / "clean.txt")]) == 0
    assert set(tmp_path.iterdir()) - before == {tmp_path / "clean.txt"}


def test_cli_refuses_to_replace_an_existing_mapping(tmp_path, capsys, fake_model):
    saved = tmp_path / "a.mapping.json"
    saved.write_text('{"<PERSON_1>": "Earlier"}', encoding="utf-8")
    clean = tmp_path / "clean.txt"
    assert main(["anonymize", write_input(tmp_path), "-o", str(clean), "--mapping", str(saved)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert json.loads(saved.read_text(encoding="utf-8")) == {"<PERSON_1>": "Earlier"}
    assert not clean.exists()


def test_cli_refuses_a_mapping_that_is_the_input_or_output(tmp_path, capsys, fake_model):
    source = write_input(tmp_path)
    assert main(["anonymize", source, "--mapping", source]) == 1
    assert (tmp_path / "in.txt").read_bytes().startswith(b"Jane")
    capsys.readouterr()


def test_restore_requires_an_output_file(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["restore", write_input(tmp_path), "--mapping", "m.json"])
    assert "Jane" not in capsys.readouterr().out


def test_restore_reports_unknown_placeholders_without_real_values(tmp_path, capsys):
    source = write_input(tmp_path, b"<PERSON_1> and <PERSON_2>")
    saved = tmp_path / "m.json"
    saved.write_text('{"<PERSON_1>": "CANARY"}', encoding="utf-8")
    assert main(["restore", source, "--mapping", str(saved), "-o", str(tmp_path / "out.txt")]) == 0
    captured = capsys.readouterr()
    assert "<PERSON_2>" in captured.err
    assert "CANARY" not in captured.out + captured.err


@pytest.mark.parametrize("content", ['{"<PERSON_1>": CANARY', '["CANARY"]', '{"<PERSON_1>": 5}'])
def test_bad_mapping_errors_do_not_quote_it(tmp_path, capsys, content):
    source = write_input(tmp_path, b"<PERSON_1>")
    saved = tmp_path / "m.json"
    saved.write_text(content, encoding="utf-8")
    target = tmp_path / "out.txt"
    assert main(["restore", source, "--mapping", str(saved), "-o", str(target)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: the mapping file")
    assert "CANARY" not in captured.out + captured.err
    assert not target.exists()
