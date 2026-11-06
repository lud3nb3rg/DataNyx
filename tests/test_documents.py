"""Documents of every kind are anonymized with stable placeholders and their structure intact."""

import email.message
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import pytest

from datanyx.cli import main
from datanyx.sources import SourceError
from datanyx.text import Anonymizer, DictionaryDetector, PatternDetector
from datanyx.text.batch import anonymize_folder, anonymize_stream, restore_folder
from datanyx.text.documents import (
    anonymize_bytes,
    anonymize_file,
    looks_like_whatsapp,
    restore_bytes,
)
from datanyx.text.mapping import MappingFile, read_mapping

JESSICA = "Jessica Whitfield"
TOMASZ = "Tomasz Kowalczyk"
SRC = str(Path(__file__).resolve().parents[1] / "src")


def make_anonymizer(**options):
    """People come from a dictionary, so these tests don't depend on a language model."""
    return Anonymizer([DictionaryDetector("PERSON", [JESSICA, "Whitfield", TOMASZ]), PatternDetector()], **options)


# --- The shared anonymizer ------------------------------------------------------------------------------------


def test_placeholders_are_shared_across_texts():
    anonymizer = make_anonymizer()
    first = anonymizer.anonymize(f"{JESSICA} wrote to a@acme-corp.io")
    second = anonymizer.anonymize(f"{TOMASZ} and {JESSICA} wrote to b@acme-corp.io and a@acme-corp.io")
    assert first == "<PERSON_1> wrote to <EMAIL_1>"
    assert second == "<PERSON_2> and <PERSON_1> wrote to <EMAIL_2> and <EMAIL_1>"
    assert anonymizer.mapping["<PERSON_1>"] == JESSICA


def test_segments_are_judged_together_and_keep_their_count():
    anonymizer = make_anonymizer()
    assert anonymizer.anonymize_segments(["", f"{JESSICA}.", "see a@acme-corp.io", f"{JESSICA} again"]) == [
        "",
        "<PERSON_1>.",
        "see <EMAIL_1>",
        "<PERSON_1> again",
    ]
    assert anonymizer.anonymize_segments([]) == []


def test_a_segment_that_already_holds_the_separator_is_done_on_its_own():
    anonymizer = make_anonymizer()
    separator = "\n⁣\n"
    assert anonymizer.anonymize_segments([f"a{separator}b {JESSICA}", "c"]) == [f"a{separator}b <PERSON_1>", "c"]


def test_a_value_known_to_be_a_kind_shares_its_placeholder_with_text():
    anonymizer = make_anonymizer()
    sender = anonymizer.placeholder_for("PERSON", f"  {JESSICA.lower()} ")
    assert anonymizer.anonymize(f"{JESSICA} replied") == f"{sender} replied"
    assert anonymizer.placeholder_for("PERSON", JESSICA) == sender
    assert anonymizer.mapping[sender] == JESSICA.lower()
    assert anonymizer.changes >= 1


def test_a_placeholder_written_literally_in_any_earlier_text_is_never_reused():
    anonymizer = make_anonymizer()
    anonymizer.anonymize("the literal <PERSON_1> in one document")
    assert anonymizer.anonymize(f"{JESSICA}") == "<PERSON_2>"


# --- Mapping files ------------------------------------------------------------------------------------------------


def test_mapping_files_are_exclusive_rewritable_and_read_in_both_shapes(tmp_path):
    path = tmp_path / "m.mapping.json"
    mapping = MappingFile(path)
    mapping.write({"<A_1>": "a long value", "<A_2>": "x"})
    mapping.write({"<A_1>": "short"})  # Rewritten, not appended to.
    mapping.close()
    assert read_mapping(path) == ({"<A_1>": "short"}, {})
    with pytest.raises(SourceError, match="already exists"):
        MappingFile(path)

    folder = MappingFile(tmp_path / "f.mapping.json")
    folder.write({"<A_1>": "v"}, {"file_001.txt": "reports/q3.txt"})
    folder.close()
    assert read_mapping(tmp_path / "f.mapping.json") == ({"<A_1>": "v"}, {"file_001.txt": "reports/q3.txt"})


