"""The files that ship DataNyx (action, hook, image, workflows) stay valid and in step with the program."""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
tomllib = pytest.importorskip("tomllib" if __import__("sys").version_info >= (3, 11) else "tomli")

from datanyx.cli import build_parser, main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", [*WORKFLOWS, ROOT / "action.yml", ROOT / ".pre-commit-hooks.yaml"], ids=lambda p: p.name)
def test_yaml_files_parse(path):
    assert load(path)


def test_workflows_use_current_actions_and_pinned_runners():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert "actions/checkout@v4" not in text and "setup-uv@v5" not in text, path.name
        assert "ubuntu-latest" not in text, f"{path.name}: pin the runner"


def test_release_workflow_publishes_by_trusted_publishing_from_a_tag():
    release = load(ROOT / ".github" / "workflows" / "release.yml")
    assert release[True]["push"]["tags"] == ["v*"]  # YAML reads the key `on` as True.
    assert release["jobs"]["pypi"]["permissions"] == {"id-token": "write"}
    assert "password" not in str(release["jobs"]["pypi"]).lower()  # No stored PyPI token.


def test_pre_commit_hook_runs_a_real_command():
    (hook,) = load(ROOT / ".pre-commit-hooks.yaml")
    command = hook["entry"].split()
    assert command[:2] == ["datanyx", "scan"]
    args = build_parser().parse_args([*command[1:], "a.py", "b.py"])
    assert args.fail_on_findings and args.path == ["a.py", "b.py"]


def test_action_inputs_reach_a_valid_command():
    action = load(ROOT / "action.yml")
    assert set(action["inputs"]) >= {"command", "path", "args", "version"}
    run = next(step["run"] for step in action["runs"]["steps"] if step.get("name") == "Run datanyx")
    assert "${{" not in run  # Inputs go through the environment so they can't inject commands.
    parser = build_parser()
    assert parser.parse_args(["scan", "src", "--fail-on-findings"]).fail_on_findings
    assert parser.parse_args(["diff", "old.json", "new.json", "--fail-on", "breaking"]).fail_on == "breaking"
    assert "diff $TARGETS --fail-on breaking" in run and "scan $TARGETS --fail-on-findings" in run


def test_dockerfile_runs_as_a_non_root_user_with_datanyx_as_the_entrypoint():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["datanyx"]' in text
    assert re.search(r"^USER datanyx$", text, re.MULTILINE)
    assert (ROOT / ".dockerignore").exists()


def test_the_package_version_is_a_release_version():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_http_mode_is_in_the_command_reference_and_needs_a_token(monkeypatch, capsys):
    reference = (ROOT / "docs" / "cli-reference.md").read_text(encoding="utf-8")
    assert "`--http` `HOST:PORT`" in reference and "`--token-env` `NAME`" in reference
    monkeypatch.delenv("DATANYX_MCP_TOKEN", raising=False)
    assert main(["--no-config", "mcp", "--http", "127.0.0.1:1"]) == 1
    assert "DATANYX_MCP_TOKEN" in capsys.readouterr().err
