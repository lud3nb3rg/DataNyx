"""Detector packs, other detector backends, and the eval command that compares them."""

import http.server
import json
import sys
import threading
import types

import pytest

from datanyx.cli import build_parser, main
from datanyx.cli.common import _models
from datanyx.config import describe, load_config
from datanyx.evaluation import evaluate, load_cases, render_report
from datanyx.sources import SourceError
from datanyx.text import Anonymizer, DictionaryDetector, PatternDetector, kinds_of
from datanyx.text.backends import (
    GlinerDetector,
    OllamaDetector,
    PresidioDetector,
    _windows,
    availability,
    build_backend,
)
from datanyx.text.packs import (
    PACKS,
    aba_valid,
    dea_valid,
    isin_valid,
    load_packs,
    luhn_valid,
    npi_valid,
    vin_valid,
)

PEOPLE = ["Jessica Whitfield", "Tomasz Kowalczyk", "Amara Okonkwo", "Will Turner"]


def people_detector():
    return DictionaryDetector("PERSON", PEOPLE)


def hidden(text, packs, extra=()):
    return Anonymizer([*load_packs(packs), *extra, PatternDetector()]).anonymize(text)


# --- Check digits ------------------------------------------------------------------------------------------


def test_check_digits_accept_real_numbers_and_reject_near_misses():
    assert luhn_valid("4539 1488 0343 6467") and not luhn_valid("4539 1488 0343 6468")
    assert aba_valid("021000021") and not aba_valid("021000022") and not aba_valid("12345")
    assert isin_valid("US0378331005") and isin_valid("GB0002634946") and not isin_valid("US0378331006")
    assert npi_valid("1234567893") and not npi_valid("1234567890")
    assert dea_valid("AB1234563") and not dea_valid("AB1234560") and not dea_valid("ZZ1234563")
    assert vin_valid("1HGCM82633A004352") and not vin_valid("1HGCM82633A004353") and not vin_valid("TOOSHORT")


# --- Healthcare --------------------------------------------------------------------------------------------

CHART = (
    "Patient: Amara Okonkwo, SSN 078-05-1120, MRN: 00482913, member ID: XZK4429981, NPI: 1234567893. "
    "Seen by Dr. Hiroshi Tanaka (DEA AB1234563) in Austin TX 78701. Age 93. She is a 94-year-old. "
    "VIN 1HGCM82633A004352. Serial no: SN-998877AA. Fax: +1 503 555 0142."
)


def test_healthcare_pack_finds_the_identifiers_it_covers():
    out = hidden(CHART, ["healthcare"])
    for kind in ("PERSON", "SSN", "MRN", "MEMBER_ID", "NPI", "DEA_NUMBER", "ZIP_CODE", "AGE", "VIN", "DEVICE_ID", "PHONE"):
        assert f"<{kind}_" in out, kind
    for gone in ("Okonkwo", "078-05-1120", "00482913", "XZK4429981", "1234567893", "Tanaka", "AB1234563", "78701", "1HGCM82633A004352", "SN-998877AA", "555 0142"):
        assert gone not in out
    assert "Austin TX" in out and "Age <AGE_1>" in out and "<AGE_2>-year-old" in out


def test_healthcare_pack_leaves_ordinary_text_and_younger_ages_alone():
    text = "The patient was given 93 tablets. The model aged 40 saw the doctor on Friday; a 34-year-old and a 12 year old. Call the clinic."
    assert hidden(text, ["healthcare"]) == text
    assert "<AGE_1>" in hidden("Age: 91", ["healthcare"]) and "88" in hidden("Age: 88", ["healthcare"])


