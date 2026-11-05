"""Detectors described in a file: patterns and dictionaries for formats and names that are yours."""

import json

import pytest

from datanyx.cli import main
from datanyx.scan import scan
from datanyx.sources import SourceError
from datanyx.text import PatternDetector, anonymize, anonymize_with_mapping, restore
from datanyx.text.custom import DictionaryDetector, RegexDetector, build_detectors, kinds_of, load_detector_file

CANARY = "zqcanary-client"


def write(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def spans(detector, text):
    return [text[s.start : s.end] for s in detector.find(text)]


def load(tmp_path, text):
    return load_detector_file(write(tmp_path, "d.toml", text))


# --- Patterns ---------------------------------------------------------------------------------


def test_a_pattern_marks_what_the_regex_matches(tmp_path):
    (detector,) = load(tmp_path, '[[pattern]]\nkind = "EMPLOYEE_ID"\nregex = \'EMP-\\d{6}\'\n')
    assert isinstance(detector, RegexDetector)
    assert spans(detector, "EMP-123456 and emp-654321 and EMP-12") == ["EMP-123456"]


def test_flags_and_groups(tmp_path):
    insensitive, grouped = load(
        tmp_path,
        '[[pattern]]\nkind = "A"\nregex = \'emp-\\d{6}\'\nflags = "i"\n'
        '[[pattern]]\nkind = "B"\nregex = \'ticket (\\d+)\'\ngroup = 1\n',
    )
    assert spans(insensitive, "EMP-123456") == ["EMP-123456"]
    assert spans(grouped, "see ticket 4521 now") == ["4521"]  # Only the number is marked.


def test_a_pattern_that_matches_nothing_in_particular_yields_no_empty_spans(tmp_path):
    (detector,) = load(tmp_path, '[[pattern]]\nkind = "X"\nregex = \'a*\'\n')
    assert spans(detector, "baab") == ["aa"]


# --- Dictionaries -----------------------------------------------------------------------------------


def test_a_dictionary_matches_whole_words_ignoring_case_and_prefers_the_longest(tmp_path):
    (detector,) = load(tmp_path, '[[dictionary]]\nkind = "CLIENT"\nterms = ["Acme", "Acme Labs", "North-wind"]\n')
    assert isinstance(detector, DictionaryDetector)
    assert spans(detector, "ACME Labs and acme, not Acmeville or Nacme, but north-wind.") == ["ACME Labs", "acme", "north-wind"]


def test_dictionary_options_and_terms_file(tmp_path):
    write(tmp_path, "clients.txt", "# our clients\nGlobex\n\n  Initech  \n")
    (detector,) = load(
        tmp_path,
        '[[dictionary]]\nkind = "CLIENT"\nterms = ["Acme"]\nterms_file = "clients.txt"\nignore_case = false\nword_boundaries = false\n',
    )
    assert spans(detector, "Acme globex Globex Initech Initechs ACME") == ["Acme", "Globex", "Initech", "Initech"]


def test_a_pattern_found_by_the_anonymizer_becomes_a_placeholder_that_restores(tmp_path):
    detectors = [
        *load(tmp_path, '[[pattern]]\nkind = "EMPLOYEE_ID"\nregex = \'EMP-\\d{6}\'\n[[dictionary]]\nkind = "CLIENT"\nterms = ["Acme"]\n'),
        PatternDetector(),
    ]
    text = "EMP-123456 visited Acme; EMP-123456 again, then EMP-654321."
    clean, mapping = anonymize_with_mapping(text, detectors)
    assert clean == "<EMPLOYEE_ID_1> visited <CLIENT_1>; <EMPLOYEE_ID_1> again, then <EMPLOYEE_ID_2>."
    assert restore(clean, mapping) == text


def test_custom_kinds_win_ties_over_the_builtin_ones_when_listed_first(tmp_path):
    custom = load(tmp_path, '[[pattern]]\nkind = "TICKET"\nregex = \'[A-Z]{3}-\\d{4}\'\n')
    assert anonymize("see ABC-1234", [*custom, PatternDetector()]) == "see <TICKET_1>"
    assert anonymize("see ABC-1234", [PatternDetector(), *custom]) == "see <ID_1>"


def test_kinds_of_lists_only_custom_kinds(tmp_path):
    detectors = [*load(tmp_path, '[[pattern]]\nkind = "A"\nregex = "x"\n[[dictionary]]\nkind = "B"\nterms = ["y"]\n'), PatternDetector()]
    assert kinds_of(detectors) == {"A", "B"}


# --- Errors ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[[pattern]]\nregex = "x"\n', "every detector needs a kind"),
        ('[[pattern]]\nkind = "lower"\nregex = "x"\n', "every detector needs a kind"),
        ('[[pattern]]\nkind = "EMP2"\nregex = "x"\n', "every detector needs a kind"),
        ('[[pattern]]\nkind = "A"\n', "needs a regex"),
        ('[[pattern]]\nkind = "A"\nregex = "("\n', "regex for A is invalid"),
        ('[[pattern]]\nkind = "A"\nregex = "x"\nflags = "z"\n', "flags for A"),
        ('[[pattern]]\nkind = "A"\nregex = "x"\ngroup = 3\n', "no group 3"),
        ('[[pattern]]\nkind = "A"\nregex = "x"\nregx = "y"\n', "unknown setting 'regx' for A"),
        ('[[dictionary]]\nkind = "A"\nterms = []\n', "has no terms"),
        ('[[dictionary]]\nkind = "A"\nterms = [1]\n', "terms for A must be a list of text"),
        ('[[dictionary]]\nkind = "A"\nterms = ["x"]\nignore_case = "yes"\n', "ignore_case for A must be true or false"),
        ('[[dictionary]]\nkind = "A"\nterms_file = "missing.txt"\n', "cannot read the terms file for A"),
        ('[schema]\nmap_paths = []\n', "belong in a detector file"),
        ("[[pattern", "isn't valid TOML"),
    ],
)
def test_mistakes_are_clear_errors(tmp_path, text, message):
    with pytest.raises(SourceError, match=message):
        load(tmp_path, text)


