"""Anonymizing HTML, Word and chat/email exports: what changes, what stays, and what is removed."""

import io
import json
import zipfile

import pytest

from datanyx.sources import SourceError
from datanyx.text import Anonymizer, DictionaryDetector, PatternDetector
from datanyx.text.detectors import PERSON
from datanyx.text.documents import anonymize_bytes, restore_bytes
from docx_helpers import docx


def anonymizer(*names):
    return Anonymizer([DictionaryDetector(PERSON, list(names)), PatternDetector()])


# --- HTML --------------------------------------------------------------------------------------------------


def html_out(page, *names):
    return anonymize_bytes(page.encode(), ".html", anonymizer(*names)).data.decode()


@pytest.mark.parametrize(
    "page",
    [
        "<!-- jane@corp.com --><p>x</p>",
        '<p data-email="jane@corp.com">x</p>',
        "<input value=jane@corp.com>",
        '<script>var u="jane@corp.com";</script>',
        "<script>var u='jane@corp.com';</script>",
        "<script>var u=`jane@corp.com`;</script>",
        "<script>// jane@corp.com\nvar x = 1;</script>",
        "<script>/* jane@corp.com */</script>",
        '<script type="application/ld+json">{"email": "jane@corp.com"}</script>',
        '<style>a { background: url("jane@corp.com") }</style>',
        '<a href="x" aria-description="jane@corp.com">y</a>',
        '<img src="/u/jane@corp.com/p.png">',
    ],
)
def test_html_has_nowhere_for_a_value_to_hide(page):
    assert "jane@corp.com" not in html_out(page)


def test_a_script_is_removed_and_a_stylesheet_keeps_its_rules():
    out = html_out('<script>var u = "jane@corp.com"; call();</script><style>.a { color: red; background: url(bob@corp.com) }</style>')
    assert "call()" not in out and "<script></script>" in out and ".a { color: red; background: url(" in out and "bob@corp.com" not in out


def test_nothing_changes_when_there_is_nothing_to_hide():
    page = '<style>.a { content: "b" } /* c */</style><input value=plain data-x="y"><p>Text &amp; more</p>'
    assert html_out(page) == page


def test_an_unquoted_attribute_is_escaped():
    assert "value=&lt;EMAIL_1&gt;>" in html_out("<input value=jane@corp.com>")


def test_html_restores_from_stylesheets_too():
    who = anonymizer()
    page = '<style>a { content: "bob@corp.com" }</style><p data-x="carl@corp.com">dan@corp.com</p>'
    done = anonymize_bytes(page.encode(), ".html", who).data
    assert restore_bytes(done, ".html", who.mapping).decode() == page


# --- Word ------------------------------------------------------------------------------------------------------


def part(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).decode()


def body(*paragraphs):
    return f'<w:document xmlns:w="w"><w:body>{"".join(paragraphs)}</w:body></w:document>'


def run(text):
    return f"<w:r><w:t>{text}</w:t></w:r>"


def test_text_after_a_text_box_is_anonymized():
    box = f"<w:txbxContent><w:p>{run('inner carl@corp.com')}</w:p></w:txbxContent>"
    document = body(f"<w:p>{run('before amy@corp.com')}{box}{run('after dan@corp.com')}</w:p>")
    xml = part(anonymize_bytes(docx(document), ".docx", anonymizer()).data, "word/document.xml")
    for real in ("amy@corp.com", "carl@corp.com", "dan@corp.com"):
        assert real not in xml
    assert "<w:txbxContent><w:p>" in xml and xml.count("</w:p>") == 2


def test_paragraphs_that_are_empty_or_self_closed_dont_confuse_it():
    document = body('<w:p w:rsidR="1"/>', f"<w:p>{run('amy@corp.com')}</w:p>", "<w:p></w:p>", f'<w:p w:rsidR="2">{run("bob@corp.com")}</w:p>')
    xml = part(anonymize_bytes(docx(document), ".docx", anonymizer()).data, "word/document.xml")
    assert "amy@corp.com" not in xml and "bob@corp.com" not in xml and '<w:p w:rsidR="1"/>' in xml


