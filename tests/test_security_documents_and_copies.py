"""Each test here is a way past DataNyx's promises that once worked, kept so it can't come back: documents, pseudonymized copies, and scrub, restore and overwriting."""

import io
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

from datanyx.cli import main
from datanyx.scrub import ScrubPolicy, scrub
from datanyx.sources import SourceError
from datanyx.text import Anonymizer, PatternDetector
from datanyx.text.batch import restore_folder
from datanyx.text.documents import anonymize_bytes

# --- documents ------------------------------------------------------------------------------


def docx_with_everything() -> bytes:
    document = """<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>Plain jane@acme-corp.io</w:t></w:r></w:p>
<w:p><w:ins w:author="Jane Author" w:initials="JA"><w:r><w:t>kept</w:t></w:r></w:ins><w:del w:author="Del Author"><w:r><w:delText>deleted bob@acme-corp.io</w:delText></w:r></w:del></w:p>
<w:p><w:moveFrom w:author="M"><w:r><w:t>moved mover@acme-corp.io</w:t></w:r></w:moveFrom><w:pPr><w:rPr><w:del w:id="1" w:author="Self Closed"/></w:rPr></w:pPr></w:p>
<w:p><w:r><w:drawing><wp:docPr descr="photo of carol@acme-corp.io" title="t"/></w:drawing></w:r></w:p>
<w:sdt><w:sdtPr><w:alias w:val="owner sara@acme-corp.io"/></w:sdtPr></w:sdt></w:body></w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/comments.xml", '<w:comments xmlns:w="w"><w:comment w:author="Dana Commenter" w:initials="DC"><w:p><w:r><w:t>ok</w:t></w:r></w:p></w:comment></w:comments>')
        archive.writestr("word/settings.xml", '<w:settings xmlns:w="w"><w:x w:author="Set Author"/></w:settings>')
        archive.writestr("word/people.xml", '<w:people xmlns:w="w"><w15:person w15:author="erin@acme-corp.io"/></w:people>')
        archive.writestr("docProps/custom.xml", "<Properties><property><vt:lpwstr>frank@acme-corp.io</vt:lpwstr></property></Properties>")
        archive.writestr("customXml/item1.xml", "<x>grace@acme-corp.io</x>")
        archive.writestr("customXml/itemProps1.xml", "<props/>")
        archive.writestr("word/embeddings/o.bin", "heidi@acme-corp.io")
        archive.writestr("word/glossary/document.xml", '<w:document xmlns:w="w"><w:p><w:r><w:t>ivan@acme-corp.io</w:t></w:r></w:p></w:document>')
    return buffer.getvalue()


def test_word_files_keep_nothing_personal_in_their_other_parts():
    output = anonymize_bytes(docx_with_everything(), ".docx", Anonymizer([PatternDetector()]))
    with zipfile.ZipFile(io.BytesIO(output.data)) as archive:
        parts = {name: archive.read(name).decode() for name in archive.namelist()}
    everything = "\n".join(parts.values())
    for needle in ("jane@", "Jane Author", "Del Author", "bob@", "mover@", "Self Closed", "carol@", "sara@", "Dana Commenter", "Set Author", "erin@", "frank@", "grace@", "heidi@", "ivan@"):
        assert needle not in everything, needle
    assert 'w:initials=""' in parts["word/comments.xml"]
    assert any("tracked deletion" in note for note in output.notes) and any("embedded file" in note for note in output.notes)
    assert "itemProps" in " ".join(parts)  # The package's structure stays.
    assert "kept" in parts["word/document.xml"]


def test_a_word_authors_placeholder_is_consistent_and_restorable():
    from datanyx.text.documents import restore_bytes

    anonymizer = Anonymizer([PatternDetector()])
    output = anonymize_bytes(docx_with_everything(), ".docx", anonymizer)
    restored = restore_bytes(output.data, ".docx", anonymizer.mapping)
    with zipfile.ZipFile(io.BytesIO(restored)) as archive:
        assert "Dana Commenter" in archive.read("word/comments.xml").decode()


def test_html_keeps_nothing_personal_outside_its_text():
    page = """<html><!-- owner jane@acme-corp.io --><head><script>var u="bob@acme-corp.io"</script><title>carol@acme-corp.io</title>
