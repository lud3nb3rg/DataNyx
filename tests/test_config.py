"""datanyx.toml: found, merged, validated, and applied by the commands that use it."""

import json

import pytest

from datanyx.cli import main
from datanyx.config import Config, describe, find_files, load_config
from datanyx.sources import SourceError

CANARY = "zqcanary-client"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATANYX_CONFIG", raising=False)
    return tmp_path


def write(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# --- Finding and merging -------------------------------------------------------------------


def test_no_file_means_no_settings(project):
    config = load_config()
    assert not config.found and config.map_paths == () and config.model is None


def test_the_nearest_project_file_is_found_from_a_subfolder(project):
    write(project, "datanyx.toml", '[schema]\nkeep_names = ["Order Details"]\n')
    inner = project / "src" / "deep"
    inner.mkdir(parents=True)
    assert load_config(start=inner).keep_names == ("Order Details",)
    assert find_files(inner) == [project / "datanyx.toml"]


def test_an_explicit_file_and_the_environment_override_the_search(project, monkeypatch):
    write(project, "datanyx.toml", '[schema]\nkeep_names = ["a"]\n')
    other = write(project, "other.toml", '[schema]\nkeep_names = ["b"]\n')
    assert load_config(other).keep_names == ("b",)
    monkeypatch.setenv("DATANYX_CONFIG", str(other))
    assert load_config().keep_names == ("b",)
    with pytest.raises(SourceError, match="doesn't exist"):
        load_config(project / "missing.toml")


def test_the_local_file_is_merged_on_top(project):
    write(project, "datanyx.toml", '[anonymize]\nmodel = "shared"\nhide = ["Acme"]\n[schema]\nmap_paths = ["$.a"]\n')
    write(project, "datanyx.local.toml", '[anonymize]\nmodel = "mine"\nhide = ["Northwind", "Acme"]\n')
    config = load_config()
    assert config.model == "mine"  # A single value: the local file wins.
    assert config.hide == ("Acme", "Northwind")  # Lists are joined without repeats.
    assert config.map_paths == ("$.a",)
    assert [path.name for path in config.files] == ["datanyx.toml", "datanyx.local.toml"]


def test_a_local_file_alone_is_enough(project):
    write(project, "datanyx.local.toml", '[anonymize]\nhide = ["Acme"]\n')
    assert load_config().hide == ("Acme",)


def test_paths_are_relative_to_the_file_that_names_them(project):
    folder = project / "conf"
    folder.mkdir()
    write(folder, "datanyx.toml", '[mcp]\nroots = ["../data"]\naudit_log = "audit.jsonl"\n[scrub]\nnames_from = ["x.csv"]\n')
    config = load_config(folder / "datanyx.toml")
    assert config.mcp_roots == ((project / "data").resolve(),)
    assert config.audit_log == (folder / "audit.jsonl").resolve()
    assert config.scrub_names_from == ((folder / "x.csv").resolve(),)


# --- Validation ------------------------------------------------------------------------------


def error_for(project, text):
    write(project, "datanyx.toml", text)
    with pytest.raises(SourceError) as error:
        load_config()
    return str(error.value)


def test_typos_are_errors_with_a_suggestion(project):
    assert "unknown setting 'hid' in [anonymize]; did you mean 'hide'?" in error_for(project, '[anonymize]\nhid = ["x"]\n')
    assert "unknown section [scrubb]; did you mean 'scrub'?" in error_for(project, "[scrubb]\n")
    assert "unknown setting 'colour' in [scan]" in error_for(project, "[scan]\ncolour = 1\n")


def test_wrong_types_are_errors(project):
    assert "[scan] max_size must be a number" in error_for(project, '[scan]\nmax_size = "big"\n')
    assert "[anonymize] hide must be a list of text" in error_for(project, "[anonymize]\nhide = [1, 2]\n")
    assert "[scrub] ner must be a true or false" in error_for(project, '[scrub]\nner = "yes"\n')
    assert "[schema] must be a table" in error_for(project, 'schema = "x"\n')
    assert "isn't valid TOML" in error_for(project, "[anonymize\n")


def test_errors_never_repeat_the_values(project):
    for text in (f'[anonymize]\nhid = ["{CANARY}"]\n', f'[anonymize]\nhide = "{CANARY}"\n', f'[scan]\nmax_size = "{CANARY}"\n'):
        assert CANARY not in error_for(project, text)


# --- Showing it ----------------------------------------------------------------------------------


def test_describe_counts_terms_unless_asked_to_reveal_them(project):
    write(project, "datanyx.toml", '[schema]\nkeep_names = ["Order Details"]\n[anonymize]\nhide = ["Acme", "Northwind"]\n')
    quiet = describe(load_config())
    assert "Order Details" in quiet and "Acme" not in quiet and "2 (hidden; use --reveal to show)" in quiet
    assert "Acme, Northwind" in describe(load_config(), reveal=True)
    assert "datanyx init" in describe(Config())


def test_config_command(project, capsys):
    assert main(["config"]) == 0 and "No datanyx.toml found" in capsys.readouterr().out
    write(project, "datanyx.toml", '[anonymize]\nhide = ["Acme"]\n')
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    assert "datanyx.toml" in out and "Acme" not in out
    assert main(["config", "--reveal"]) == 0 and "Acme" in capsys.readouterr().out


def test_init_creates_starter_files_and_keeps_the_local_one_out_of_git(project, capsys):
    assert main(["init"]) == 0
    assert (project / "datanyx.toml").read_text(encoding="utf-8").startswith("# DataNyx project settings")
    assert load_config().found  # The starter is valid, with everything commented out.
    assert main(["init"]) == 1 and "already exists" in capsys.readouterr().err

    (project / ".git").mkdir()
    assert main(["init", "--local"]) == 0
    assert "datanyx.local.toml" in (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert main(["init", "--local"]) == 1


# --- Applied by commands --------------------------------------------------------------------------------


def test_schema_uses_the_map_paths_from_the_config(project, capsys):
    data = write(project, "d.json", json.dumps({"users": {"alice": {"age": 1}, "bob": {"age": 2}}}))
    main(["schema", str(data)])
    assert "alice" in capsys.readouterr().out

    write(project, "datanyx.toml", '[schema]\nmap_paths = ["$.users"]\n')
    main(["schema", str(data)])
    out = capsys.readouterr().out
    assert "alice" not in out and "additionalProperties" in out

    main(["--no-config", "schema", str(data)])
    assert "alice" in capsys.readouterr().out


def test_command_line_options_are_added_to_the_config(project, capsys):
    data = write(project, "d.json", json.dumps({"a": {"secretp": 1}, "b": {"secretq": 2}, "c": {"shown": 3}}))
    write(project, "datanyx.toml", '[schema]\nmap_paths = ["$.a"]\n')
    main(["schema", str(data), "--map-path", "$.b", "--format", "text"])
    out = capsys.readouterr().out
    assert "secretp" not in out and "secretq" not in out and "shown" in out


def test_an_explicit_config_option_is_used(project, capsys):
    data = write(project, "d.json", json.dumps({"users": {"alice": {"age": 1}, "bob": {"age": 2}}}))
    other = write(project, "elsewhere.toml", '[schema]\nmap_paths = ["$.users"]\n')
    main(["--config", str(other), "schema", str(data), "--format", "text"])
    assert "alice" not in capsys.readouterr().out


def test_a_broken_config_is_a_clean_error(project, capsys):
    write(project, "datanyx.toml", "[anonymize]\nhid = []\n")
    assert main(["schema", str(write(project, "d.json", "{}"))]) == 1
    assert "did you mean 'hide'?" in capsys.readouterr().err


def test_scrub_uses_allowed_names_hide_terms_and_ner_setting(project, capsys):
    write(project, "datanyx.toml", '[scrub]\nner = false\nallow = ["plan"]\n[anonymize]\nhide = ["Northwind"]\n')
    log = write(project, "log.txt", "KeyError: 'plan'\nKeyError: 'other'\nNorthwind export failed\n")
    assert main(["scrub", str(log)]) == 0
    out = capsys.readouterr().out
    assert "'plan'" in out and "'other'" not in out and "Northwind" not in out


def test_scan_uses_exclude_kinds_and_size_from_the_config(project, capsys):
    write(project, "a.txt", "mail zq@acme-corp.io or call +44 20 7946 0958\n")
    write(project, "skip.txt", "mail zq@acme-corp.io\n")
    write(project, "datanyx.toml", '[scan]\nexclude = ["skip.txt"]\nkinds = ["EMAIL"]\n')
    assert main(["scan", ".", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [f["path"] for f in data["files"]] == ["a.txt"] and set(data["totals"]) == {"EMAIL"}
    write(project, "datanyx.toml", "[scan]\nmax_size = 0.00001\n")
    main(["scan", ".", "--format", "json"])
    assert json.loads(capsys.readouterr().out)["skipped"]["too_large"] >= 1


def test_anonymize_uses_config_terms_custom_detectors_and_entities(project, capsys):
    spacy_util = pytest.importorskip("spacy.util")
    if not spacy_util.is_package("en_core_web_lg"):
        pytest.skip("needs en_core_web_lg")
    write(project, "datanyx.toml", '[anonymize]\nkeep_entities = ["EMPLOYEE_ID"]\n[[pattern]]\nkind = "TICKET"\nregex = \'[A-Z]{3}-\\d{4}\'\n')
    write(project, "datanyx.local.toml", f'[anonymize]\nhide = ["{CANARY}"]\n[[pattern]]\nkind = "EMPLOYEE_ID"\nregex = \'EMP-\\d{{6}}\'\n')
    text = write(project, "t.txt", f"EMP-123456 filed ABC-1234 for {CANARY}.\n")
    assert main(["anonymize", str(text)]) == 0
    out = capsys.readouterr().out
    assert "EMP-123456" in out  # Kept by keep_entities.
    assert "<TICKET_1>" in out and CANARY not in out
    assert main(["anonymize", str(text), "--keep-entity", "NOT_A_KIND"]) == 1
    assert "unknown entity kind" in capsys.readouterr().err
