"""Each test here is a way past DataNyx's promises that once worked, kept so it can't come back: the Claude Code guard."""

import json
import os
from pathlib import Path

import pytest

from datanyx.cli import build_parser
from datanyx.protect import ProtectConfig, decide, run_hook

# --- the guard ------------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "x.csv").write_text("name\nJane\n", encoding="utf-8")
    (root / "secret.csv").write_text("name\nJane\n", encoding="utf-8")
    (root / ".datanyx").mkdir()
    (root / ".datanyx" / "protect.json").write_text(json.dumps({"version": 1, "paths": ["data", "secret.csv"]}), encoding="utf-8")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    return root


@pytest.fixture
def config(project):
    return ProtectConfig(project, (project / "data", project / "secret.csv"))


def bash(config, command):
    return decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(config.root)}, config)


@pytest.mark.parametrize(
    "command",
    [
        "ls -m datanyx schema data",
        "less -m datanyx schema data/x.csv",
        "python -c \"print(open('data/x.csv').read())\" -m datanyx schema",
        "python -c \"print(open('data/x.csv').read())\" datanyx schema",
        "python evil.py datanyx schema data/x.csv",
    ],
)
def test_merely_mentioning_datanyx_earns_no_allowance(config, command):
    assert bash(config, command), command


@pytest.mark.parametrize(
    "command",
    [
        "datanyx run cp data/x.csv /tmp/leak.csv",
        "datanyx run python -c \"print(open('data/x.csv').read())\"",
        "datanyx run cat data/x.csv -- anything",
    ],
)
def test_run_checks_its_command_with_or_without_the_separator(config, command):
    assert bash(config, command), command


def test_abbreviated_options_dont_exist():
    parser = build_parser()
    for arguments in (
        ["config", "--rev"],
        ["anonymize", "--mapp", "m.json", "f"],
        ["run", "--save-stdo=o", "--", "x"],
        ["schema", "f", "--keep-n=a"],
        ["scan", "d", "--show"],
        ["--conf=e.toml", "doctor"],
        ["--no-c", "doctor"],
        ["scrub", "f", "--no-n"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(arguments)


def test_every_parser_refuses_abbreviations():
    parser = build_parser()
    commands = next(action for action in parser._actions if isinstance(action.choices, dict))
    assert not parser.allow_abbrev
    assert all(not sub.allow_abbrev for sub in commands.choices.values())


@pytest.mark.parametrize(
    "command",
    [
        "DATANYX_CONFIG=evil.toml datanyx schema data/x.csv",
        "export DATANYX_CONFIG=evil.toml",
        "$env:DATANYX_CONFIG='evil.toml'",
        "datanyx scrub data/x.csv --names-from guess.json",
        "datanyx scrub data/x.csv --detectors weak.toml",
        "datanyx scrub data/x.csv --model ./blank",
        "datanyx scrub data/x.csv --backend gliner:someone/else",
        "datanyx anonymize data/x.csv --hide guess",
        "datanyx anonymize data -o clean --keep-names",
        "datanyx scan data --allow-unscanned",
        "datanyx mcp --allow-env PROD_URL",
    ],
)
def test_options_and_settings_that_loosen_protection_are_refused(config, command):
    assert bash(config, command), command


def test_names_may_come_from_the_protected_data_itself(config):
    assert bash(config, "datanyx run --names-from data/x.csv -- python src/a.py") is None
    assert bash(config, "datanyx scrub log.txt --names-from=data/x.csv") is None


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'cat data/x.csv'",
        'sh -c "cat secret.csv"',
        "bash -lc 'echo hi; cat secret.csv'",
        "pwsh -Command Get-Content secret.csv",
        "eval cat secret.csv",
        "cat da''ta/x.csv",
        'cat "da"ta/x.csv',
        "cat d\\ata/x.csv",
        "cat 0<secret.csv",
        "echo $(cat secret.csv)",
        'echo "$(cat secret.csv)"',
        "echo `cat secret.csv`",
        "(cat secret.csv)",
        "{ cat secret.csv; }",
        "cat secret.csv&",
        "cat secret.csv>/tmp/o",
        "cat secret.csv 2>&1",
        "diff <(cat secret.csv) /dev/null",
        "echo $(echo $(cat secret.csv))",
        "if true; then cat secret.csv; fi",
    ],
)
def test_shell_syntax_doesnt_hide_a_protected_path(config, command):
    assert bash(config, command), command


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf .datanyx",
        "mv .datanyx x",
        "rm .datanyx/*",
        "rm .dat*/protect.json",
        "cd .datanyx && rm protect.json",
        "mv .claude .c",
        "cd .claude && echo {} > settings.json",
        "echo {} > ./.claude/./settings.json",
        "cat /dev/null > .claude/settings.json",
        "grep . src/a.py > .claude/settings.json",
        "git rm -f .claude/settings.json",
        "git checkout HEAD~1 -- .datanyx/protect.json",
        "git -C . rm .mcp.json",
        "git clean -fdx",
        "git stash -u",
        "cp evil.json .claude/settings.json",
        "bash -c 'rm -rf .datanyx'",
        "rm -rf .",
        "rm -rf ../proj",
        "echo '[schema]' > sub/datanyx.toml",
        "mv x.toml datanyx.toml",
        "mv x sub/datanyx.local.toml",
        "echo {} > ~/.claude/settings.json",
        "touch ~/.claude/settings.local.json",
    ],
)
def test_the_protection_cant_be_removed_from_the_shell(config, command):
    assert bash(config, command), command


