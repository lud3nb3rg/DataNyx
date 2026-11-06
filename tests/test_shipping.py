"""The files that ship DataNyx (action, hook, image, workflows) stay valid and in step with the program."""

import re
import sys
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


def test_every_action_is_pinned_to_a_commit():
    pinned = re.compile(r"uses:\s*[\w./-]+@[0-9a-f]{40}\b")
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "uses:" in line and "./" not in line.split("uses:")[1].split()[0]:
                assert pinned.search(line), f"{path.name}: pin this to a commit: {line.strip()}"


def test_release_workflow_attests_signs_and_publishes_a_release_only_from_a_tag():
    release = load(ROOT / ".github" / "workflows" / "release.yml")
    jobs = release["jobs"]
    assert "workflow_dispatch" in release[True]  # A dry run builds everything and publishes nothing.
    for name in ("pypi", "attest", "release"):
        assert jobs[name]["if"] == "github.ref_type == 'tag'", name
    assert jobs["attest"]["permissions"] == {"contents": "read", "id-token": "write", "attestations": "write"}
    assert jobs["release"]["permissions"] == {"contents": "write"}
    docker = str(jobs["docker"])
    assert "provenance" in docker and "sbom" in docker and "cosign sign" in docker
    assert jobs["docker"]["permissions"]["id-token"] == "write"
    assert set(jobs["release"]["needs"]) == {"build", "pypi", "attest", "docker"}  # Nothing is announced before it is all out.


def test_the_docs_site_is_built_strictly_and_deployed_from_main():
    pages = load(ROOT / ".github" / "workflows" / "pages.yml")
    assert pages[True]["push"]["branches"] == ["main"]
    assert pages["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert "mkdocs build --strict" in str(pages["jobs"]["build"])
    assert "site_url: https://lud3nb3rg.github.io/DataNyx/" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


def test_release_notes_come_from_the_changelog():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from release_notes import notes_for
    finally:
        sys.path.pop(0)
    text = "# Changelog\n\n## Unreleased\n\n- next\n\n## 1.2.0 (2030-01-01)\n\nIntro.\n\n- one\n- two\n\n## 1.1.0\n\n- old\n"
    assert notes_for("1.2.0", text) == "Intro.\n\n- one\n- two"
    assert notes_for("1.1.0", text) == "- old"
    assert notes_for("1.3.0", text) == ""  # Missing: the script refuses, so no release goes out blank.
    assert notes_for("1.2", text) == ""  # Not a prefix match.
    real = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert notes_for("0.1.0", real).startswith("The first release.")
    assert real.count("\n## 0.1.0") == 1  # One section per version.


def test_the_package_points_at_its_documentation():
    urls = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["urls"]
    assert urls["Documentation"].startswith("https://lud3nb3rg.github.io/DataNyx")
    assert urls["Changelog"].endswith("CHANGELOG.md")
