"""The scan command: what it reads, what it flags, and how a report says it is incomplete."""

import json

import pytest

from datanyx.cli import main
from datanyx.scan import render_report, scan
from datanyx.text import PatternDetector

# --- Scan ----------------------------------------------------------------------------------------------------------------


def test_utf16_files_are_read(tmp_path):
    for encoding in ("utf-16", "utf-32"):
        (tmp_path / f"{encoding}.csv").write_text("email\na.person@corp.io\n", encoding=encoding)
    report = scan(str(tmp_path))
    assert report.scanned == 2 and report.totals["EMAIL"] == 2 and report.binary == 0


def test_a_report_says_when_it_isnt_complete(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 2000, encoding="utf-8")
    report = scan(str(tmp_path), max_bytes=1000)
    assert report.clean and not report.complete
    text = render_report(report)
    assert "No personal data" not in text and "weren't" in text
    assert json.loads(render_report(report, "json"))["complete"] is False


def test_a_binary_file_that_isnt_data_doesnt_spoil_a_clean_result(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    report = scan(str(tmp_path))
    assert report.clean and report.complete and "No personal data" in render_report(report)


def test_ci_fails_on_what_it_couldnt_read(tmp_path, capsys):
    (tmp_path / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")
    assert main(["--no-config", "scan", str(tmp_path), "--fail-on-findings", "--max-size", "1"]) == 1
    (tmp_path / "big.txt").unlink()
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    assert main(["--no-config", "scan", str(tmp_path), "--fail-on-findings"]) == 0
    capsys.readouterr()


def test_an_unreadable_file_is_counted(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    real_open = open

    def refusing(path, *args, **kwargs):
        if str(path).endswith("a.txt"):
            raise PermissionError
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", refusing)
    report = scan(str(tmp_path))
    assert report.unscanned == ["a.txt"] and not report.complete


@pytest.mark.parametrize("name", ["x.duckdb", "x.ddb", "x.zsav", "x.por", "x.xpt"])
def test_data_formats_the_project_reads_are_flagged(tmp_path, name):
    (tmp_path / name).write_bytes(bytes(range(256)) * 4)
    assert scan(str(tmp_path)).not_scanned_data == [name]


def test_a_data_file_too_large_to_scan_is_listed(tmp_path):
    (tmp_path / "x.db").write_bytes(b"x" * 2000)
    report = scan(str(tmp_path), max_bytes=1000)
    assert report.not_scanned_data == ["x.db"] and report.too_large == 1


def test_excludes_apply_to_files_named_one_by_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fixtures").mkdir()
    for path in ("uv.lock", "fixtures/a.txt", "real.txt"):
        (tmp_path / path).write_text("a.person@corp.io", encoding="utf-8")
    report = scan(["uv.lock", "fixtures/a.txt", "real.txt"], excludes=["*.lock", "fixtures"])
    assert report.scanned == 1 and [item.display for item in report.findings] == ["real.txt"]
    assert scan("uv.lock", excludes=["*.lock"]).scanned == 0


def test_where_a_checkout_lives_is_no_reason_to_skip_a_file(tmp_path, monkeypatch):
    project = tmp_path / "build" / "proj"
    project.mkdir(parents=True)
    (project / "a.txt").write_text("a.person@corp.io", encoding="utf-8")
    monkeypatch.chdir(project)
    assert scan(str(project / "a.txt")).scanned == 1
    assert scan("a.txt").scanned == 1


def test_line_numbers_stay_right(tmp_path):
    (tmp_path / "a.txt").write_text("one\nsecond a@corp.io\n\nfourth b@corp.io\n", encoding="utf-8")
    assert scan(str(tmp_path)).findings[0].lines["EMAIL"] == [2, 4]


class FakeBackend:
    uses_model = True

    def find(self, text):
        from datanyx.text import Span

        at = text.find("123456789")
        return [Span(at, at + 9, "SSN")] if at >= 0 else []


def test_what_a_backend_reports_isnt_filtered_away(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("id 123456789 on 2024-01-02", encoding="utf-8")
    monkeypatch.setattr("datanyx.cli.scan._backend_detectors", lambda args: [FakeBackend()])
    assert main(["--no-config", "scan", str(tmp_path), "--format", "json"]) == 0
    totals = json.loads(capsys.readouterr().out)["totals"]
    assert totals["SSN"] == 1 and "DATE" not in totals


def test_a_backend_kind_is_reported_but_the_patterns_stay_quiet(tmp_path):
    from datanyx.text.backends import BACKEND_KINDS

    (tmp_path / "a.txt").write_text("id 123456789 on 2024-01-02", encoding="utf-8")
    report = scan(str(tmp_path), detectors=[PatternDetector(), FakeBackend()], kinds={"EMAIL"}, backend_kinds=BACKEND_KINDS)
    assert report.totals["SSN"] == 1 and report.totals["DATE"] == 0


def test_a_backend_kind_can_be_asked_for(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("id 123456789", encoding="utf-8")
    monkeypatch.setattr("datanyx.cli.scan._backend_detectors", lambda args: [FakeBackend()])
    assert main(["--no-config", "scan", str(tmp_path), "--kinds", "SSN", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["SSN"] == 1