def test_bad_mapping_files_give_fixed_messages(tmp_path):
    secret = "zq-secret-value"
    for text, message in (
        ("{ not json " + secret, "isn't valid JSON"),
        (json.dumps({"<A_1>": [secret]}), "expected format"),
        (json.dumps({"placeholders": {"<A_1>": "x"}, "files": {"f": 1}}), "expected format"),
        (json.dumps([secret]), "expected format"),
    ):
        path = tmp_path / "bad.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(SourceError, match=message) as error:
            read_mapping(path)
        assert secret not in str(error.value)
    with pytest.raises(SourceError, match="cannot read the mapping"):
        read_mapping(tmp_path / "missing.json")


# --- HTML ------------------------------------------------------------------------------------------------------------

PAGE = (
    "<!DOCTYPE html><html><head><title>Notes</title><style>.a { color: red }</style></head>"
    f'<body><!-- keep {JESSICA} in comment --><p class="x">Contact <b>{TOMASZ}</b> at '
    f'<a href="mailto:tk@acme-corp.io" title="{TOMASZ}">tk@acme-corp.io</a> &amp; friends.</p>'
    f'<img src="p.png" alt="Photo of {JESSICA}"><br/><p>Nothing here &amp; &eacute;.</p>'
    '<script>var e = "js@acme-corp.io";</script></body></html>'
)


def test_html_keeps_the_markup_and_replaces_text_and_descriptive_attributes():
    out = anonymize_bytes(PAGE.encode(), ".html", make_anonymizer()).data.decode()
    assert "&lt;PERSON_" in out and 'title="&lt;PERSON_' in out and 'alt="Photo of &lt;PERSON_' in out
    assert 'href="mailto:&lt;EMAIL_1&gt;"' in out
    assert TOMASZ not in out and "tk@acme-corp.io" not in out
    assert '<p class="x">' in out and '<img src="p.png"' in out and "<br/>" in out and "<!DOCTYPE html>" in out


def test_html_leaves_unchanged_text_and_code_exactly_and_hides_what_is_in_scripts_and_comments():
    out = anonymize_bytes(PAGE.encode(), ".html", make_anonymizer()).data.decode()
    assert "Nothing here &amp; &eacute;." in out  # Nothing to hide: the entities are as they were.
    assert ".a { color: red }" in out and 'var e = "<EMAIL_' in out  # The code stays, the value goes.
    assert "js@acme-corp.io" not in out and JESSICA not in out and "in comment" in out


def test_html_output_keeps_every_tag_balanced():
    out = anonymize_bytes(PAGE.encode(), ".html", make_anonymizer()).data.decode()
    assert out.count("<p") == PAGE.count("<p") and out.count("</p>") == PAGE.count("</p>")
    # Placeholders are escaped in the page's text, so no raw tag was added there.
    without_code = re.sub(r"<!--.*?-->|<script.*?</script>", "", out, flags=re.DOTALL)
    assert without_code.count("<") == re.sub(r"<!--.*?-->|<script.*?</script>", "", PAGE, flags=re.DOTALL).count("<")


# --- Email -----------------------------------------------------------------------------------------------------------


