"""Each test here is a way past DataNyx's promises that once worked, kept so it can't come back: the scan as a gate, and what ships."""

import re
from pathlib import Path

import pytest

from datanyx.cli import main
from datanyx.scan import scan

ROOT = Path(__file__).resolve().parent.parent


LINE = "jane.real@acme-corp.io,+1 415 555 0132," + "AKIA" + "ABCDEFGHIJKLMNOP\n"


# --- the scan as a gate ---------------------------------------------------------------------


def test_utf16_exports_and_stray_nuls_are_scanned(tmp_path):
    for name, data in (
        ("bom.csv", LINE.encode("utf-16")),
        ("le.csv", LINE.encode("utf-16-le") * 3),
        ("be.csv", LINE.encode("utf-16-be") * 3),
        ("utf32.csv", LINE.encode("utf-32")),
        ("nul.txt", b"\x00" + LINE.encode() * 3),
    ):
        path = tmp_path / name
        path.write_bytes(data)
        assert not scan(str(path)).clean, name


def test_a_file_that_cant_be_checked_fails_the_gate_but_media_doesnt(tmp_path, capsys):
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / "logo.png").write_bytes(bytes(range(256)) * 40)
    assert main(["--no-config", "scan", str(folder), "--fail-on-findings"]) == 0

    (folder / "export.xlsx").write_bytes(bytes(range(256)) * 40)
    assert main(["--no-config", "scan", str(folder), "--fail-on-findings"]) == 1
    assert "export.xlsx" in capsys.readouterr().out
    assert main(["--no-config", "scan", str(folder), "--fail-on-findings", "--allow-unscanned"]) == 0
    (folder / "export.xlsx").unlink()

    (folder / "large.csv").write_text(LINE * 200, encoding="utf-8")
    assert main(["--no-config", "scan", str(folder), "--fail-on-findings", "--max-size", "0.001"]) == 1
    report = scan(str(folder), max_bytes=1000)
    assert report.clean and not report.complete and report.unscanned == ["large.csv"]


def test_noreply_only_excuses_a_noreply_address(tmp_path):
    (tmp_path / "a.txt").write_text("jane.noreply@acme-corp.io\n", encoding="utf-8")
    assert not scan(str(tmp_path)).clean
    (tmp_path / "a.txt").write_text("noreply@acme-corp.io no-reply@acme-corp.io\n", encoding="utf-8")
    assert scan(str(tmp_path)).clean


def test_the_action_ignores_a_config_a_pull_request_can_change():
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert 'CONFIG="--no-config"' in text and "datanyx $CONFIG scan" in text and "datanyx $CONFIG diff" in text


def test_a_repositorys_config_can_be_ignored(tmp_path, monkeypatch):
    (tmp_path / "leak.txt").write_text(LINE, encoding="utf-8")
    (tmp_path / "datanyx.toml").write_text('[scan]\nexclude = ["*"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["scan", ".", "--fail-on-findings"]) == 0  # The file is obeyed when it is trusted...
    assert main(["--no-config", "scan", ".", "--fail-on-findings"]) == 1  # ...and the Action doesn't trust it.


# --- shipping -------------------------------------------------------------------------------


def test_every_action_is_pinned_to_a_commit():
    uses = re.compile(r"uses:\s*(\S+)")
    for path in [*(ROOT / ".github" / "workflows").glob("*.yml"), ROOT / "action.yml"]:
        for reference in uses.findall(path.read_text(encoding="utf-8")):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), f"{path.name}: {reference}"


def test_workflows_start_from_read_only_permissions():
    yaml = pytest.importorskip("yaml")
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["permissions"] == {"contents": "read"}, path.name


def test_the_image_is_built_from_pinned_inputs():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    images = re.findall(r"^FROM (\S+)", text, re.MULTILINE)
    assert images and all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)
    assert "uv.lock" in text and "--frozen" in text and "--require-hashes" in text
    assert (ROOT / ".github" / "dependabot.yml").exists()