@pytest.mark.parametrize(
    "command",
    [
        "git status", "git diff", "git log", "git add src/a.py", 'git commit -m "fix"', "git stash", "git checkout -b x",
        "cat .claude/settings.json", "cat .datanyx/protect.json", "git diff .claude/settings.json", "ls .claude",
        "cp src/a.py .", "pytest .", "ruff check .", "find . -name '*.py'", "echo it's fine", "python src/a.py 2>&1 | tee out.txt",
        "echo hi > out.txt", "cd src && rm old.py", "rm -rf build", "bash -c 'echo hi'",
        'python -c "print(1); print(2)"', "awk '{print $1}' src/a.py", "echo $(date)",
    ],
)
def test_ordinary_work_is_still_allowed(config, command):
    assert bash(config, command) is None, command


@pytest.mark.parametrize(
    ("tool", "inputs"),
    [
        ("Write", {"file_path": "sub/datanyx.toml", "content": ""}),
        ("Write", {"file_path": "sub/datanyx.local.toml", "content": ""}),
        ("Edit", {"file_path": str(Path.home() / ".claude" / "settings.json")}),
        ("Glob", {"pattern": "**/*.csv"}),
        ("Glob", {"pattern": "da*/x.csv"}),
    ],
)
def test_other_tools_cant_reach_the_setup_or_list_the_data(config, tool, inputs):
    assert decide({"tool_name": tool, "tool_input": inputs, "cwd": str(config.root)}, config), inputs


def test_a_glob_that_misses_the_data_is_fine(config):
    assert decide({"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}, "cwd": str(config.root)}, config) is None


def test_the_running_install_cant_be_replaced(config):
    import datanyx

    package = Path(datanyx.__file__).parent
    assert decide({"tool_name": "Edit", "tool_input": {"file_path": str(package / "protect" / "guard.py")}, "cwd": str(config.root)}, config)
    assert bash(config, f"rm {(package / 'protect' / 'guard.py').as_posix()}")


def test_datanyx_can_be_used_to_overwrite_neither_data_nor_setup(config):
    for command in (
        "datanyx schema data/x.csv -o data/x.csv",
        "datanyx schema secret.csv --output=secret.csv",
        "datanyx schema secret.csv -osecret.csv",
        "datanyx mcp --root . --audit-log data/x.csv",
        "datanyx schema secret.csv -o .claude/./settings.json",
    ):
        assert bash(config, command), command


def test_a_hook_installed_by_protect_refuses_everything_once_its_settings_are_gone(project):
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "data/x.csv"}, "cwd": str(project)})
    environment = {"CLAUDE_PROJECT_DIR": str(project)}
    assert run_hook(payload, environment, require_config=True)
    (project / ".datanyx" / "protect.json").unlink()
    (project / ".datanyx").rmdir()
    assert "missing" in run_hook(payload, environment, require_config=True)
    other = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(project)})
    assert "missing" in run_hook(other, environment, require_config=True)
    assert run_hook(payload, environment) == ""  # A hook not installed by protect has nothing to enforce.


def test_protect_warns_about_a_claude_code_too_old_to_fail_closed(monkeypatch):
    from datanyx.protect import settings

    class Result:
        stdout = "2.1.100 (Claude Code)"

    monkeypatch.setattr(settings.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(settings.subprocess, "run", lambda *a, **k: Result())
    assert "older" in settings.claude_code_warning()
    Result.stdout = "2.1.295 (Claude Code)"
    assert settings.claude_code_warning() is None
    monkeypatch.setattr(settings.shutil, "which", lambda _: None)
    assert "2.1.295" in settings.claude_code_warning()
