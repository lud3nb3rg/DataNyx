"""The guard refuses direct access to protected data, lets DataNyx's own commands through,
and the setup it writes is merged safely, repeatable and reversible."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from datanyx.cli import main
from datanyx.protect import ProtectConfig, apply, decide, plan_protect, plan_remove, run_hook, self_test, split_command
from datanyx.sources import SourceError

CSV = "id,email\n1,a@x.com\n"


@pytest.fixture
def project(tmp_path):
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data2").mkdir()
    (root / "src").mkdir()
    (root / "data" / "customers.csv").write_text(CSV, encoding="utf-8")
    (root / "data" / "notes.txt").write_text("hello", encoding="utf-8")
    (root / "data2" / "other.csv").write_text(CSV, encoding="utf-8")
    (root / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    return root


@pytest.fixture
def config(project):
    return ProtectConfig(project, (project / "data",))


def call(config, tool, cwd=None, **tool_input):
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or config.root)}
    return decide(payload, config)


def bash(config, command, **options):
    return call(config, "Bash", command=command, **options)


# --- Read, Write, Grep, Glob ----------------------------------------------------------


def test_reading_protected_files_is_refused_with_a_way_forward(config, project):
    reason = call(config, "Read", file_path=str(project / "data" / "customers.csv"))
    assert reason and "datanyx schema data" in reason and "datanyx run" in reason
    assert call(config, "Read", file_path="data/customers.csv")  # Relative to the working folder.
    assert call(config, "NotebookRead", notebook_path=str(project / "data" / "n.ipynb"))


def test_other_paths_are_untouched_even_with_a_similar_name(config, project):
    assert call(config, "Read", file_path=str(project / "src" / "a.py")) is None
    assert call(config, "Read", file_path=str(project / "data2" / "other.csv")) is None
    assert call(config, "Read", file_path=str(project / "database.txt")) is None
    assert call(config, "Read", file_path="") is None


def test_changing_protected_data_and_the_setup_files_is_refused(config, project):
    assert call(config, "Write", file_path=str(project / "data" / "x.csv"), content="")
    assert call(config, "Edit", file_path=str(project / "data" / "customers.csv"))
    for guarded in (".claude/settings.json", ".claude/settings.local.json", ".mcp.json", ".datanyx/protect.json"):
        assert "protection" in call(config, "Write", file_path=str(project / guarded), content="{}")
    assert call(config, "Write", file_path=str(project / "src" / "new.py"), content="x") is None


def test_searching_inside_protected_data_or_a_folder_that_contains_it(config, project):
    assert call(config, "Grep", path=str(project / "data"), pattern="x")
    assert "narrower folder" in call(config, "Grep", pattern="x")  # No path: the project root.
    assert "narrower folder" in call(config, "Grep", path=str(project), pattern="x")
    assert call(config, "Grep", path=str(project / "src"), pattern="x") is None
    assert call(config, "Glob", path=str(project / "data"), pattern="*.csv")
    assert call(config, "Glob", path=str(project), pattern="src/*.py") is None
    assert call(config, "Glob", path=str(project), pattern="data/*.csv")


def test_broad_searches_can_be_allowed(project):
    relaxed = ProtectConfig(project, (project / "data",), block_broad_search=False)
    assert call(relaxed, "Grep", path=str(project), pattern="x") is None
    assert bash(relaxed, "grep -rn foo .") is None
    assert call(relaxed, "Grep", path=str(project / "data"), pattern="x")  # Direct access still refused.


# --- Bash: direct references ----------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat data/customers.csv",
        "ls data",
        "head -5 ./data/customers.csv",
        "cat < data/customers.csv",
        "cp data/customers.csv /tmp/copy.csv",
        "python -c \"print(open('data/customers.csv').read())\"",
        "python -c 'import os; os.listdir(\"data\")'",
        "cd data && cat customers.csv",
        "cd data; ls",
        "echo hi && cat data/customers.csv",
        "echo hi | tee data/customers.csv",
        "cat data/*.csv",
        "cat da*/customers.csv",
        "cat 'data/customers.csv'",
        "FOO=1 cat data/customers.csv",
        "sudo cat data/customers.csv",
        "tail --file=data/notes.txt",
        "cat $(echo data/customers.csv)",
    ],
)
def test_shell_commands_that_name_protected_data_are_refused(config, command):
    assert bash(config, command), command


def test_shell_commands_with_absolute_paths_are_refused(config, project):
    assert bash(config, f"cat {(project / 'data' / 'customers.csv').as_posix()}")
    assert bash(config, f"cat '{project / 'data' / 'customers.csv'}'")


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_windows_style_paths_are_refused(config):
    assert bash(config, r"type data\customers.csv")
    assert bash(config, r"Get-Content .\data\customers.csv")
    assert call(config, "PowerShell", command=r"Get-Content .\data\customers.csv")


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > out.txt",
        "git status",
        "git log --oneline -3",
        'echo "update data"',
        'git commit -m "data fix"',
        "cat src/a.py",
        "ls src",
        "cat data2/other.csv",
        "python -c \"print('hello')\"",
        "python src/a.py",
        "pytest -q",
        "cd src && ls",
        "grep foo src/a.py",
        "grep -r foo src",
        "rg foo src",
        "find src -name '*.py'",
        "find . -name '*.py'",
        "cat .claude/settings.json",
        "",
    ],
)
def test_ordinary_commands_are_left_alone(config, command):
    assert bash(config, command) is None, command


# --- Bash: broad searches -------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["grep -rn foo .", "grep -R foo", "rg foo", "rg foo .", "ag foo", "find . -exec cat {} +", "find . -type f -exec grep x {} ;"],
)
def test_recursive_searches_that_would_reach_protected_data_are_refused(config, command):
    assert "narrower folder" in (bash(config, command) or ""), command


def test_searches_from_inside_a_safe_folder_are_fine(config, project):
    assert call(config, "Bash", cwd=project / "src", command="rg foo") is None
    assert call(config, "Bash", cwd=project / "src", command="grep -r foo .") is None


# --- Bash: DataNyx's own commands ------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "datanyx schema data/customers.csv",
        "datanyx schema ./data -r --format text",
        "datanyx synth data/customers.csv --rows 20",
        "datanyx check q.sql --against data/customers.csv",
        "datanyx anonymize data/notes.txt",
        "datanyx scrub data/notes.txt",
        "datanyx run -- python src/a.py",
        "datanyx run --names-from data/customers.csv --stdout scrubbed -- python src/a.py",
        "uv run datanyx schema data/customers.csv",
        "uv run --project tools datanyx schema data/customers.csv",
        "python -m datanyx schema data/customers.csv",
        "python3 -m datanyx synth data",
        "DATANYX=1 datanyx schema data/customers.csv",
        "datanyx.exe schema data/customers.csv",
    ],
)
def test_datanyx_commands_that_dont_reveal_data_are_allowed(config, command):
    assert bash(config, command) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "datanyx schema data/customers.csv && cat data/customers.csv",
        "datanyx schema data/customers.csv; cat data/customers.csv",
        "datanyx schema data/customers.csv | cat data/customers.csv",
        "datanyx run -- cat data/customers.csv",
        "datanyx run --stdout scrubbed -- python -c \"open('data/customers.csv')\"",
        "datanyx run --save-stdout out.txt -- python src/a.py",
        "datanyx run --save-stderr=err.txt -- python src/a.py",
        "datanyx anonymize data/notes.txt --mapping notes.mapping.json",
        "datanyx restore out.txt --mapping m.json -o real.txt",
        "datanyx protect --remove --write",
        "uv run datanyx protect --remove --write",
        "datanyx schema data/customers.csv $(cat data/customers.csv)",
        "datanyx schema `cat data/customers.csv`",
    ],
)
def test_datanyx_commands_that_would_reveal_data_or_weaken_protection_are_refused(config, command):
    assert bash(config, command), command


def test_the_setup_files_cant_be_changed_from_the_shell(config):
    for command in (
        "sed -i s/deny/allow/ .claude/settings.json",
        "echo '{}' > .claude/settings.local.json",
        "rm .datanyx/protect.json",
        "python -c \"open('.mcp.json','w')\"",
        "mv .mcp.json /tmp",
    ):
        assert "protection" in (bash(config, command) or ""), command
    assert bash(config, "cat .datanyx/protect.json") is None
    assert bash(config, "git diff .claude/settings.json") is None


# --- The hook ---------------------------------------------------------------------------


def payload(project, tool, **tool_input):
    return json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(project)})


def configure(project, paths=("data",)):
    apply(plan_protect(project, [project / p for p in paths], ["datanyx"]))


def test_hook_output_is_a_claude_code_denial(project):
    configure(project)
    out = run_hook(payload(project, "Read", file_path=str(project / "data" / "customers.csv")), {})
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse" and decision["permissionDecision"] == "deny"
    assert "datanyx schema" in decision["permissionDecisionReason"]
    assert run_hook(payload(project, "Read", file_path=str(project / "src" / "a.py")), {}) == ""


def test_hook_finds_the_project_from_a_subfolder_or_the_environment(project):
    configure(project)
    inside = {"tool_name": "Bash", "tool_input": {"command": "cat ../data/customers.csv"}, "cwd": str(project / "src")}
    assert json.loads(run_hook(json.dumps(inside), {}))["hookSpecificOutput"]["permissionDecision"] == "deny"
    elsewhere = {"tool_name": "Read", "tool_input": {"file_path": str(project / "data" / "customers.csv")}, "cwd": str(project.parent)}
    assert run_hook(json.dumps(elsewhere), {}) == ""  # Not in a protected project, and no hint.
    assert json.loads(run_hook(json.dumps(elsewhere), {"CLAUDE_PROJECT_DIR": str(project)}))


def test_hook_fails_closed_on_garbage_and_errors(project, monkeypatch):
    configure(project)
    assert "blocked to be safe" in json.loads(run_hook("not json", {}))["hookSpecificOutput"]["permissionDecisionReason"]
    assert "blocked to be safe" in json.loads(run_hook("[1, 2]", {}))["hookSpecificOutput"]["permissionDecisionReason"]
    monkeypatch.setattr("datanyx.protect.guard.decide", lambda *_: 1 / 0)
    assert "guard failed" in json.loads(run_hook(payload(project, "Read", file_path="x"), {}))["hookSpecificOutput"]["permissionDecisionReason"]


def test_unrelated_tools_and_unprotected_projects_pass(project, tmp_path):
    configure(project)
    assert run_hook(payload(project, "WebFetch", url="https://example.com"), {}) == ""
    bare = tmp_path / "bare"
    bare.mkdir()
    assert run_hook(payload(bare, "Bash", command="cat data/x.csv"), {}) == ""


# --- Settings ------------------------------------------------------------------------------


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_dry_run_changes_nothing_and_write_creates_the_setup(project):
    changes = plan_protect(project, [project / "data"], ["datanyx"])
    assert {c.path.name for c in changes} == {"settings.json", ".mcp.json", "protect.json"}
    assert not (project / ".claude").exists()

    apply(changes)
    settings = read_json(project / ".claude" / "settings.json")
    assert settings["permissions"]["deny"] == ["Read(data/**)", "Edit(data/**)", "Read(datanyx.local.toml)"]
    hook = settings["hooks"]["PreToolUse"][0]
    assert "Bash" in hook["matcher"] and "Grep" in hook["matcher"]
    assert hook["hooks"][0] == {
        "type": "command",
        "command": "datanyx",
        "args": ["hook", "--require-config"],
        "onFailure": "block",
        "statusMessage": "DataNyx guard",
    }
    assert read_json(project / ".mcp.json")["mcpServers"]["datanyx"] == {
        "command": "datanyx",
        "args": ["mcp", "--root", "data"],
    }
    assert read_json(project / ".datanyx" / "protect.json") == {"version": 1, "paths": ["data"], "block_broad_search": True}


def test_a_launcher_with_arguments_is_split_into_command_and_args(project):
    apply(plan_protect(project, [project / "data"], split_command("uv run datanyx")))
    hook = read_json(project / ".claude" / "settings.json")["hooks"]["PreToolUse"][0]["hooks"][0]
    assert (hook["command"], hook["args"]) == ("uv", ["run", "datanyx", "hook", "--require-config"])
    assert read_json(project / ".mcp.json")["mcpServers"]["datanyx"]["args"][:3] == ["run", "datanyx", "mcp"]
    assert split_command('"C:/Program Files/uv/uv.exe" run datanyx') == ["C:/Program Files/uv/uv.exe", "run", "datanyx"]
    with pytest.raises(SourceError):
        split_command("  ")


def test_running_it_again_changes_nothing_and_replaces_old_entries(project):
    configure(project)
    first = (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert plan_protect(project, [project / "data"], ["datanyx"]) == []
    apply(plan_protect(project, [project / "data", project / "data2"], ["datanyx"]))
    settings = read_json(project / ".claude" / "settings.json")
    assert len(settings["hooks"]["PreToolUse"]) == 1 and "Read(data2/**)" in settings["permissions"]["deny"]
    assert first != (project / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_existing_settings_are_kept(project):
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "model": "sonnet",
                "permissions": {"allow": ["Bash(git *)"], "deny": ["Read(.env)"]},
                "hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine"}]}],
                    "Stop": [{"hooks": [{"type": "command", "command": "bye"}]}],
                },
            }
        ),
        encoding="utf-8",
    )
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    configure(project)
    settings = read_json(settings_path)
    assert settings["model"] == "sonnet" and settings["permissions"]["allow"] == ["Bash(git *)"]
    assert settings["permissions"]["deny"][0] == "Read(.env)" and "Read(data/**)" in settings["permissions"]["deny"]
    assert [h["hooks"][0]["command"] for h in settings["hooks"]["PreToolUse"]] == ["mine", "datanyx"]
    assert settings["hooks"]["Stop"]
    assert set(read_json(project / ".mcp.json")["mcpServers"]) == {"other", "datanyx"}


def test_remove_takes_out_only_what_was_added(project):
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(json.dumps({"permissions": {"deny": ["Read(.env)"]}}), encoding="utf-8")
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    configure(project)
    apply(plan_remove(project))
    assert read_json(settings_path) == {"permissions": {"deny": ["Read(.env)"]}}
    assert read_json(project / ".mcp.json") == {"mcpServers": {"other": {"command": "x"}}}
    assert not (project / ".datanyx").exists()
    with pytest.raises(SourceError, match="nothing is protected"):
        plan_remove(project)


def test_remove_deletes_files_that_end_up_empty(project):
    configure(project)
    apply(plan_remove(project))
    assert not (project / ".claude" / "settings.json").exists() and not (project / ".mcp.json").exists()


def test_a_settings_file_that_isnt_json_is_left_alone(project):
    broken = project / ".claude" / "settings.json"
    broken.parent.mkdir()
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SourceError, match="left alone"):
        plan_protect(project, [project / "data"], ["datanyx"])
    assert broken.read_text(encoding="utf-8") == "{ not json"


def test_paths_outside_the_project_use_the_hook_but_not_permission_rules(project, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    apply(plan_protect(project, [project / "data", outside], ["datanyx"]))
    deny = read_json(project / ".claude" / "settings.json")["permissions"]["deny"]
    assert deny == ["Read(data/**)", "Edit(data/**)", "Read(datanyx.local.toml)"]
    stored = read_json(project / ".datanyx" / "protect.json")["paths"]
    assert stored[0] == "data" and Path(stored[1]).is_absolute()
    assert run_hook(payload(project, "Read", file_path=str(outside / "x.csv")), {})


def test_files_and_missing_paths_get_the_right_rules(project):
    apply(plan_protect(project, [project / "data" / "customers.csv", project / "later"], ["datanyx"]))
    deny = read_json(project / ".claude" / "settings.json")["permissions"]["deny"]
    assert "Read(data/customers.csv)" in deny and "Read(later)" in deny and "Read(later/**)" in deny


def test_local_settings_are_kept_out_of_git(project):
    (project / ".git").mkdir()
    apply(plan_protect(project, [project / "data"], ["datanyx"], local=True))
    assert (project / ".claude" / "settings.local.json").exists() and not (project / ".claude" / "settings.json").exists()
    assert ".claude/settings.local.json" in (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert plan_protect(project, [project / "data"], ["datanyx"], local=True) == []


def test_switching_between_shared_and_local_leaves_no_stale_hook(project):
    configure(project)
    apply(plan_protect(project, [project / "data"], ["datanyx"], local=True))
    assert not (project / ".claude" / "settings.json").exists()  # Nothing else was in it, so it went.
    local = read_json(project / ".claude" / "settings.local.json")
    assert len(local["hooks"]["PreToolUse"]) == 1


def test_without_mcp_no_server_is_written(project):
    apply(plan_protect(project, [project / "data"], ["datanyx"], with_mcp=False))
    assert not (project / ".mcp.json").exists()


# --- End to end -----------------------------------------------------------------------------


LAUNCHER = [sys.executable, "-m", "datanyx"]


def test_self_test_runs_the_real_hook(project):
    configure(project)
    assert self_test(project, LAUNCHER, project / "data") is None
    assert "couldn't run" in self_test(project, ["definitely-not-a-program"], project / "data")


def test_hook_command_works_as_a_subprocess(project):
    configure(project)
    result = subprocess.run(
        [*LAUNCHER, "hook"],
        input=payload(project, "Bash", command="cat data/customers.csv"),
        capture_output=True,
        text=True,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project)),
        timeout=60,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_protect_command_dry_run_then_write_then_remove(project, capsys):
    launcher = f'"{sys.executable}" -m datanyx'
    assert main(["protect", str(project / "data"), "--dir", str(project), "--command", launcher]) == 0
    out = capsys.readouterr().out
    assert "would create .claude" in out and "Nothing was changed" in out and not (project / ".datanyx").exists()

    assert main(["protect", str(project / "data"), "--dir", str(project), "--command", launcher, "--write"]) == 0
    assert "Checked: the guard hook refuses protected files." in capsys.readouterr().out
    assert main(["protect", str(project / "data"), "--dir", str(project), "--command", launcher]) == 0
    assert "Already up to date." in capsys.readouterr().out

    assert main(["protect", "--remove", "--dir", str(project), "--write"]) == 0
    assert "Protection removed." in capsys.readouterr().out and not (project / ".datanyx").exists()


def test_protect_command_reports_a_broken_launcher_and_bad_input(project, capsys):
    code = main(["protect", str(project / "data"), "--dir", str(project), "--command", "definitely-not-a-program", "--write"])
    assert code == 1 and "warning: couldn't run" in capsys.readouterr().out
    assert main(["protect", "--dir", str(project)]) == 1
    assert "name at least one" in capsys.readouterr().err
    assert main(["protect", "x", "--remove", "--dir", str(project)]) == 1


# --- Settings files and options that would loosen the policy -----------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "datanyx schema data/customers.csv --keep-name secret",
        "datanyx schema data/customers.csv --keep-name=secret",
        "datanyx synth data/customers.csv --keep-name x",
        "datanyx scrub data/notes.txt --allow jane",
        "datanyx scrub data/notes.txt --keep-numbers",
        "datanyx scrub data/notes.txt --no-ner",
        "datanyx anonymize data/notes.txt --keep Jessica",
        "datanyx anonymize data/notes.txt --keep-entity PERSON",
        "datanyx scan data --show-paths",
        "datanyx scan data --include-examples",
        "datanyx config --reveal",
        "datanyx --config evil.toml schema data/customers.csv",
        "datanyx --no-config schema data/customers.csv",
        "datanyx init",
        "datanyx mcp --allow-urls",
        "uv run datanyx schema data/customers.csv --keep-name x",
        "datanyx run --allow plan -- python src/a.py",
    ],
)
def test_options_that_loosen_the_policy_are_refused_for_the_agent(config, command):
    assert bash(config, command), command


@pytest.mark.parametrize(
    "command",
    [
        "datanyx scan data",
        "datanyx scan data --format json --kinds EMAIL",
        "datanyx diff old.json data/customers.csv",
        "datanyx audit datanyx-audit.jsonl",
        "datanyx config",
        "datanyx doctor",
        "uv run datanyx diff a.json b.json --fail-on never",
    ],
)
def test_reading_commands_stay_available(config, command):
    assert bash(config, command) is None, command


def test_the_local_settings_file_lists_terms_and_cant_be_read(config, project):
    (project / "datanyx.local.toml").write_text('[anonymize]\nhide = ["Acme"]\n', encoding="utf-8")
    assert "lists the terms" in call(config, "Read", file_path=str(project / "datanyx.local.toml"))
    assert "lists the terms" in bash(config, "cat datanyx.local.toml")
    assert bash(config, "head -3 ./datanyx.local.toml")
    assert bash(config, "grep -r Acme .")  # A recursive search would reach it.
    assert call(config, "Grep", path=str(project / "src"), pattern="Acme") is None


def test_the_settings_files_cant_be_changed_even_without_a_local_file(config, project):
    assert call(config, "Read", file_path=str(project / "datanyx.local.toml")) is None  # Nothing there to protect.
    for name in ("datanyx.toml", "datanyx.local.toml"):
        assert "protection" in call(config, "Write", file_path=str(project / name), content="x")
        assert "protection" in call(config, "Edit", file_path=str(project / name))
    assert "protection" in bash(config, "echo '[schema]' > datanyx.toml")
    assert bash(config, "cat datanyx.toml") is None  # The shared policy may be read.


@pytest.mark.parametrize(
    "command",
    [
        "datanyx pseudonymize data/customers.csv -o fake.csv",
        "datanyx pseudonymize data/customers.csv --plan",
        "datanyx eval labelled.json --show-misses",
        "uv run datanyx pseudonymize data/customers.csv -o fake.csv --key-file k.key",
        "datanyx restore out.txt --mapping m.json -o real.txt",
    ],
)
def test_commands_that_read_or_rebuild_real_values_are_not_available_to_the_agent(config, command):
    assert "isn't available" in (bash(config, command) or ""), command


def test_anonymize_of_folders_and_streams_stays_available_but_without_a_mapping(config):
    assert bash(config, "datanyx anonymize data -o clean -r") is None
    assert bash(config, "datanyx anonymize data/notes.txt --stream") is None
    assert bash(config, "datanyx anonymize data -o clean --mapping m.mapping.json")
