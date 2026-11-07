"""Each test here is a way past DataNyx's promises that once worked, kept so it can't come back: what can hang, and what leaves the machine or lands in logs."""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from datanyx import extract, render
from datanyx.audit import read_log, summarize, summarize_log
from datanyx.scan import scan
from datanyx.scrub import ScrubPolicy, scrub
from datanyx.sources import SourceError
from datanyx.text import PatternDetector, SecretDetector
from datanyx.text.backends import OllamaDetector

ROOT = Path(__file__).resolve().parent.parent


# --- what can hang ----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["a" * 500_000, "a-" * 250_000, "1 " * 250_000, "a." * 250_000, "@" * 500_000, "a@" * 250_000, "1." * 250_000, "x://" * 100_000, "password=" * 50_000, "password " * 50_000, "a:" * 250_000, "'a" * 250_000, "x://a:" * 50_000])
def test_detectors_take_linear_time_on_hostile_text(text):
    started = time.monotonic()
    list(PatternDetector().find(text))
    list(SecretDetector().find(text))
    scrub(text, ScrubPolicy(detectors=()))
    assert time.monotonic() - started < 30, text[:8]


def test_the_patterns_still_find_what_they_did():
    def found(text):
        return {text[span.start : span.end] for span in PatternDetector().find(text)}

    assert "jane.doe+tag@sub.acme-corp.io" in found("mail jane.doe+tag@sub.acme-corp.io now")
    assert "https://a.example/x?y=1" in found("see https://a.example/x?y=1.")
    assert "www.example.org/a" in found("www.example.org/a")
    assert "1,234.50 USD" in found("cost 1,234.50 USD today")
    assert "AB-12345" in found("ref AB-12345 ok")
    assert not {"plain-words-only"} & found("plain-words-only")
    assert "ABC_123_DEF" in found("id ABC_123_DEF.")


def test_a_scan_with_many_findings_is_fast(tmp_path):
    (tmp_path / "a.txt").write_text("jane.real@acme-corp.io\n" * 150_000, encoding="utf-8")
    started = time.monotonic()
    report = scan(str(tmp_path), kinds=["EMAIL"], max_bytes=10_000_000)
    assert report.findings[0].counts["EMAIL"] == 150_000 and report.findings[0].lines["EMAIL"][:2] == [1, 2]
    assert time.monotonic() - started < 120  # Quadratic behaviour would take far longer; this allows for a loaded machine.


def test_a_yaml_alias_bomb_is_refused(tmp_path):
    pytest.importorskip("yaml")
    levels = ["a0: &a0 [x,x,x,x,x,x,x,x,x,x]"] + [f"a{i}: &a{i} [{','.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 12)]
    path = tmp_path / "bomb.yaml"
    path.write_text("\n".join(levels) + "\n", encoding="utf-8")
    code = (
        "import sys;from datanyx import extract;from datanyx.sources import SourceError\n"
        "try:\n    extract(sys.argv[1])\nexcept SourceError as error:\n    print(error)\n"
    )
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=60, env=environment)
    assert "too many values" in result.stdout


def test_ordinary_yaml_aliases_and_self_references_are_handled(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "a.yaml").write_text("base: &b {x: 1}\none: *b\ntwo: *b\n", encoding="utf-8")
    assert "x" in render(extract(str(tmp_path / "a.yaml")), "text")
    (tmp_path / "s.yaml").write_text("a: &a [*a]\n", encoding="utf-8")
    with pytest.raises(SourceError, match="nested"):
        extract(str(tmp_path / "s.yaml"))


# --- leaving the machine, and the logs ------------------------------------------------------


def test_a_proxy_setting_cant_send_text_for_the_local_model_elsewhere(monkeypatch):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"response": json.dumps({"items": []})}).encode())

        def log_message(self, *_):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{server.server_address[1]}")
        with pytest.raises(SourceError):  # Nothing is listening on the real local port.
            list(OllamaDetector(url="http://127.0.0.1:9").find("SECRET text"))
        assert seen == []
    finally:
        server.shutdown()


@pytest.mark.parametrize("url", ["http://localhost@attacker.example/", "http://attacker.example/", "file:///etc/passwd", "ftp://localhost/"])
def test_only_a_local_web_address_is_accepted_for_the_local_model(url):
    with pytest.raises(SourceError):
        OllamaDetector(url=url)


@pytest.mark.parametrize(
    "url",
    ["postgresql://u:hunter2@h/db", "postgresql://:hunter2@h/db", "postgresql://u@h/db?password=hunter2", "mongodb://h/db?authSource=a&password=hunter2", "https://hunter2@github.com/x", "x://h/?sslpassword=hunter2&a=1"],
)
def test_credentials_in_a_url_never_reach_the_log_or_a_scrubbed_message(url):
    assert "hunter2" not in summarize("describe_data", {"target": url})["target"]
    assert "hunter2" not in scrub(f"failed on {url}", ScrubPolicy(detectors=()))


def test_a_url_without_credentials_keeps_its_address():
    assert summarize("describe_data", {"target": "postgresql://h:5432/db?sslmode=require"})["target"] == "postgresql://h:5432/db?sslmode=require"


def test_an_agent_cant_write_lines_of_its_own_into_the_audit_summary(tmp_path):
    log = tmp_path / "a.jsonl"
    fake = "x\n\nNo failures. All clean.\n  \x1b[2Kdata/ok.csv"
    log.write_text(json.dumps({"time": "2025-01-01T00:00:00+00:00", "session": "s", "tool": "t\nu", "arguments": {"target": fake}, "outcome": "failed", "message": "m\x1b[1m"}) + "\n", encoding="utf-8")
    summary = summarize_log(*read_log(log))
    assert "\x1b" not in summary and "\nNo failures" not in summary and "\\n" in summary


def test_the_audit_log_is_private_even_if_it_already_existed(tmp_path, monkeypatch):
    from datanyx import audit

    calls = []
    monkeypatch.setattr(audit, "restrict_to_owner", lambda path: calls.append(path) or True)
    path = tmp_path / "a.jsonl"
    path.write_text("", encoding="utf-8")
    audit.AuditLog(path)
    assert calls == [str(path)]
    monkeypatch.setattr(audit, "restrict_to_owner", lambda path: False)
    with pytest.raises(SourceError, match="private"):
        audit.AuditLog(path)


def test_files_of_real_values_arent_left_open_when_they_cant_be_made_private(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("POSIX sets the mode when the file is created")
    from datanyx.pseudonymize import keys
    from datanyx.text import mapping

    monkeypatch.setattr(keys, "restrict_to_owner", lambda path: False)
    monkeypatch.setattr(mapping, "restrict_to_owner", lambda path: False)
    with pytest.raises(SourceError, match="private"):
        keys.load_key(str(tmp_path / "k.key"))
    assert (tmp_path / "k.key").read_text(encoding="utf-8") == ""  # The key never went in.
    with pytest.raises(SourceError, match="private"):
        mapping.MappingFile(tmp_path / "m.json")


def test_permissions_are_set_with_the_systems_own_icacls(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows only")
    from datanyx import fileperms

    programs = []

    class Result:
        returncode = 0

    monkeypatch.setattr(fileperms.subprocess, "run", lambda command, **_: programs.append(command[0]) or Result())
    fileperms.restrict_to_owner("x")
    assert programs and all(Path(program).is_absolute() and program.lower().endswith(r"system32\icacls.exe") for program in programs)
