"""The scan says where personal data and secrets are, and never what they are."""

import json

import pytest

from datanyx.cli import main
from datanyx.scan import DEFAULT_KINDS, render_report, scan
from datanyx.sources import SourceError
from datanyx.text import SecretDetector

CANARY_EMAIL = "zqcanary@acme-corp.io"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # The placeholder from AWS's own documentation.


@pytest.fixture
def tree(tmp_path):
    def write(name, text, mode="w"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "wb":
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    write("app/config.py", f'AWS = "{AWS_KEY}"\nx = 1\nDB_PASSWORD = "s3cr3tValue99"\n')
    write("data/customers.csv", f"id,email,phone\n1,{CANARY_EMAIL},+44 20 7946 0958\n2,bo@acme-corp.io,+1 415 555 2671\n3,cy@acme-corp.io,\n")
    write("docs/guide.md", "Mail user@example.com or noreply@github.com. Loopback 127.0.0.1, docs 192.0.2.7, internal 10.0.0.5.\n")
    write("src/clean.py", "def add(a, b):\n    return a + b  # version 1.2.3 on 2024-01-02, 198.51.100.4\n")
    write(".git/config", "email = hidden@acme-corp.io\n")
    write("node_modules/pkg/index.js", "// dev@acme-corp.io\n")
    write("big.txt", "x@acme-corp.io " * 400_000)
    write("blob.bin", b"\x00\x01\x02 binary", "wb")
    write("exports/people.xlsx", b"PK\x03\x04\x00\x00 zip", "wb")
    write("Jane Doe.csv", "email\njane@acme-corp.io\n")
    return tmp_path


def by_name(report):
    return {item.display: item for item in report.findings}


def test_finds_secrets_emails_phones_and_addresses_by_file(tree):
    report = scan(str(tree))
    found = by_name(report)
    assert found["app/config.py"].counts == {"SECRET": 2}
    assert found["app/config.py"].lines["SECRET"] == [1, 3]
    assert found["data/customers.csv"].counts == {"EMAIL": 3, "PHONE": 2}
    assert found["data/customers.csv"].lines["EMAIL"] == [2, 3, 4]


def test_benign_matches_are_not_counted(tree):
    found = by_name(scan(str(tree)))
    assert found["docs/guide.md"].counts == {"IP_ADDRESS": 1}  # Only the internal address.
    assert "src/clean.py" not in found  # A version number, a date and a documentation IP.
    included = by_name(scan(str(tree), include_examples=True))
    assert included["docs/guide.md"].counts["EMAIL"] == 2 and included["docs/guide.md"].counts["IP_ADDRESS"] == 3


def test_excluded_big_and_binary_files_are_reported_as_skipped(tree):
    report = scan(str(tree), max_bytes=1_000_000)
    assert report.too_large == 1 and report.binary == 2
    assert report.not_scanned_data == ["exports/people.xlsx"]
    assert not any(".git" in name or "node_modules" in name for name in by_name(report))
    assert "hidden@acme-corp.io" not in render_report(report)
    extra = by_name(scan(str(tree), excludes=["data"], max_bytes=1_000_000))
    assert "data/customers.csv" not in extra


def test_values_never_appear_in_any_output(tree):
    report = scan(str(tree), max_bytes=1_000_000)
    for output in (render_report(report), render_report(report, "json")):
        for value in (CANARY_EMAIL, AWS_KEY, "s3cr3tValue99", "7946", "415 555", "jane@acme-corp.io"):
            assert value not in output


def test_file_names_that_look_like_data_are_hidden_unless_asked(tree):
    report = scan(str(tree), max_bytes=1_000_000)
    names = set(by_name(report))
    assert "Jane Doe.csv" not in names and any(name.startswith("file_") for name in names)
    assert report.hidden_names == 1 and "file_N" in render_report(report)
    assert "Jane Doe.csv" not in render_report(report)
    shown = scan(str(tree), max_bytes=1_000_000, show_paths=True)
    assert "Jane Doe.csv" in by_name(shown) and shown.hidden_names == 0


def test_kinds_can_be_chosen(tree):
    only_email = scan(str(tree), kinds={"EMAIL"}, max_bytes=1_000_000)
    assert set(only_email.totals) == {"EMAIL"}
    assert "DATE" not in DEFAULT_KINDS
    with_dates = scan(str(tree), kinds={"DATE"}, max_bytes=1_000_000)
    assert "src/clean.py" in by_name(with_dates)


def test_phone_pattern_ignores_dates_versions_and_ips(tmp_path):
    (tmp_path / "t.txt").write_text(
        "2024-01-02 and 02/03/2024 and 1.2.3 and 2026.10.23 and 6.0.12.20261103 and 198.51.100.0\n"
        "call +33 1 70 18 99 00 or (415) 555-2671 or 555.123.4567\n",
        encoding="utf-8",
    )
    assert scan(str(tmp_path)).totals == {"PHONE": 3}


def test_single_files_and_missing_paths(tree):
    report = scan(str(tree / "app" / "config.py"))
    assert report.scanned == 1 and report.findings[0].display == "config.py"
    with pytest.raises(SourceError):
        scan(str(tree / "nowhere"))


def test_clean_folder_says_so(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here\n", encoding="utf-8")
    report = scan(str(tmp_path))
    assert report.clean and "No personal data or secrets found" in render_report(report)


def test_json_report_shape(tree):
    data = json.loads(render_report(scan(str(tree), max_bytes=1_000_000), "json"))
    assert data["skipped"] == {"too_large": 1, "binary": 2}
    assert data["totals"]["SECRET"] == 2
    assert {"path", "counts", "lines"} <= set(data["files"][0])


def test_ner_finds_names_when_asked(tmp_path):
    spacy_util = pytest.importorskip("spacy.util")
    if not spacy_util.is_package("en_core_web_lg"):
        pytest.skip("needs en_core_web_lg")
    from datanyx.scan import NER_KINDS
    from datanyx.text import PatternDetector, SpacyDetector

    (tmp_path / "n.txt").write_text("Jessica Whitfield called Tomasz Kowalczyk in Portland.\n", encoding="utf-8")
    report = scan(
        str(tmp_path),
        detectors=[PatternDetector(), SpacyDetector("en_core_web_lg")],
        kinds=DEFAULT_KINDS | NER_KINDS,
    )
    assert report.totals["PERSON"] == 2 and "Jessica" not in render_report(report)


# --- Secrets ------------------------------------------------------------------------------


def secrets(text):
    return [text[s.start : s.end] for s in SecretDetector().find(text)]


def test_secret_formats_are_found_with_only_the_secret_marked():
    assert secrets(f"key = {AWS_KEY}") == [AWS_KEY]
    assert secrets("t = ghp_" + "a" * 36) == ["ghp_" + "a" * 36]
    assert secrets("-----BEGIN RSA PRIVATE KEY-----") == ["-----BEGIN RSA PRIVATE KEY-----"]
    assert secrets("postgres://admin:hunter22pw@db.internal/app") == ["hunter22pw"]
    assert secrets('DB_PASSWORD = "s3cr3tValue99"') == ["s3cr3tValue99"]
    assert secrets("api_key: abcd1234efgh5678") == ["abcd1234efgh5678"]
    assert secrets('"client_secret": "Zx81kQpLm92aa"') == ["Zx81kQpLm92aa"]
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert secrets(f"Authorization: Bearer {jwt}") == [jwt]


@pytest.mark.parametrize(
    "text",
    [
        'password = "changeme"',
        'api_key = os.environ["API_KEY"]',
        "secret: ${SECRET}",
        "password = getpass.getpass()",
        "token = fetch_token(user)",
        "the password field is required",
        "password: <your password>",
        'secret = "xxxxxxxx"',
        "http://user@host/path",
    ],
)
def test_placeholders_and_ordinary_text_are_not_secrets(text):
    assert secrets(text) == []


# --- Command line ------------------------------------------------------------------------


def test_cli_text_and_exit_codes(tree, capsys):
    assert main(["scan", str(tree), "--max-size", "1"]) == 0
    out = capsys.readouterr().out
    assert "app/config.py" in out and "SECRET 2" in out and CANARY_EMAIL not in out
    assert main(["scan", str(tree), "--max-size", "1", "--fail-on-findings"]) == 1
    clean = tree / "clean"
    clean.mkdir()
    (clean / "a.txt").write_text("hello\n", encoding="utf-8")
    assert main(["scan", str(clean), "--fail-on-findings"]) == 0


def test_cli_options(tree, capsys):
    assert main(["scan", str(tree), "--max-size", "1", "--kinds", "SECRET", "--format", "json"]) == 0
    assert set(json.loads(capsys.readouterr().out)["totals"]) == {"SECRET"}
    assert main(["scan", str(tree), "--max-size", "1", "--kinds", "all", "--exclude", "data", "--show-paths"]) == 0
    out = capsys.readouterr().out
    assert "Jane Doe.csv" in out and "data/customers.csv" not in out
    assert main(["scan", str(tree / "missing")]) == 1
    assert main(["scan", str(tree), "--max-size", "0"]) == 1


def test_mcp_scan_tool(tree):
    pytest.importorskip("mcp")
    import asyncio

    from datanyx.mcp_server import create_server

    server = create_server([tree])

    async def call(arguments):
        return await server.call_tool("scan_folder", arguments)

    text = asyncio.run(call({"target": str(tree / "app")})).content[0].text
    assert "config.py" in text and "SECRET 2" in text and AWS_KEY not in text
    with pytest.raises(Exception, match="kinds"):
        asyncio.run(call({"target": str(tree / "app"), "kinds": ["NOPE"]}))
    with pytest.raises(Exception, match="outside"):
        asyncio.run(call({"target": str(tree.parent)}))
