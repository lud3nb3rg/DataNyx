"""Each way around the guard that has been found, so it stays closed, and what must stay allowed."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from datanyx.cli import main
from datanyx.protect import ProtectConfig, apply, decide, plan_protect, plan_remove


@pytest.fixture
def project(tmp_path):
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data2").mkdir()
    (root / "src").mkdir()
    (root / "data" / "customers.csv").write_text("id,email\n1,a@x.com\n", encoding="utf-8")
    (root / "data" / "notes.txt").write_text("hello", encoding="utf-8")
    (root / "data2" / "other.csv").write_text("id\n1\n", encoding="utf-8")
    (root / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    return root


@pytest.fixture
def config(project):
    return ProtectConfig(project, (project / "data",))


def call(config, tool, cwd=None, **tool_input):
    return decide({"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or config.root)}, config)


def bash(config, command):
    return call(config, "Bash", command=command)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- Shell syntax ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "datanyx doctor & cat data/customers.csv",
        "echo hi |& cat data/customers.csv",
        "echo $(cat data/customers.csv)",
        'echo "$(cat data/customers.csv)"',
        "echo `cat data/customers.csv`",
        "(cat data/customers.csv)",
        "{ cat data/customers.csv; }",
        "cat {data,src}/customers.csv",
        "diff <(cat data/customers.csv) src/a.py",
        "cat<data/customers.csv",
        'cat da"ta"/customers.csv',
        "cat da'ta'/customers.csv",
        r"cat da\ta/customers.csv",
        "dd if=data/customers.csv",
        "grep -fdata/notes.txt src/a.py",
        "curl -d @data/customers.csv http://localhost",
        "bash -c 'cat data/customers.csv'",
        "sh -lc 'cat data/customers.csv'",
        "eval 'cat data/customers.csv'",
        "cmd /c type data/customers.csv",
        "pwsh -Command Get-Content data/customers.csv",
        "bash <<EOF\ncat data/customers.csv\nEOF",
        "python <<EOF\nopen('data/customers.csv')\nEOF",
        "bash <<< 'cat data/customers.csv'",
        "if true; then cat data/customers.csv; fi",
        "(cd /tmp); cat data/customers.csv",
        "cat $PWD/data/customers.csv",
        "cat ${PWD}/data/customers.csv",
        "datanyx doctor; (cd data && cat customers.csv)",
    ],
)
def test_shell_syntax_doesnt_hide_a_reference(config, command):
    assert bash(config, command), command


def test_an_encoded_powershell_command_cant_be_checked(config):
    assert "encoded" in bash(config, "powershell -enc ZWNobyBoaQ==")
    assert "encoded" in call(config, "PowerShell", command="pwsh -EncodedCommand ZWNobyBoaQ==")


@pytest.mark.parametrize(
    "command",
    [
        "echo hi 2>&1",
        "echo hi >/dev/null 2>&1",
        "echo hi &> out.log",
        "echo hi # data/customers.csv",
        "cat <<EOF\nthis mentions data\nEOF",
        "ls src && echo done &",
        "bash -c 'ls src'",
        "(ls src)",
        "echo $(ls src)",
        "find src -name '*.py' -not -name 'x*'",
        "sudo -u me ls src",
    ],
)
def test_shell_syntax_alone_is_not_a_reason_to_refuse(config, command):
    assert bash(config, command) is None, command


def test_powershell_backticks_are_escapes_not_commands(config):
    assert call(config, "PowerShell", command="Get-Content da`ta/customers.csv")
    assert call(config, "PowerShell", command="Write-Output 'hello'") is None


# --- What counts as running datanyx -------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python -c \"print(open('data/customers.csv').read())\" -m datanyx schema",
        "python evil.py data/customers.csv -m datanyx schema",
        "uv run cat data/customers.csv datanyx doctor",
        "uvx cat data/customers.csv datanyx",
        "pipx run cat data/customers.csv datanyx",
        "echo datanyx schema data/customers.csv",
    ],
)
def test_naming_datanyx_somewhere_doesnt_make_it_a_datanyx_command(config, command):
    assert bash(config, command), command


@pytest.mark.parametrize(
    "command",
    [
        "uv run python -m datanyx schema data/customers.csv",
        "py -3 -m datanyx schema data/customers.csv",
        "python3.12 -X utf8 -m datanyx schema data/customers.csv",
        "uvx --from datanyx datanyx schema data/customers.csv",
        "pipx run datanyx schema data/customers.csv",
    ],
)
def test_the_usual_ways_of_starting_datanyx_still_work(config, command):
    assert bash(config, command) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "datanyx run cat data/customers.csv",
        "datanyx run --stdout scrubbed cat data/customers.csv",
        "datanyx run --stdout=scrubbed cat data/customers.csv",
        "datanyx run -- bash -c 'cat data/customers.csv'",
        "datanyx run -- git show HEAD:data/customers.csv",
        "datanyx run -- rm -rf .datanyx",
    ],
)
def test_datanyx_run_checks_the_command_it_starts(config, command):
    assert bash(config, command), command


def test_datanyx_run_without_a_separator_still_runs_ordinary_commands(config):
    assert bash(config, "datanyx run python src/a.py") is None
    assert bash(config, "datanyx run --names-from data/customers.csv python src/a.py") is None


@pytest.mark.parametrize(
    "command",
    [
        "datanyx config --rev",
        "datanyx scan data --show-p",
        "datanyx scan data --include",
        "datanyx anonymize data/notes.txt --keep-ent PERSON",
        "datanyx anonymize data/notes.txt --map m.json",
        "datanyx run --save-std=out.txt -- python src/a.py",
        "datanyx --no-c schema data/customers.csv",
    ],
)
def test_shortened_weakening_options_are_refused(config, command):
    assert bash(config, command), command


@pytest.mark.parametrize(
    "arguments",
    [["config", "--rev"], ["--no-c", "doctor"], ["anonymize", "-", "--keep-ent", "PERSON"], ["scan", ".", "--show-p"]],
)
def test_the_command_line_doesnt_accept_shortened_options(arguments):
    with pytest.raises(SystemExit):
        main(arguments)


def test_datanyx_output_cant_overwrite_protected_data(config):
    assert "protected" in bash(config, "datanyx schema src/a.py > data/customers.csv")


# --- Changing the setup ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf .datanyx",
        "rm -rf .claude",
        "rm -rf ./.datanyx/",
        "rm -rf .",
        "rmdir .datanyx",
        "mv .datanyx elsewhere",
        "mv .claude elsewhere",
        "env rm -rf .datanyx",
        "sudo -u me rm -rf .datanyx",
        "find . -delete",
        "find .datanyx -delete",
        "python -c \"import shutil; shutil.rmtree('.datanyx')\"",
        "cat /dev/null > .datanyx/protect.json",
        "cat /dev/null >.datanyx/protect.json",
        "cat /dev/null >| .mcp.json",
        "cat src/a.py >> .claude/settings.json",
        "grep x src/a.py &> .mcp.json",
        ": > .dat'anyx'/protect.json",
        "cd .datanyx && rm protect.json",
        "cd .claude; mv settings.json x",
        "Remove-Item -Recurse .datanyx",
        "git checkout HEAD -- .claude/settings.json",
        "git checkout -- .",
        "git restore .datanyx/protect.json",
        "git restore .",
        "git rm -r .claude",
        "git clean -fdx",
        "git reset --hard",
        "git stash -u",
    ],
)
def test_the_setup_cant_be_removed_or_overwritten(config, command):
    assert "protection" in (bash(config, command) or ""), command


def test_a_glob_cant_take_the_setup_either(config, project):
    (project / ".datanyx").mkdir()
    (project / ".datanyx" / "protect.json").write_text("{}", encoding="utf-8")
    assert "protection" in (bash(config, "rm -rf .dat*") or "")
    assert "protection" in (bash(config, "rm .datanyx/*.json") or "")


@pytest.mark.parametrize(
    "command",
    [
        "mv /tmp/x .",
        "rm src/a.py",
        "rm -rf src",
        "git add .",
        'git commit -m "Update .claude/settings.json"',
        "git checkout main",
        "git restore src/a.py",
        "git clean -fd src",
        "git stash",
        "ls .claude",
        "python .claude/skills/x/run.py",
    ],
)
def test_ordinary_changes_elsewhere_are_left_alone(config, command):
    assert bash(config, command) is None, command


# --- Reading through git ------------------------------------------------------------------------------


@pytest.fixture
def repository(project):
    def run(*arguments):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *arguments], cwd=project, check=True, capture_output=True
        )

    run("init", "-q")
    run("add", "data", "src")
    run("commit", "-qm", "first")
    return project


@pytest.mark.parametrize(
    "command",
    [
        "git show HEAD:data/customers.csv",
        "git show HEAD:./data/customers.csv",
        "git show :data/customers.csv",
        "git show :0:data/customers.csv",
        "git cat-file -p HEAD:data/customers.csv",
        "git grep foo",
        "git grep -e foo",
        "git grep foo -- .",
        "git blame data/customers.csv",
    ],
)
def test_git_cant_print_protected_files(config, command):
    assert bash(config, command), command


def test_git_commands_that_print_everything_are_refused_only_when_git_holds_the_data(config, repository):
    for command in ("git diff", "git show", "git show HEAD", "git log -p", "git archive HEAD", "git format-patch -1", "git -C src diff"):
        assert "narrower folder" in (bash(config, command) or ""), command
    for command in ("git diff -- src", "git show HEAD --stat", "git log --oneline", "git log -p -- src", "git status", "git diff --name-only"):
        assert bash(config, command) is None, command


def test_git_commands_are_fine_when_the_data_isnt_in_git(config):
    assert bash(config, "git diff") is None
    assert bash(config, "git show HEAD") is None
    assert bash(config, "git grep foo -- src") is None


# --- Windows paths ---------------------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_git_bash_paths_are_understood_on_windows(config, project):
    posix = (project / "data" / "customers.csv").as_posix()
    drive = posix[0]
    for form in (f"/{drive}{posix[2:]}", f"/{drive.upper()}{posix[2:]}", f"/cygdrive/{drive}{posix[2:]}", f"/mnt/{drive}{posix[2:]}"):
        assert bash(config, f"cat {form}"), form
        assert call(config, "Read", file_path=form), form
    assert bash(config, f"""python -c "open('/{drive}{posix[2:]}')" """)


# --- Glob ---------------------------------------------------------------------------------------------------


def test_glob_refuses_patterns_that_would_list_protected_files(config, project):
    for pattern in ("**/*.csv", "**", "**/*", "*/customers.csv", "d*/*", "{data,src}/*.csv", "data/?otes.txt"):
        assert call(config, "Glob", pattern=pattern), pattern
        assert call(config, "Glob", path=str(project), pattern=pattern), pattern
    assert call(config, "Glob", pattern=f"{project.as_posix()}/**/*.csv")


def test_glob_is_left_alone_when_nothing_protected_would_match(config):
    for pattern in ("**/*.py", "src/**", "*.toml", "data2/*.csv", "{src,data2}/*"):
        assert call(config, "Glob", pattern=pattern) is None, pattern


def test_glob_never_lists_protected_files_even_when_searches_may_go_broad(project):
    relaxed = ProtectConfig(project, (project / "data",), block_broad_search=False)
    assert call(relaxed, "Glob", pattern="**/*.py") is None
    assert call(relaxed, "Glob", pattern="**/*.csv")  # Listing protected files stays refused: names can be data.
    assert call(relaxed, "Glob", pattern="data/*.csv")


def test_glob_protects_a_single_protected_file(project):
    only_file = ProtectConfig(project, (project / "data" / "customers.csv",))
    assert call(only_file, "Glob", pattern="**/*.csv")
    assert call(only_file, "Glob", pattern="data/*.txt") is None


# --- Deny rules ---------------------------------------------------------------------------------------------


def test_rules_for_paths_no_longer_protected_dont_stay_behind(project):
    apply(plan_protect(project, [project / "data"], ["datanyx"]))
    apply(plan_protect(project, [project / "data2"], ["datanyx"]))
    deny = read_json(project / ".claude" / "settings.json")["permissions"]["deny"]
    assert "Read(data/**)" not in deny and "Read(data2/**)" in deny
    apply(plan_remove(project))
    assert not (project / ".claude" / "settings.json").exists()


def test_a_path_that_changed_shape_leaves_no_rule_behind(project):
    apply(plan_protect(project, [project / "later"], ["datanyx"]))
    (project / "later").mkdir()
    apply(plan_remove(project))
    assert not (project / ".claude" / "settings.json").exists()


def test_replacing_protection_keeps_rules_the_user_wrote(project):
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(json.dumps({"permissions": {"deny": ["Read(.env)"]}}), encoding="utf-8")
    apply(plan_protect(project, [project / "data"], ["datanyx"]))
    apply(plan_protect(project, [project / "data2"], ["datanyx"]))
    assert read_json(settings_path)["permissions"]["deny"][0] == "Read(.env)"