def test_errors_never_repeat_the_terms(tmp_path):
    for text in (
        f'[[dictionary]]\nkind = "A"\nterms = ["{CANARY}"]\nignore_case = 1\n',
        f'[[dictionary]]\nkind = "A"\nterms = ["{CANARY}"]\nunknown = 1\n',
        f'[[dictionary]]\nkind = "a"\nterms = ["{CANARY}"]\n',
    ):
        with pytest.raises(SourceError) as error:
            load(tmp_path, text)
        assert CANARY not in str(error.value)
    with pytest.raises(SourceError) as error:
        build_detectors([{"_type": "dictionary", "kind": "A", "terms_file": CANARY + ".txt", "_dir": str(tmp_path)}])
    assert CANARY not in str(error.value)


def test_a_missing_detector_file_is_an_error(tmp_path):
    with pytest.raises(SourceError, match="cannot read the detector file"):
        load_detector_file(tmp_path / "nope.toml")


# --- In commands -----------------------------------------------------------------------------------------


def test_scan_counts_custom_kinds_by_default_and_by_name(tmp_path, capsys):
    detectors = write(tmp_path, "det.toml", '[[pattern]]\nkind = "EMPLOYEE_ID"\nregex = \'EMP-\\d{6}\'\n')
    data = tmp_path / "d"
    data.mkdir()
    write(data, "a.txt", "EMP-123456 and EMP-654321 and zq@acme-corp.io\n")
    assert main(["scan", str(data), "--detectors", str(detectors), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"] == {"EMPLOYEE_ID": 2, "EMAIL": 1}
    assert main(["scan", str(data), "--detectors", str(detectors), "--kinds", "EMPLOYEE_ID", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"] == {"EMPLOYEE_ID": 2}
    assert main(["scan", str(data), "--kinds", "EMPLOYEE_ID"]) == 1  # Not known without the detector.
    assert "unknown kind" in capsys.readouterr().err


def test_scan_library_call_with_custom_detectors(tmp_path):
    (tmp_path / "a.txt").write_text("client Globex called\n", encoding="utf-8")
    detector = DictionaryDetector("CLIENT", ["Globex"])
    report = scan(str(tmp_path), detectors=[detector], kinds={"CLIENT"})
    assert report.totals == {"CLIENT": 1}


def test_scrub_applies_custom_detectors(tmp_path, capsys):
    detectors = write(tmp_path, "det.toml", '[[dictionary]]\nkind = "CLIENT"\nterms = ["Globex"]\n')
    log = write(tmp_path, "log.txt", "Globex export failed for EMP-123456\n")
    assert main(["scrub", str(log), "--no-ner", "--detectors", str(detectors)]) == 0
    out = capsys.readouterr().out
    assert "Globex" not in out and "<CLIENT_1>" in out