def make_eml(html_only=False):
    message = email.message.EmailMessage()
    message["From"] = f'"{JESSICA}" <jessica@acme-corp.io>'
    message["To"] = f"{TOMASZ} <tk@acme-corp.io>, bare@acme-corp.io"
    message["Cc"] = "carol@acme-corp.io"
    message["Subject"] = f"Invoice for {TOMASZ}"
    message["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    message["Received"] = "from mail.internal.acme-corp.io (10.1.2.3)"
    message["Message-ID"] = "<abc123@mail.internal.acme-corp.io>"
    if html_only:
        message.set_content(f"<html><body><p>Hello {TOMASZ},</p><p>Call jessica@acme-corp.io.</p></body></html>", subtype="html")
    else:
        message.set_content(f"Hello {TOMASZ},\nPlease call jessica@acme-corp.io.\n")
        message.add_attachment(b"secret bytes", maintype="application", subtype="pdf", filename=f"{JESSICA} contract.pdf")
    return message.as_bytes()


def test_email_becomes_text_with_people_replaced_and_attachments_left_out():
    result = anonymize_bytes(make_eml(), ".eml", make_anonymizer())
    out = result.data.decode()
    assert result.suffix == ".txt"
    assert out.splitlines()[0] == "From: <PERSON_1> (<EMAIL_1>)"
    assert "To: <PERSON_2> (<EMAIL_2>), <EMAIL_3>" in out and "Cc: <EMAIL_4>" in out
    assert "Date: Mon, 01 Jan 2024 10:00:00 +0000" in out and "Subject: Invoice for <PERSON_2>" in out
    assert "Hello <PERSON_2>," in out and "call <EMAIL_1>" in out
    assert "[attachment removed: <PERSON_1> contract.pdf]" in out
    for gone in ("Received", "Message-ID", "10.1.2.3", "secret bytes", "internal"):
        assert gone not in out


def test_an_html_only_email_is_read_as_text():
    out = anonymize_bytes(make_eml(html_only=True), ".eml", make_anonymizer()).data.decode()
    assert "Hello <PERSON_2>," in out and "<p>" not in out and "call <EMAIL_1>" in out.replace("Call", "call")


# --- Word -------------------------------------------------------------------------------------------------------------

NAMESPACES = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def make_docx(paragraphs):
    def run(text):
        return f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'

    body = "".join(f'<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr>{"".join(run(t) for t in runs)}</w:p>' for runs in paragraphs)
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NAMESPACES}><w:body>{body}</w:body></w:document>'
    core = (
        '<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Jane Doe</dc:creator><cp:lastModifiedBy>Jane Doe</cp:lastModifiedBy>'
        "<dc:title>Contract</dc:title></cp:coreProperties>"
    )
    app = '<?xml version="1.0"?><Properties><Company>Acme Corp</Company><Pages>1</Pages></Properties>'
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="x" Target="mailto:jane@acme-corp.io" TargetMode="External"/>'
        '<Relationship Id="rId2" Type="y" Target="https://example.com/page" TargetMode="External"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/media/image1.png", b"\x89PNG not really")
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
    return buffer.getvalue()


def docx_part(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).decode("utf-8")


def test_docx_hides_names_split_across_runs_and_keeps_other_paragraphs_untouched():
    paragraphs = [["Dear Jess", "ica Whit", "field, thanks & regards"], ["Untouched ", "paragraph"], [f"{TOMASZ} <tk@acme-corp.io>"]]
    original = make_docx(paragraphs)
    result = anonymize_bytes(original, ".docx", make_anonymizer())
    document = docx_part(result.data, "word/document.xml")
    assert JESSICA not in document and TOMASZ not in document and "Whit" not in document
    assert "Dear &lt;PERSON_1&gt;, thanks &amp; regards" in document
    assert docx_part(original, "word/document.xml").count('<w:t xml:space="preserve">Untouched </w:t>') == 1
    assert '<w:t xml:space="preserve">Untouched </w:t><' in document  # Its runs and formatting are as they were.
    ElementTree.fromstring(document)  # Still well-formed XML.
    assert "&lt;PERSON_2&gt; &lt;&lt;EMAIL_1&gt;&gt;" in document or "&lt;PERSON_2&gt;" in document


def test_docx_clears_properties_hides_mailto_links_and_notes_images():
    result = anonymize_bytes(make_docx([["hello"]]), ".docx", make_anonymizer())
    core = docx_part(result.data, "docProps/core.xml")
    assert "Jane Doe" not in core and "Contract" not in core and "<dc:creator></dc:creator>" in core
    assert "Acme Corp" not in docx_part(result.data, "docProps/app.xml") and "<Pages>1</Pages>" in docx_part(result.data, "docProps/app.xml")
    rels = docx_part(result.data, "word/_rels/document.xml.rels")
    assert "jane@acme-corp.io" not in rels and "https://example.com/page" not in rels and 'TargetMode="External"' in rels
    assert any("1 embedded image" in note for note in result.notes)
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("word/media/image1.png") == b"\x89PNG not really"


def test_docx_restores_to_the_original_text():
    anonymizer = make_anonymizer()
    original = make_docx([[f"{JESSICA} said ", "hi & bye"], [f"Mail {TOMASZ}"]])
    result = anonymize_bytes(original, ".docx", anonymizer)
    restored = restore_bytes(result.data, ".docx", anonymizer.mapping)
    document = docx_part(restored, "word/document.xml")
    assert JESSICA in document and TOMASZ in document and "hi &amp; bye" in document and "&lt;PERSON" not in document