def test_healthcare_labels_and_numbers_must_be_valid():
    # These still look like ids to the general detectors, but the pack doesn't claim them as its kind.
    assert "<NPI_" not in hidden("NPI: 1234567890", ["healthcare"])
    assert "<DEA_NUMBER_" not in hidden("DEA AB1234560", ["healthcare"])
    assert "<SSN_" not in hidden("SSN 000-12-3456", ["healthcare"]) and "<SSN_" not in hidden("SSN 666-12-3456", ["healthcare"])
    assert "member id: pending" in hidden("member id: pending", ["healthcare"])  # A word, not a code.
    assert "MRN: pending" in hidden("MRN: pending", ["healthcare"])
    assert "<SSN_1>" in hidden("SSN 123-45-6789", ["healthcare"])


def test_pack_names_join_the_regular_person_kind_so_placeholders_agree():
    out = hidden("Patient: Amara Okonkwo was seen. Later Amara Okonkwo called.", ["healthcare"], [people_detector()])
    assert out.count("<PERSON_1>") == 2 and "<PERSON_2>" not in out


# --- Finance -----------------------------------------------------------------------------------------------

LEDGER = (
    "Wire to account number 1234 5678 90, routing number 021000021, SWIFT: DEUTDEFF500, sort code 20-00-00. "
    "ISIN US0378331005. EIN: 12-3456789. NI AB123456C. VAT: GB123456789. "
    "Wallet 0x52908400098527886E0F7030069857D2E4169EE7."
)


def test_finance_pack_finds_account_and_tax_identifiers():
    out = hidden(LEDGER, ["finance"])
    for kind in ("ACCOUNT_NUMBER", "ROUTING_NUMBER", "SWIFT_CODE", "SORT_CODE", "ISIN", "TAX_ID", "NATIONAL_INSURANCE", "VAT_NUMBER", "CRYPTO_WALLET"):
        assert f"<{kind}_" in out, kind
    for gone in ("1234 5678 90", "021000021", "DEUTDEFF500", "20-00-00", "US0378331005", "12-3456789", "AB123456C", "GB123456789", "0x5290"):
        assert gone not in out


def test_finance_pack_needs_context_or_a_check_digit():
    text = "Total 2024 items. The word DELIVERY and the code AB12345678 are just text. Bank account of 2024. Routing number 123456789."
    out = hidden(text, ["finance"])
    assert not any(f"<{kind}_" in out for kind in ("ROUTING_NUMBER", "ACCOUNT_NUMBER", "SWIFT_CODE"))  # Failing its check.
    assert "2024 items" in out and "DELIVERY" in out
    assert "VAT: registered" in hidden("VAT: registered", ["finance"]) and "SWIFT: transfer" in hidden("SWIFT: transfer", ["finance"])


def test_packs_can_be_combined_and_unknown_ones_are_refused():
    both = load_packs(["healthcare", "finance", "healthcare"])
    assert len(both) == len(load_packs(["healthcare"])) + len(load_packs(["finance"]))
    assert {"SSN", "SWIFT_CODE"} <= kinds_of(both)
    assert "SSN" in kinds_of(load_packs(["HEALTHCARE"]))
    with pytest.raises(SourceError, match=r"unknown pack 'dental'.*finance, healthcare"):
        load_packs(["dental"])
    assert set(PACKS) == {"healthcare", "finance"}