<style>.a { color: red }</style></head>
<body class="main"><div data-email="dana@acme-corp.io" id="erin@acme-corp.io" onclick="x('frank@acme-corp.io')" data-bare=gina@acme-corp.io>grace@acme-corp.io</div>
<img src="/u/heidi@acme-corp.io.png" alt="ivan@acme-corp.io"></body></html>"""
    output = anonymize_bytes(page.encode(), ".html", Anonymizer([PatternDetector()]))
    text = output.data.decode()
    for needle in ("jane@", "bob@", "carol@", "dana@", "erin@", "frank@", "gina@", "grace@", "heidi@", "ivan@"):
        assert needle not in text, needle
    assert 'class="main"' in text and ".a { color: red }" in text and "<script>" in text and "</script>" in text
    assert any("comment" in note for note in output.notes) and any("script" in note for note in output.notes)


# --- pseudonymized copies -------------------------------------------------------------------


def run_pseudonymize(source: Path, target: Path, *extra: str) -> int:
    return main(["--no-config", "pseudonymize", str(source), "-o", str(target), *extra])


def test_a_pseudonymized_database_keeps_nothing_in_its_schema_or_blobs(tmp_path):
    source = tmp_path / "real.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        """create table people( -- comment.real@acme-corp.io
            id integer primary key,
            email text default 'owner.real@acme-corp.io' check (email != 'banned.real@acme-corp.io'),
            status text,
            note blob,
            count integer default 3,
            constraint named check (status <> 'named.real@acme-corp.io'),
            check (status <> 'plain.real@acme-corp.io')
        );
        insert into people(email, status, note) values ('a.real@acme-corp.io', 'x', cast('blob.real@acme-corp.io' as blob));
        create view vip as select * from people where email = 'vip.real@acme-corp.io';
        create view plain_view as select id from people;
        create index partial_index on people(status) where status = 'index.real@acme-corp.io';
        create index plain_index on people(status);"""
    )
    connection.commit()
    connection.close()
    target = tmp_path / "fake.db"
    assert run_pseudonymize(source, target) == 0
    raw = target.read_bytes()
    assert b".real@" not in raw
    copy = sqlite3.connect(target)
    assert copy.execute("select count(*) from people").fetchone() == (1,)
    assert copy.execute("select note from people").fetchone() == (b"",)
    names = {row[0] for row in copy.execute("select name from sqlite_master")}
    assert {"plain_view", "plain_index"} <= names and not {"vip", "partial_index"} & names
    copy.close()


def test_blobs_can_be_kept_on_request(tmp_path):
    source = tmp_path / "real.db"
    connection = sqlite3.connect(source)
    connection.execute("create table t(id integer primary key, photo blob)")
    connection.execute("insert into t(photo) values (x'0102')")
    connection.commit()
    connection.close()
    assert run_pseudonymize(source, tmp_path / "o.db", "--keep-column", "photo") == 0
    copy = sqlite3.connect(tmp_path / "o.db")
    assert copy.execute("select photo from t").fetchone() == (b"\x01\x02",)
    copy.close()


def test_clean_ddl_keeps_what_the_copy_needs():
    from datanyx.pseudonymize.tables import clean_ddl

    assert clean_ddl("create table t(a int default 3, b text default (1 + 2) not null, c text)") == "create table t(a int , b text  not null, c text)"
    assert clean_ddl('create table t("default" text, [check] int, `x` int)') == 'create table t("default" text, [check] int, `x` int)'
    assert "it''s" not in clean_ddl("create table t(a text default 'it''s', b int)")
    cleaned = clean_ddl("create table t(a int, constraint c check (a > 0), b int, check (b > 0))")
    assert "check" not in cleaned.lower() and "constraint" not in cleaned.lower()
    sqlite3.connect(":memory:").execute(cleaned)


def test_values_without_a_name_to_judge_them_by_are_replaced(tmp_path):
    source = tmp_path / "in.json"
    source.write_text(json.dumps([{"jane.real@acme-corp.io": "note-under-data-key", "email": "b.real@acme-corp.io"}, {"email": "c.real@acme-corp.io"}]), encoding="utf-8")
    assert run_pseudonymize(source, tmp_path / "out.json") == 0
    out = (tmp_path / "out.json").read_text(encoding="utf-8")
    assert "note-under-data-key" not in out and "real@" not in out

    source = tmp_path / "in.csv"
    source.write_text("email,city\na.real@acme-corp.io,Paris,overflow.real@acme-corp.io\n", encoding="utf-8")
    assert run_pseudonymize(source, tmp_path / "out.csv") == 0
    assert "overflow" not in (tmp_path / "out.csv").read_text(encoding="utf-8")


def test_a_headerless_csv_isnt_copied_with_its_first_row_in_the_clear(tmp_path):
    source = tmp_path / "in.csv"
    source.write_text("Jane Real,jane.real@acme-corp.io\nBob Real,bob.real@acme-corp.io\n", encoding="utf-8")
    assert run_pseudonymize(source, tmp_path / "out.csv") == 0
    out = (tmp_path / "out.csv").read_text(encoding="utf-8")
    assert "real@" not in out and len(out.splitlines()) == 2


# --- scrub, restore, overwriting --------------------------------------------------------------


def test_only_real_traceback_lines_escape_scrubbing():
    policy = ScrubPolicy(detectors=(PatternDetector(),))
    forged = 'Traceback (most recent call last):\n  Jane, jane@acme-corp.io, 4111111111111111\n'
    assert "jane@" not in scrub(forged, policy)
    code_without_file = "Traceback (most recent call last):\n    jane@acme-corp.io\n"
    assert "jane@" not in scrub(code_without_file, policy)
    real = 'Traceback (most recent call last):\n  File "a.py", line 3, in f\n    x = row["email"]\n        ^^^^\nKeyError: \'jane@acme-corp.io\'\n'
    out = scrub(real, policy)
    assert 'File "a.py", line 3' in out and 'x = row["email"]' in out and "^^^^" in out and "jane@" not in out
    repeated = 'Traceback (most recent call last):\n  File "a.py", line 1, in f\n    f()\n  [Previous line repeated 996 more times]\nRecursionError: x\n'
    assert "[Previous line repeated 996 more times]" in scrub(repeated, policy)


def test_long_quoted_values_are_scrubbed_too():
    text = "ValueError: '" + "S3CRETVALUE " * 500 + "'"
    assert "S3CRETVALUE" not in scrub(text, ScrubPolicy(detectors=()))


def test_restore_stays_inside_the_output_folder(tmp_path):
    source = tmp_path / "anon"
    source.mkdir()
    (source / "file_001.txt").write_text("<PERSON_1>", encoding="utf-8")
    target = tmp_path / "nested" / "restored"
    for escaping in ("../../escaped.txt", str(tmp_path / "absolute.txt")):
        with pytest.raises(SourceError, match="outside"):
            restore_folder(str(source), str(target), {"<PERSON_1>": "x"}, {"file_001.txt": escaping})
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "absolute.txt").exists()


def test_schema_wont_overwrite_its_own_input(tmp_path):
    path = tmp_path / "orig.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    assert main(["--no-config", "schema", str(path), "-o", str(path)]) == 1
    assert path.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_saved_real_output_is_private(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX file modes")
    saved = tmp_path / "out.txt"
    assert main(["--no-config", "run", "--no-ner", "--save-stdout", str(saved), "--", sys.executable, "-c", "print(1)"]) == 0
    assert saved.stat().st_mode & 0o777 == 0o600
