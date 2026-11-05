"""The documentation stays in step with the code: a generated reference, working links, every command covered."""

import importlib.util
import re
from pathlib import Path

import pytest

from datanyx.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _commands() -> list[str]:
    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            return sorted(action.choices)
    return []


def _generator():
    spec = importlib.util.spec_from_file_location("gen_cli_reference", ROOT / "tools" / "gen_cli_reference.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_reference_is_up_to_date():
    generator = _generator()
    assert generator.PAGE.read_text(encoding="utf-8") == generator.render(), (
        "docs/cli-reference.md is stale: run `uv run python tools/gen_cli_reference.py`"
    )


def test_every_command_is_in_the_reference_and_explained_somewhere():
    reference = (DOCS / "cli-reference.md").read_text(encoding="utf-8")
    prose = "\n".join(p.read_text(encoding="utf-8") for p in DOCS.glob("*.md") if p.name != "cli-reference.md")
    for command in _commands():
        assert f"## `datanyx {command}`" in reference, command
        assert f"datanyx {command}" in prose, f"no page mentions `datanyx {command}`"


def _links(path: Path):
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            yield from LINK.findall(re.sub(r"`[^`]*`", "", line))


def _anchors(path: Path) -> set[str]:
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6} (.+)$", line)
        if match:
            text = re.sub(r"[^\w\s-]", "", match.group(1).replace("`", "")).strip().lower()
            found.add(re.sub(r"[-\s]+", "-", text))
    return found


@pytest.mark.parametrize("page", [ROOT / "README.md", *sorted(DOCS.glob("*.md"))], ids=lambda p: p.name)
def test_links_point_at_real_files_and_headings(page):
    for target in _links(page):
        if re.match(r"[a-z]+:", target):
            continue
        name, _, anchor = target.partition("#")
        destination = (page.parent / name).resolve() if name else page
        assert destination.exists(), f"{page.name}: {target} points at nothing"
        if anchor and destination.suffix == ".md":
            assert anchor in _anchors(destination), f"{page.name}: no heading #{anchor} in {destination.name}"


def test_every_page_is_in_the_navigation():
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for page in DOCS.glob("*.md"):
        assert page.name in nav, f"{page.name} is not in mkdocs.yml"


def test_readme_stays_short():
    assert len((ROOT / "README.md").read_text(encoding="utf-8").splitlines()) < 150