def test_packs_are_available_in_commands(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)
    data = tmp_path / "d"
    data.mkdir()
    (data / "a.txt").write_text("SSN 123-45-6789 and routing number 021000021\n", encoding="utf-8")
    assert main(["scan", str(data), "--pack", "finance", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"] == {"SSN": 1, "ROUTING_NUMBER": 1}
    (tmp_path / "datanyx.toml").write_text('[anonymize]\npacks = ["healthcare"]\n', encoding="utf-8")
    assert main(["scan", str(data), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"] == {"SSN": 1}
    assert main(["scan", str(data), "--pack", "dental"]) == 1


# --- Backends ----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def presidio():
    pytest.importorskip("presidio_analyzer")
    spacy_util = pytest.importorskip("spacy.util")
    if not spacy_util.is_package("en_core_web_lg"):
        pytest.skip("needs en_core_web_lg")
    return PresidioDetector()


def test_presidio_finds_names_and_formats_and_feeds_the_anonymizer(presidio):
    out = Anonymizer([presidio]).anonymize(
        "Jessica Whitfield (jessica@acme-corp.io) lives in Portland, call +1 503 555 0142, card 4539 1488 0343 6467."
    )
    for kind in ("PERSON", "EMAIL", "LOCATION", "PHONE", "CREDIT_CARD"):
        assert f"<{kind}_1>" in out, kind
    assert "Whitfield" not in out and "acme-corp" not in out


def test_presidio_offsets_hold_for_long_text(presidio):
    filler = "The team reviewed ordinary topics.\n" * 4000
    out = Anonymizer([presidio]).anonymize(f"{filler}Jessica Whitfield approved it.\n")
    assert "Whitfield" not in out and out.startswith("The team reviewed") and out.endswith(" approved it.\n")


def test_presidio_needs_its_extra_a_model_and_a_language_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "presidio_analyzer", None)
    with pytest.raises(SourceError, match=r"datanyx\[presidio\]"):
        PresidioDetector()
    monkeypatch.undo()
    pytest.importorskip("presidio_analyzer")
    pytest.importorskip("spacy")
    with pytest.raises(SourceError, match="needs a spaCy model for 'de'"):
        PresidioDetector("de")
    with pytest.raises(SourceError, match="NER model not found"):
        PresidioDetector("en", "no_such_model")


def install_fake_gliner(monkeypatch, entities):
    calls = []

    class Model:
        def predict_entities(self, text, labels, threshold):
            calls.append((text, list(labels), threshold))
            return [dict(entity) for entity in entities(text)]

    module = types.ModuleType("gliner")
    module.GLiNER = types.SimpleNamespace(from_pretrained=lambda name: Model())
    monkeypatch.setitem(sys.modules, "gliner", module)
    return calls


def test_gliner_labels_become_kinds_and_windows_keep_offsets(monkeypatch):
    def entities(window):
        found = []
        for word, label in (("Jessica", "person"), ("Leeds", "location"), ("odd", "mystery thing")):
            at = window.find(word)
            if at != -1:
                found.append({"start": at, "end": at + len(word), "label": label})
        return found

    calls = install_fake_gliner(monkeypatch, entities)
    detector = GlinerDetector(window=40)
    text = "filler " * 20 + "Jessica works in Leeds. " + "more " * 20 + "odd"
    spans = list(detector.find(text))
    assert {(text[s.start : s.end], s.kind) for s in spans} == {("Jessica", "PERSON"), ("Leeds", "LOCATION"), ("odd", "MISC")}
    assert len(calls) > 3 and all("person" in labels for _, labels, _ in calls) and calls[0][2] == 0.5
    assert Anonymizer([detector]).anonymize(text).count("<PERSON_1>") == 1


def test_gliner_reports_a_missing_package_and_a_model_that_wont_load(monkeypatch):
    monkeypatch.setitem(sys.modules, "gliner", None)
    with pytest.raises(SourceError, match="pip install gliner"):
        GlinerDetector()
    broken = types.ModuleType("gliner")

    def fail(name):
        raise OSError("zq-secret-detail")

    broken.GLiNER = types.SimpleNamespace(from_pretrained=fail)
    monkeypatch.setitem(sys.modules, "gliner", broken)
    with pytest.raises(SourceError, match="could not be loaded") as error:
        GlinerDetector()
    assert "zq-secret-detail" not in str(error.value)


@pytest.fixture
def ollama_server():
    state = {"requests": [], "answer": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, body))
            answer = state["answer"] or {"items": []}
            payload = json.dumps({"response": answer if isinstance(answer, str) else json.dumps(answer)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def test_ollama_quotes_become_spans_for_every_occurrence(ollama_server):
    ollama_server["answer"] = {
        "items": [
            {"text": "Jessica Whitfield", "kind": "PERSON"},
            {"text": "Leeds", "kind": "LOCATION"},
            {"text": "blue-widget", "kind": "something else"},
            {"text": "", "kind": "PERSON"},
        ]
    }
    detector = OllamaDetector("tiny", ollama_server["url"])
    text = "Jessica Whitfield is in Leeds. Leeds again, Jessica Whitfield too, a blue-widget."
    spans = list(detector.find(text))
    assert sorted((text[s.start : s.end], s.kind) for s in spans) == sorted(
        [("Jessica Whitfield", "PERSON")] * 2 + [("Leeds", "LOCATION")] * 2 + [("blue-widget", "MISC")]
    )
    path, body = ollama_server["requests"][0]
    assert path == "/api/generate" and body["model"] == "tiny" and body["stream"] is False
    assert text in body["prompt"] and body["options"]["temperature"] == 0 and "properties" in body["format"]


def test_ollama_refuses_remote_servers_and_fails_without_quoting_text(ollama_server):
    for url in ("http://example.com:11434", "https://api.openai.com", "http://192.168.1.20:11434"):
        with pytest.raises(SourceError, match="on this computer"):
            OllamaDetector("m", url)
    assert OllamaDetector("m", "http://localhost:11434") and OllamaDetector("m", "http://[::1]:11434")
    dead = OllamaDetector("m", "http://127.0.0.1:1")
    with pytest.raises(SourceError, match="could not be reached") as error:
        list(dead.find("zq-secret-text"))
    assert "zq-secret-text" not in str(error.value)
    ollama_server["answer"] = "this is not json zq-secret-answer"
    with pytest.raises(SourceError, match="wasn't usable") as error:
        list(OllamaDetector("m", ollama_server["url"]).find("hello"))
    assert "zq-secret-answer" not in str(error.value)


def test_backend_specs_availability_and_windows(monkeypatch, ollama_server):
    install_fake_gliner(monkeypatch, lambda window: [])
    assert isinstance(build_backend("gliner"), GlinerDetector) and isinstance(build_backend("gliner:some/model"), GlinerDetector)
    assert isinstance(build_backend(f"ollama:tiny@{ollama_server['url']}"), OllamaDetector)
    with pytest.raises(SourceError, match=r"unknown backend 'bert'.*presidio, gliner, ollama"):
        build_backend("bert")
    assert set(availability()) == {"presidio", "gliner", "ollama"} and availability()["ollama"] is True
    pieces = list(_windows("alpha beta gamma delta", 12))
    assert "".join(text for _, text in pieces) == "alpha beta gamma delta"
    assert all(start == sum(len(t) for _, t in pieces[:i]) for i, (start, _) in enumerate(pieces))
    assert all(len(text) <= 12 for _, text in pieces)


def test_backends_run_through_commands(tmp_path, ollama_server, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)
    ollama_server["answer"] = {"items": [{"text": "Globex", "kind": "ORG"}]}
    log = tmp_path / "log.txt"
    log.write_text("Globex export failed\n", encoding="utf-8")
    assert main(["scrub", str(log), "--no-ner", "--backend", f"ollama:tiny@{ollama_server['url']}"]) == 0
    assert "Globex" not in capsys.readouterr().out
    assert main(["scrub", str(log), "--no-ner", "--backend", "nonsense"]) == 1
    assert "unknown backend" in capsys.readouterr().err


# --- Evaluation --------------------------------------------------------------------------------------------


def write_cases(tmp_path, cases, name="labelled.json"):
    path = tmp_path / name
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def make_anonymizer():
    return Anonymizer([DictionaryDetector("PERSON", ["Jessica Whitfield", "Tomasz"]), PatternDetector()])


CASES = [
    {
        "name": "ticket",
        "text": "Jessica Whitfield wrote from jw@acme-corp.io. Will Turner and Tomasz Kowalczyk replied; Will you join?",
        "sensitive": {
            "PERSON": ["Jessica Whitfield", "Will Turner", "Tomasz Kowalczyk"],
            "EMAIL": ["jw@acme-corp.io"],
        },
    }
]


def test_evaluation_counts_hidden_partly_hidden_and_missed(tmp_path):
    cases = load_cases(write_cases(tmp_path, CASES))
    report = evaluate(cases, make_anonymizer)
    person = report.kinds["PERSON"]
    assert (person.total, person.hidden, person.partial, person.missed) == (3, 1, 1, 1)
    assert report.kinds["EMAIL"].recall == 1.0 and report.overall.total == 4
    outcomes = {(string, outcome) for _, _, string, outcome in report.misses}
    assert outcomes == {("Tomasz Kowalczyk", "partly hidden"), ("Will Turner", "missed")}


def test_titles_and_ordinary_case_differences_are_not_leaks(tmp_path):
    cases = load_cases(
        write_cases(
            tmp_path,
            [{"text": "Dr. Jessica Whitfield saw it. Will she come?", "sensitive": {"PERSON": ["Dr. Jessica Whitfield", "Jessica Whitfield"]}}],
        )
    )
    assert evaluate(cases, make_anonymizer).kinds["PERSON"].hidden == 2


def test_replacements_the_labels_dont_cover_are_counted(tmp_path):
    cases = load_cases(write_cases(tmp_path, [{"text": "Mail jw@acme-corp.io or other@acme-corp.io", "sensitive": {"EMAIL": ["jw@acme-corp.io"]}}]))
    assert evaluate(cases, make_anonymizer).unlabelled == {"EMAIL": 1}


def test_kinds_can_be_selected_and_thresholds_checked(tmp_path):
    report = evaluate(load_cases(write_cases(tmp_path, CASES)), make_anonymizer, {"EMAIL"})
    assert set(report.kinds) == {"EMAIL"} and report.below(0.9) == []
    full = evaluate(load_cases(write_cases(tmp_path, CASES)), make_anonymizer)
    assert full.below(0.9) == ["overall", "PERSON"] and full.below(0.1) == []


def test_labels_can_be_character_spans_and_files_can_be_jsonl_or_wrapped(tmp_path):
    text = "Call Tomasz on 555 0100"
    span_case = {"name": "x", "text": text, "spans": [{"start": 5, "end": 11, "kind": "PERSON"}]}
    assert load_cases(write_cases(tmp_path, [span_case]))[0].sensitive == {"PERSON": ["Tomasz"]}
    assert load_cases(write_cases(tmp_path, {"documents": [span_case]}, "wrapped.json"))[0].name == "x"
    lines = tmp_path / "labelled.jsonl"
    lines.write_text(json.dumps(span_case) + "\n\n" + json.dumps(span_case) + "\n", encoding="utf-8")
    assert len(load_cases(lines)) == 2


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{ not json", "isn't valid JSON"),
        ("[]", "list of documents"),
        ('[{"name": "a"}]', "needs a text"),
        ('[{"text": "hi", "sensitive": {}}]', "nothing is labelled"),
        ('[{"text": "hi", "sensitive": {"PERSON": ["nobody"]}}]', "isn't in the text"),
        ('[{"text": "hi", "sensitive": {"PERSON": "hi"}}]', "should be a list of text"),
        ('[{"text": "hi", "spans": [{"start": 0, "end": 99, "kind": "X"}]}]', "outside the text"),
        ('[{"text": "hi", "spans": [{"start": 0}]}]', "needs a start, an end and a kind"),
    ],
)
def test_bad_label_files_are_clear_errors_that_dont_repeat_labels(tmp_path, content, message):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SourceError, match=message) as error:
        load_cases(path)
    assert "nobody" not in str(error.value)
    with pytest.raises(SourceError, match="cannot read"):
        load_cases(tmp_path / "missing.json")


def test_reports_in_text_and_json(tmp_path):
    report = evaluate(load_cases(write_cases(tmp_path, CASES)), make_anonymizer)
    text = render_report(report)
    assert "Evaluated 1 document, 4 labelled strings." in text and "PERSON" in text and "Overall" in text
    assert "Tomasz Kowalczyk" not in text and "--show-misses" in text  # Strings only when asked.
    assert "Tomasz Kowalczyk" in render_report(report, show_misses=True)
    data = json.loads(render_report(report, output_format="json"))
    assert data["kinds"]["PERSON"]["missed"] == 1 and data["overall"]["recall"] == 0.5 and "misses" not in data
    assert json.loads(render_report(report, True, "json"))["misses"][0]["document"] == "ticket"


def test_eval_command_compares_settings_and_gates_on_recall(tmp_path, capsys, monkeypatch):
    spacy_util = pytest.importorskip("spacy.util")
    if not spacy_util.is_package("en_core_web_lg"):
        pytest.skip("needs en_core_web_lg")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)
    text = "Jessica Whitfield (jessica@acme-corp.io) lives in Portland. Age: 93."
    path = write_cases(tmp_path, [{"text": text, "sensitive": {"PERSON": ["Jessica Whitfield"], "LOCATION": ["Portland"], "AGE": ["93"]}}])
    assert main(["eval", str(path), "--format", "json"]) == 0
    default = json.loads(capsys.readouterr().out)["kinds"]
    assert default["PERSON"]["recall"] == 1.0 and default["AGE"]["recall"] == 0.0
    assert main(["eval", str(path), "--pack", "healthcare", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["kinds"]["AGE"]["recall"] == 1.0
    assert main(["eval", str(path), "--no-ner", "--kind", "PERSON", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["kinds"] == {"PERSON": {"total": 1, "hidden": 0, "partly_hidden": 0, "missed": 1, "recall": 0.0}}
    assert main(["eval", str(path), "--min-recall", "0.9"]) == 1
    assert "recall is below 90% for: overall, AGE" in capsys.readouterr().err
    assert main(["eval", str(path), "--pack", "healthcare", "--min-recall", "1"]) == 0
    assert main(["eval", str(path), "--min-recall", "2"]) == 1


# --- Models and settings -----------------------------------------------------------------------------------


def parse(*args):
    return build_parser().parse_args(["anonymize", "x.txt", *args])


def test_models_come_from_the_command_line_then_the_config_then_the_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)

    def models(*args):
        namespace = parse(*args)
        namespace.no_config, namespace.config_file = False, None
        return _models(namespace)

    assert models() == ["en_core_web_lg"]
    assert models("--model", "fr_core_news_md") == ["fr_core_news_md"]
    assert models("--model", "a, b", "--model", "c") == ["a", "b", "c"]
    (tmp_path / "datanyx.toml").write_text('[anonymize]\nmodel = "single"\n', encoding="utf-8")
    assert models() == ["single"]
    (tmp_path / "datanyx.toml").write_text('[anonymize]\nmodel = "single"\nmodels = ["x", "y"]\n', encoding="utf-8")
    assert models() == ["x", "y"] and models("--model", "z") == ["z"]


def test_new_settings_are_validated_and_shown(tmp_path, monkeypatch):
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)
    path = tmp_path / "datanyx.toml"
    path.write_text('[anonymize]\nmodels = ["a", "b"]\npacks = ["finance"]\nbackends = ["presidio"]\n', encoding="utf-8")
    config = load_config(path)
    assert (config.models, config.packs, config.backends) == (("a", "b"), ("finance",), ("presidio",))
    shown = describe(config)
    assert "packs: finance" in shown and "backends: presidio" in shown and "models: a, b" in shown
    path.write_text('[anonymize]\npack = ["finance"]\n', encoding="utf-8")
    with pytest.raises(SourceError, match="did you mean 'packs'"):
        load_config(path)


def test_doctor_lists_backends_and_packs(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "backend presidio" in out and "backend gliner" in out and "packs: finance, healthcare" in out