def test_docx_errors():
    with pytest.raises(SourceError, match=r"valid \.docx"):
        anonymize_bytes(b"not a zip", ".docx", make_anonymizer())
    with pytest.raises(SourceError, match=r"valid \.docx"):
        restore_bytes(b"not a zip", ".docx", {})


# --- PDF ---------------------------------------------------------------------------------------------------------------


def make_pdf(pages):
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        page_number = len(objects) + 1
        kids.append(f"{page_number} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_number + 1} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


def test_pdf_becomes_text_with_page_markers():
    pytest.importorskip("pypdf")
    result = anonymize_bytes(make_pdf([f"Dear {JESSICA} call a@acme-corp.io", "Page two"]), ".pdf", make_anonymizer())
    out = result.data.decode()
    assert result.suffix == ".txt" and "--- page 1 ---" in out and "--- page 2 ---" in out
    assert "Dear <PERSON_1> call <EMAIL_1>" in out and JESSICA not in out and "Page two" in out


def test_pdf_with_no_text_and_garbage_are_reported_without_content():
    pytest.importorskip("pypdf")
    empty = anonymize_bytes(make_pdf([""]), ".pdf", make_anonymizer())
    assert any("no text was found" in note for note in empty.notes)
    with pytest.raises(SourceError, match="readable PDF") as error:
        anonymize_bytes(b"zq-secret-value", ".pdf", make_anonymizer())
    assert "zq-secret-value" not in str(error.value)


# --- WhatsApp ------------------------------------------------------------------------------------------------------------

ANDROID = (
    "12/01/2024, 10:23 - Messages and calls are end-to-end encrypted.\n"
    f"12/01/2024, 10:24 - {JESSICA}: Hi {TOMASZ}, send it to a@acme-corp.io\n"
    "continued on the next line\n"
    f"12/01/2024, 10:25 - {TOMASZ}: Thanks {JESSICA}\n"
    "12/01/2024, 10:26 - +44 7700 900123: who is this?\n"
)
IOS = f"[12/01/2024, 10:24:05] {JESSICA}: Hello\r\n[12/01/2024, 10:25:11] {TOMASZ}: Hi\r\n[12/01/2024, 10:26:00] {JESSICA}: Bye\r\n"


def test_whatsapp_senders_become_one_placeholder_each_and_structure_stays():
    assert looks_like_whatsapp(ANDROID) and looks_like_whatsapp(IOS) and not looks_like_whatsapp("just\nsome\nlines\nof text\n")
    out = anonymize_bytes(ANDROID.encode(), ".txt", make_anonymizer()).data.decode()
    lines = out.splitlines()
    assert lines[0] == "12/01/2024, 10:23 - Messages and calls are end-to-end encrypted."
    assert lines[1] == "12/01/2024, 10:24 - <PERSON_1>: Hi <PERSON_2>, send it to <EMAIL_1>"
    assert lines[2] == "continued on the next line"
    assert lines[3] == "12/01/2024, 10:25 - <PERSON_2>: Thanks <PERSON_1>"
    assert lines[4].startswith("12/01/2024, 10:26 - <PHONE_1>: who is this")
    assert JESSICA not in out and "7700" not in out


def test_whatsapp_ios_format_and_line_endings():
    out = anonymize_bytes(IOS.encode(), ".txt", make_anonymizer()).data.decode()
    assert out == "[12/01/2024, 10:24:05] <PERSON_1>: Hello\r\n[12/01/2024, 10:25:11] <PERSON_2>: Hi\r\n[12/01/2024, 10:26:00] <PERSON_1>: Bye\r\n"


# --- Slack ----------------------------------------------------------------------------------------------------------------

SLACK = [
    {
        "type": "message", "user": "U024BE7LH", "ts": "1700000000.000200", "client_msg_id": "5b0f3a9e-1111-4222-8333-444455556666",
        "text": f"Hi <@U061F7AUR>, {JESSICA} here: jessica@acme-corp.io",
        "user_profile": {"real_name": JESSICA, "display_name": "jess", "name": "jwhitfield", "image_72": "https://avatars.example.net/x.png"},
        "reactions": [{"name": "thumbsup", "users": ["U061F7AUR"], "count": 1}],
        "files": [{"name": "plan.pdf", "title": f"Plan for {TOMASZ}"}],
    },
    {"type": "message", "subtype": "channel_join", "user": "U061F7AUR", "ts": "1700000100.000100", "text": "<@U061F7AUR> has joined the channel"},
]


def test_slack_export_keeps_structure_and_replaces_people_ids_and_text():
    out = anonymize_bytes(json.dumps(SLACK, indent=2).encode(), ".json", make_anonymizer()).data.decode()
    data = json.loads(out)
    first = data[0]
    assert first["type"] == "message" and first["ts"] == "1700000000.000200" and data[1]["subtype"] == "channel_join"
    assert first["user"] == "<ID_1>" and data[1]["user"] == "<ID_2>"
    assert first["user_profile"]["real_name"] == "<PERSON_1>" and first["user_profile"]["display_name"] == "<PERSON_2>" and first["user_profile"]["name"] == "<PERSON_3>"
    assert "<PERSON_1>" in first["text"] and "<EMAIL_1>" in first["text"] and "<ID_2>" in first["text"]  # The mention lines up.
    assert first["reactions"][0]["name"] == "thumbsup" and first["reactions"][0]["users"] == ["<ID_2>"]
    for gone in (JESSICA, TOMASZ, "jessica@acme-corp.io", "U024BE7LH", "avatars.example.net", "jwhitfield"):
        assert gone not in out
    assert out.startswith("[\n  {")  # The layout is the input's.


def test_ordinary_json_is_left_to_plain_text_anonymization():
    data = json.dumps([{"name": "x", "note": f"{JESSICA} called"}])
    out = anonymize_bytes(data.encode(), ".json", make_anonymizer()).data.decode()
    assert out == json.dumps([{"name": "x", "note": "<PERSON_1> called"}])


# --- Plain files and one-off behaviour ------------------------------------------------------------------------------


def test_text_keeps_line_endings_and_other_formats_are_refused(tmp_path):
    out = anonymize_bytes(f"{JESSICA}\r\nline two\r\n".encode(), ".txt", make_anonymizer()).data
    assert out == b"<PERSON_1>\r\nline two\r\n"
    with pytest.raises(SourceError, match="isn't a format"):
        anonymize_bytes(b"x", ".png", make_anonymizer())
    with pytest.raises(SourceError, match="UTF-8"):
        anonymize_bytes(b"\xff\xfe\x00bad", ".txt", make_anonymizer())
    path = tmp_path / "n.txt"
    path.write_text(f"{JESSICA} calls", encoding="utf-8")
    assert anonymize_file(path, make_anonymizer()).data == b"<PERSON_1> calls"
    with pytest.raises(SourceError, match="cannot read input"):
        anonymize_file(tmp_path / "missing.txt", make_anonymizer())


# --- Folders -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "in"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text(f"{JESSICA} (a@acme-corp.io) called.\n", encoding="utf-8")
    (root / "b.md").write_text(f"Later {JESSICA} wrote from a@acme-corp.io. {TOMASZ} too.\n", encoding="utf-8")
    (root / "sub" / "page.html").write_text(f"<p>{TOMASZ}</p>", encoding="utf-8")
    (root / "sub" / "doc.docx").write_bytes(make_docx([[f"{JESSICA} signed"]]))
    (root / "image.png").write_bytes(b"\x89PNG")
    (root / "photo.jpg").write_bytes(b"\xff\xd8")
    (root / ".hidden.txt").write_text("hidden", encoding="utf-8")
    return root


def test_a_folder_is_anonymized_with_shared_placeholders_and_new_names(folder, tmp_path):
    out = tmp_path / "out"
    anonymizer = make_anonymizer()
    result = anonymize_folder(str(folder), str(out), anonymizer, recursive=True)
    assert result.files == {
        "file_001.txt": "a.txt", "file_002.md": "b.md", "file_003.docx": "sub/doc.docx", "file_004.html": "sub/page.html",
    }
    assert result.skipped == {".png": 1, ".jpg": 1} and not result.failed
    assert (out / "file_001.txt").read_text(encoding="utf-8") == "<PERSON_1> (<EMAIL_1>) called.\n"
    assert (out / "file_002.md").read_text(encoding="utf-8") == "Later <PERSON_1> wrote from <EMAIL_1>. <PERSON_2> too.\n"
    assert "&lt;PERSON_2&gt;" in (out / "file_004.html").read_text(encoding="utf-8")
    assert not any(JESSICA in path.read_text(encoding="utf-8", errors="ignore") for path in out.iterdir() if path.suffix in (".txt", ".md"))


def test_without_recursion_only_the_top_level_is_read(folder, tmp_path):
    result = anonymize_folder(str(folder), str(tmp_path / "out"), make_anonymizer())
    assert list(result.files.values()) == ["a.txt", "b.md"]


def test_keep_names_mirrors_the_input_and_email_and_pdf_become_text(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "mail.eml").write_bytes(make_eml())
    (root / "n.txt").write_text(JESSICA, encoding="utf-8")
    result = anonymize_folder(str(root), str(tmp_path / "out"), make_anonymizer(), keep_names=True)
    assert sorted(result.files) == ["mail.txt", "n.txt"]


def test_a_failing_file_doesnt_stop_the_rest_and_the_caller_hears_about_it(folder, tmp_path):
    (folder / "broken.docx").write_bytes(b"not a zip")
    seen = {}
    result = anonymize_folder(str(folder), str(tmp_path / "out"), make_anonymizer(), recursive=True, finish=lambda r: seen.update(files=dict(r.files)))
    assert len(result.failed) == 1 and "valid .docx" in result.failed[0] and result.failed[0].startswith("file_")
    assert "a.txt" in seen["files"].values() and len(result.files) == 4


def test_unsafe_output_folders_are_refused(folder, tmp_path):
    with pytest.raises(SourceError, match="inside the input"):
        anonymize_folder(str(folder), str(folder / "out"), make_anonymizer())
    out = tmp_path / "out"
    out.mkdir()
    (out / "file_001.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(SourceError, match="already has a file"):
        anonymize_folder(str(folder), str(out), make_anonymizer())
    assert (out / "file_001.txt").read_text(encoding="utf-8") == "keep me"


def test_a_folder_restores_with_its_original_names_and_formats(folder, tmp_path):
    out, back = tmp_path / "out", tmp_path / "back"
    anonymizer = make_anonymizer()
    result = anonymize_folder(str(folder), str(out), anonymizer, recursive=True)
    count, unknown = restore_folder(str(out), str(back), anonymizer.mapping, result.files)
    assert count == 4 and unknown == []
    assert (back / "a.txt").read_text(encoding="utf-8") == (folder / "a.txt").read_text(encoding="utf-8")
    assert (back / "sub" / "page.html").read_text(encoding="utf-8") == f"<p>{TOMASZ}</p>"
    assert JESSICA in docx_part((back / "sub" / "doc.docx").read_bytes(), "word/document.xml")
    with pytest.raises(SourceError, match="separate"):
        restore_folder(str(out), str(out / "again"), anonymizer.mapping, result.files)


# --- Streaming -------------------------------------------------------------------------------------------------------------


def test_lines_are_anonymized_as_they_arrive_with_stable_placeholders():
    anonymizer = make_anonymizer()
    written: list[str] = []
    flushes: list[int] = []
    lines = [f"{JESSICA} logged in\n".encode(), b"nothing here\r\n", f"{TOMASZ} and {JESSICA}".encode(), b"bad \xff byte\n"]
    count = anonymize_stream(lines, anonymizer, written.append, lambda: flushes.append(1))
    assert count == 4 and len(flushes) == 4
    assert written == ["<PERSON_1> logged in\n", "nothing here\r\n", "<PERSON_2> and <PERSON_1>", "bad � byte\n"]


def test_the_after_line_hook_sees_the_mapping_grow():
    anonymizer = make_anonymizer()
    sizes: list[int] = []
    anonymize_stream([f"{JESSICA}\n".encode(), b"plain\n", f"{TOMASZ}\n".encode()], anonymizer, lambda _: None, after_line=lambda: sizes.append(len(anonymizer.mapping)))
    assert sizes == [1, 1, 2]


# --- Command line ----------------------------------------------------------------------------------------------------------


def run(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.needs_model
def test_cli_anonymizes_a_folder_and_restores_it(folder, tmp_path, capsys):
    out, back, mapping = tmp_path / "out", tmp_path / "back", tmp_path / "m.mapping.json"
    code, _, err = run(["anonymize", str(folder), "-o", str(out), "-r", "--mapping", str(mapping)], capsys)
    assert code == 0 and "Anonymized 4 file(s)" in err and "file_001" in err and ".png (1)" in err
    assert json.loads(mapping.read_text(encoding="utf-8"))["files"]["file_001.txt"] == "a.txt"
    assert main(["restore", str(out), "-o", str(back), "--mapping", str(mapping)]) == 0
    assert (back / "a.txt").read_text(encoding="utf-8") == (folder / "a.txt").read_text(encoding="utf-8")


@pytest.mark.needs_model
def test_cli_folder_errors(folder, tmp_path, capsys):
    assert run(["anonymize", str(folder)], capsys)[0] == 1
    assert run(["anonymize", str(folder), "--stream"], capsys)[0] == 1
    assert run(["anonymize", str(folder / "a.txt"), "--recursive"], capsys)[0] == 1
    code, _, err = run(["anonymize", str(folder), "-o", str(folder / "out")], capsys)
    assert code == 1 and "inside the input" in err


@pytest.mark.needs_model
def test_cli_word_files_need_an_output_file_and_html_and_email_work(folder, tmp_path, capsys):
    docx = folder / "sub" / "doc.docx"
    code, _, err = run(["anonymize", str(docx)], capsys)
    assert code == 1 and "Word file" in err
    target = tmp_path / "clean.docx"
    code, _, err = run(["anonymize", str(docx), "-o", str(target)], capsys)
    assert code == 0 and "properties" in err and JESSICA not in docx_part(target.read_bytes(), "word/document.xml")

    code, out, _ = run(["anonymize", str(folder / "sub" / "page.html")], capsys)
    assert code == 0 and "<p>" in out and TOMASZ not in out
    eml = tmp_path / "m.eml"
    eml.write_bytes(make_eml())
    code, out, _ = run(["anonymize", str(eml)], capsys)
    assert code == 0 and out.startswith("From:") and "jessica@acme-corp.io" not in out


@pytest.mark.needs_model
def test_cli_still_reads_unknown_extensions_as_plain_text_and_stdin(tmp_path, capsys, monkeypatch):
    path = tmp_path / "notes.dat"
    path.write_text("mail a@acme-corp.io now", encoding="utf-8")
    code, out, _ = run(["anonymize", str(path)], capsys)
    assert code == 0 and out == "mail <EMAIL_1> now"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"from b@acme-corp.io"), encoding="utf-8"))
    code, out, _ = run(["anonymize", "-"], capsys)
    assert code == 0 and out == "from <EMAIL_1>"