def test_a_paragraph_with_nothing_to_hide_is_untouched():
    document = body(f'<w:p>{run("plain")}{run(" words")}</w:p>')
    assert part(anonymize_bytes(docx(document), ".docx", anonymizer()).data, "word/document.xml") == document


def test_tracked_changes_lose_their_text_and_their_author():
    change = '<w:del w:author="jane@corp.com" w:initials="JD"><w:r><w:delText>deleted amy@corp.com</w:delText></w:r></w:del>'
    xml = part(anonymize_bytes(docx(body(f"<w:p>{run('x')}{change}</w:p>")), ".docx", anonymizer()).data, "word/document.xml")
    assert "amy@corp.com" not in xml and "jane@corp.com" not in xml and 'w:initials="JD"' not in xml
    assert "deleted" not in xml


def test_other_parts_lose_what_they_hold():
    data = docx(
        body(f"<w:p>{run('x')}</w:p>"),
        {
            "word/people.xml": '<w15:people><w15:person w15:author="jane@corp.com"><w15:presenceInfo w15:userId="jane@corp.com"/></w15:person></w15:people>',
            "docProps/custom.xml": "<Properties><property><vt:lpwstr>Client Jane Doe</vt:lpwstr></property></Properties>",
            "customXml/item1.xml": "<data><owner>Jane Doe</owner></data>",
            "word/_rels/document.xml.rels": '<Relationships><Relationship Id="1" Target="https://corp.example/jane@corp.com" TargetMode="External"/><Relationship Id="2" Target="media/image1.png"/></Relationships>',
        },
    )
    result = anonymize_bytes(data, ".docx", anonymizer())
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        everything = " ".join(archive.read(name).decode() for name in archive.namelist())
    assert "jane@corp.com" not in everything and "Jane Doe" not in everything
    assert 'Target="media/image1.png"' in everything


def test_alt_text_and_picture_names_are_anonymized():
    drawing = '<wp:docPr id="1" name="amy@corp.com.png" descr="Photo of bob@corp.com" title="carl@corp.com"/>'
    xml = part(anonymize_bytes(docx(body(f"<w:p>{run('x')}{drawing}</w:p>")), ".docx", anonymizer()).data, "word/document.xml")
    for real in ("amy@corp.com", "bob@corp.com", "carl@corp.com"):
        assert real not in xml


def test_a_damaged_docx_is_a_clear_error():
    with pytest.raises(SourceError, match="isn't readable text"):
        anonymize_bytes(docx(b"\xff\x00\xff"), ".docx", anonymizer())


# --- Names known from the structure of a document ------------------------------------------------------------


def test_a_whatsapp_sender_is_hidden_in_the_messages_too():
    chat = "\n".join(f"[01/02/2024, 10:0{i}:00] Jane Doe: message {i}" for i in range(3)) + "\n[01/02/2024, 10:05:00] Bob: Thanks Jane Doe, and Jane\n"
    out = anonymize_bytes(chat.encode(), ".txt", Anonymizer([])).data.decode()
    assert "Jane" not in out and "Doe" not in out and "Bob" not in out


def test_an_email_display_name_is_hidden_in_the_body():
    message = b"From: Jane Doe <jane@corp.com>\nTo: bob@corp.com\nSubject: Hi from Jane Doe\n\nRegards, Jane Doe\n"
    out = anonymize_bytes(message, ".eml", Anonymizer([])).data.decode()
    assert "Jane" not in out and "Doe" not in out


def test_a_slack_name_is_hidden_in_message_text():
    data = json.dumps([{"id": "U1", "real_name": "Jane Doe", "profile": {}}, {"ts": "1", "text": "ask Jane Doe", "user": "U1"}])
    out = anonymize_bytes(data.encode(), ".json", Anonymizer([])).data.decode()
    assert "Jane" not in out


def test_a_known_value_respects_what_the_user_kept():
    who = Anonymizer([], keep_terms=["Jane Doe"], keep_kinds=["EMAIL"])
    who.placeholder_for("PERSON", "Jane Doe")
    who.placeholder_for("EMAIL", "a@b.co")
    assert who.anonymize("Jane Doe wrote from a@b.co") == "Jane Doe wrote from a@b.co"