@pytest.mark.needs_model
def test_cli_restores_single_html_and_docx_files(folder, tmp_path, capsys):
    mapping = tmp_path / "m.mapping.json"
    clean = tmp_path / "clean.html"
    main(["anonymize", str(folder / "sub" / "page.html"), "-o", str(clean), "--mapping", str(mapping)])
    capsys.readouterr()
    restored = tmp_path / "back.html"
    assert main(["restore", str(clean), "-o", str(restored), "--mapping", str(mapping)]) == 0
    assert restored.read_text(encoding="utf-8") == f"<p>{TOMASZ}</p>"


def stream_process(*args):
    environment = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "datanyx", "anonymize", "-", "--stream", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )


@pytest.mark.needs_model
def test_stream_anonymizes_each_line_as_it_arrives_and_keeps_the_mapping_current(tmp_path):
    mapping = tmp_path / "s.mapping.json"
    process = stream_process("--mapping", str(mapping))
    lines: queue.Queue[bytes] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    assert process.stdin is not None
    process.stdin.write(b"mail a@acme-corp.io please\n")
    process.stdin.flush()
    first = lines.get(timeout=120)  # Arrives before the input is closed.
    assert first.strip() == b"mail <EMAIL_1> please"
    process.stdin.write(b"and b@acme-corp.io, then a@acme-corp.io\n")
    process.stdin.close()
    assert lines.get(timeout=60).strip() == b"and <EMAIL_2>, then <EMAIL_1>"
    assert process.wait(timeout=60) == 0
    assert json.loads(mapping.read_text(encoding="utf-8")) == {"<EMAIL_1>": "a@acme-corp.io", "<EMAIL_2>": "b@acme-corp.io"}


def test_stream_needs_stdin_and_a_new_mapping(tmp_path, capsys):
    assert main(["anonymize", str(tmp_path / "x.txt"), "--stream"]) == 1
    assert "reads stdin" in capsys.readouterr().err
