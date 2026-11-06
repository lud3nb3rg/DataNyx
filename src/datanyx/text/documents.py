"""Anonymize documents in the formats people actually have, keeping their structure.

| Format | Result |
|---|---|
| text, Markdown, logs, CSV, JSON and similar | the same file, with placeholders |
| HTML | the same page: tags and layout stay, text and attributes change; comments and scripts are removed |
| Word (.docx) | the same document: styles, tables and layout stay; a paragraph that had something to hide loses the formatting inside it; author and company are cleared, tracked deletions are removed, and custom properties and embedded files are emptied |
| email (.eml) | a text file of the headers and the body, with the people replaced and attachments left out |
| PDF | a text file of the pages (needs `datanyx[pdf]`); scanned pages with no text can't be read |
| WhatsApp export | the same chat, with each sender replaced by one placeholder |
| Slack export | the same JSON, with names, ids, emails and message text replaced |

Everything goes through one `Anonymizer`, so a person is the same placeholder everywhere. Images
and other embedded files aren't looked at, and the notes in each result say so.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from ..sources.base import SourceError
from .anonymizer import PLACEHOLDER, Anonymizer

TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".toml", ".srt"}
)
HANDLED_EXTENSIONS = TEXT_EXTENSIONS | {".html", ".htm", ".eml", ".docx", ".pdf"}
_MAX_UNZIPPED_BYTES = 300 * 1024 * 1024


@dataclass
class Output:
    data: bytes
    suffix: str
    notes: list[str] = field(default_factory=list)


def can_handle(path: str | Path) -> bool:
    return Path(path).suffix.lower() in HANDLED_EXTENSIONS


def anonymize_file(path: str | Path, anonymizer: Anonymizer) -> Output:
    """Anonymize one document. The result's suffix says what kind of file `data` is."""
    suffix = Path(path).suffix.lower()
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise SourceError(f"cannot read input: {error.strerror}") from None
    return anonymize_bytes(data, suffix, anonymizer)


def anonymize_bytes(data: bytes, suffix: str, anonymizer: Anonymizer) -> Output:
    if suffix in (".html", ".htm"):
        page, notes = _html(_decode(data), anonymizer)
        return Output(page.encode("utf-8"), suffix, notes)
    if suffix == ".eml":
        return Output(_eml(data, anonymizer).encode("utf-8"), ".txt")
    if suffix == ".docx":
        return _docx(data, anonymizer)
    if suffix == ".pdf":
        return _pdf(data, anonymizer)
    if suffix in TEXT_EXTENSIONS:
        text = _decode(data)
        if suffix == ".txt" and looks_like_whatsapp(text):
            return Output(_whatsapp(text, anonymizer).encode("utf-8"), suffix)
        if suffix == ".json":
            slack = _slack(text, anonymizer)
            if slack is not None:
                return Output(slack.encode("utf-8"), suffix)
        return Output(anonymizer.anonymize(text).encode("utf-8"), suffix)
    raise SourceError(f"{suffix or 'this kind of file'} isn't a format DataNyx can anonymize")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("input is not valid UTF-8") from None


# --- HTML ------------------------------------------------------------------------------------------

_TAG = re.compile(r"<!--.*?-->|<![^>]*>|<[^>]*>", re.DOTALL)
_ATTRIBUTE = re.compile(r"""(\s([\w:.-]+)\s*=\s*)(?:(["'])(.*?)\3|([^\s"'>]+))""", re.DOTALL)
# Attributes about layout and behaviour. Any other can hold data: data-*, id, name, src, onclick...
_LAYOUT_ATTRIBUTES = frozenset(
    ["class", "style", "type", "rel", "lang", "dir", "width", "height", "align", "valign", "border", "charset", "media", "target", "role", "colspan", "rowspan", "method", "http-equiv", "xmlns", "hidden", "disabled", "checked", "selected", "readonly", "required", "tabindex", "enctype", "loading", "crossorigin", "integrity", "as", "sizes", "bgcolor", "color", "cellpadding", "cellspacing", "span", "scope", "maxlength", "min", "max", "step", "rows", "cols", "autocomplete", "spellcheck", "draggable", "accesskey", "xml:lang"]
)
_SKIPPED_TAG = re.compile(r"<(script|style)\b[^>]*>|</(script|style)\s*>", re.IGNORECASE)
_STYLE_TEXT = re.compile(r""""(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|/\*.*?\*/|(?<=url\()[^)'"]+(?=\))""", re.DOTALL)
_SLOT = re.compile("(\\d+)")


def _html(text: str, anonymizer: Anonymizer) -> tuple[str, list[str]]:
    """Anonymize the text and the attributes of a page, leaving the markup alone.

    Comments and scripts are removed: nothing in them is shown to a reader, and code can't be
    anonymized and still mean anything. Styles stay, as they hold no text of the page, apart from
    the strings and comments in them.
    """
    raws: list[str] = []  # What each slot held, unescaped.
    originals: list[str] = []
    quoted: list[bool] = []
    unescaped: set[int] = set()  # Slots inside a stylesheet, where entities mean nothing.

    def slot(raw: str, original: str, in_attribute: bool) -> str:
        raws.append(html.unescape(raw))
        originals.append(original)
        quoted.append(in_attribute)
        return f"{len(raws) - 1}"

    def attribute(match: re.Match[str]) -> str:
        prefix, name, quote, quoted_value, bare_value = match.groups()
        if name.lower() in _LAYOUT_ATTRIBUTES:
            return match.group(0)
        if quote:
            return f"{prefix}{quote}{slot(quoted_value, quoted_value, True)}{quote}"
        return f"{prefix}{slot(bare_value, bare_value, True)}"

    def style_text(match: re.Match[str]) -> str:
        whole = match.group()
        before = 2 if whole.startswith("/*") else 1 if whole[0] in "'\"" else 0
        inner = whole[before : len(whole) - before]
        if not inner.strip():
            return whole
        unescaped.add(len(raws))
        return whole[:before] + slot(inner, inner, False) + whole[len(whole) - before :]

    out: list[str] = []
    position = 0
    skipping = dropping = False
    comments = scripts = 0
    for match in _TAG.finditer(text):
        between = text[position : match.start()]
        if dropping:
            scripts += bool(between.strip())
        elif skipping:
            out.append(_STYLE_TEXT.sub(style_text, between))
        elif not between.strip():
            out.append(between)
        else:
            lead = between[: len(between) - len(between.lstrip())]
            trail = between[len(between.rstrip()) :]
            out.append(lead + slot(between.strip(), between.strip(), False) + trail)
        tag = match.group()
        position = match.end()
        if tag.startswith("<!--"):
            comments += 1
            continue
        skipper = _SKIPPED_TAG.fullmatch(tag)
        if skipper and not tag.endswith("/>"):
            skipping = skipper.group(1) is not None
            dropping = skipping and skipper.group(1).lower() == "script"
        out.append(_ATTRIBUTE.sub(attribute, tag))
    tail = text[position:]
    if dropping:
        scripts += bool(tail.strip())
    elif skipping:
        out.append(_STYLE_TEXT.sub(style_text, tail))
    elif tail.strip():
        out.append(tail[: len(tail) - len(tail.lstrip())] + slot(tail.strip(), tail.strip(), False))
        out.append(tail[len(tail.rstrip()) :])
    else:
        out.append(tail)

    results = anonymizer.anonymize_segments(raws)

    def fill(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if results[index] == raws[index]:
            return originals[index]  # Unchanged, so the original spelling (and entities) stay.
        return results[index] if index in unescaped else html.escape(results[index], quote=quoted[index])

    notes = []
    if comments:
        notes.append(f"{comments} HTML comment(s) were removed")
    if scripts:
        notes.append(f"the contents of {scripts} script(s) were removed")
    return _SLOT.sub(fill, "".join(out)), notes


def _html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", "", markup)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])\s*>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- Email -------------------------------------------------------------------------------------------

_ADDRESS_HEADERS = ("From", "Reply-To", "Sender", "To", "Cc", "Bcc")


def _eml(data: bytes, anonymizer: Anonymizer) -> str:
    message = email.message_from_bytes(data, policy=email.policy.default)
    header_lines: list[str] = []
    for name in _ADDRESS_HEADERS:
        for value in message.get_all(name, []):
            people = []
            for display, address in email.utils.getaddresses([str(value)]):
                parts = []
                if display.strip() and display.strip() != address:
                    parts.append(anonymizer.placeholder_for("PERSON", display))
                if address:
                    parts.append(f"({anonymizer.placeholder_for('EMAIL', address)})" if parts else anonymizer.placeholder_for("EMAIL", address))
                if parts:
                    people.append(" ".join(parts))
            header_lines.append(f"{name}: {', '.join(people)}")

    body = message.get_body(preferencelist=("plain", "html"))
    content = ""
    if body is not None:
        try:
            content = body.get_content()
        except Exception:  # A wrong charset label, for one.
            payload = body.get_payload(decode=True)
            content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
        if body.get_content_subtype() == "html":
            content = _html_to_text(content)
    names = [part.get_filename() or "unnamed" for part in message.iter_attachments()]

    date = str(message.get("Date", "")).strip()
    subject = str(message.get("Subject", "")).strip()
    pieces = anonymizer.anonymize_segments([subject, content, *names])
    lines = header_lines + ([f"Date: {date}"] if date else []) + [f"Subject: {pieces[0]}", "", pieces[1]]
    lines += [f"[attachment removed: {name}]" for name in pieces[2:]]
    return chr(10).join(lines).rstrip() + chr(10)


# --- Word --------------------------------------------------------------------------------------------

_PARAGRAPH_TAG = re.compile(r"<w:p(?:\s[^>]*?)?(/?)>|</w:p>")
_RUN_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)
_FIELD_CODE = re.compile(r"(<w:instrText(?:\s[^>]*)?>)(.*?)(</w:instrText>)", re.DOTALL)
_TEXT_PART = re.compile(r"word/(?:glossary/)?(document|header\d*|footer\d*|comments|footnotes|endnotes)\.xml")
_PERSONAL_PROPERTIES = re.compile(
    r"(<(dc:creator|cp:lastModifiedBy|dc:title|dc:subject|cp:keywords|dc:description|cp:category|cp:contentStatus|Company|Manager|HyperlinkBase|Template)\b[^>]*(?<!/)>)[^<]*(</\2>)",
    re.DOTALL,
)
# Text a reader no longer sees, kept in the file by tracked changes.
_TRACKED_REMOVALS = re.compile(r"<w:(del|moveFrom)(?:\s[^>]*[^/>])?>.*?</w:\1>", re.DOTALL)
_REVISION_AUTHOR = re.compile(r'(\s(?:\w+:)?(?:author|lastModifiedBy)=")([^"]*)(")')
_REVISION_IDENTITY = re.compile(r'(\s(?:\w+:)?(?:initials|userId|providerId)=")[^"]*(")')
_DESCRIPTIVE = re.compile(r'(\s(?:\w+:)?(?:descr|title|tooltip)="|<w:(?:alias|tag)\s+w:val=")([^"]*)(")')
_CUSTOM_XML = re.compile(r"customXml/item\d+\.xml")
_XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
# Parts emptied rather than removed: the rest of the package refers to them, and Word complains if they go.
_EMPTY_PARTS = {
    "word/people.xml": _XML_HEADER + '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"/>',
    "docProps/custom.xml": _XML_HEADER + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"/>',
}
_PICTURE_NAME = re.compile(r"""(<(?:wp:docPr|pic:cNvPr)\b[^>]*?\bname=")([^"]*)(")""")
_RELATIONSHIP = re.compile(r"<Relationship\b[^>]*>")
_TARGET = re.compile(r'(Target=")([^"]*)(")')
_OUTSIDE_LINK = re.compile(r'TargetMode="External"|Target="mailto:', re.IGNORECASE)
_LINK_ADDRESS = re.compile(r"([a-z][a-z0-9+.-]*://[^/?#]*)(.*)", re.IGNORECASE | re.DOTALL)


def _xml_text(content: bytes) -> str:
    """A part's text. Word writes UTF-8, but a part can say otherwise with a byte order mark."""
    try:
        if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return content.decode("utf-16")
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("a part of the .docx file isn't readable text") from None


def _docx(data: bytes, anonymizer: Anonymizer) -> Output:
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SourceError("input isn't a valid .docx file") from None
    with source:
        if sum(info.file_size for info in source.infolist()) > _MAX_UNZIPPED_BYTES:
            raise SourceError("the .docx file is too large to read safely")
        buffer = io.BytesIO()
        images = embedded = removals = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                name = info.filename
                try:
                    content = source.read(name)
                except Exception as error:  # zipfile's own, and zlib's, for a damaged file.
                    raise SourceError(f"the .docx file is damaged ({type(error).__name__})") from None
                if name.startswith("word/media/") or name.startswith("docProps/thumbnail"):
                    images += 1
                elif name.startswith("word/embeddings/"):
                    embedded += 1
                    content = b""
                elif name in _EMPTY_PARTS:
                    content = _EMPTY_PARTS[name].encode("utf-8")
                elif _CUSTOM_XML.fullmatch(name):
                    content = (_XML_HEADER + "<root/>").encode("utf-8")
                elif _TEXT_PART.fullmatch(name):
                    xml, removed = _TRACKED_REMOVALS.subn("", _xml_text(content))
                    removals += removed
                    content = _docx_text_part(_docx_attributes(xml, anonymizer), anonymizer).encode("utf-8")
                elif name.startswith("word/") and name.endswith(".xml"):
                    content = _docx_attributes(_xml_text(content), anonymizer).encode("utf-8")
                elif name in ("docProps/core.xml", "docProps/app.xml"):
                    content = _PERSONAL_PROPERTIES.sub(r"\1\3",_xml_text(content)).encode("utf-8")
                elif name.startswith("word/_rels/") and name.endswith(".rels"):
                    content = _link_targets(_xml_text(content), anonymizer).encode("utf-8")
                target.writestr(info, content)
    notes = ["document properties such as author and company were cleared"]
    if images:
        notes.append(f"{images} embedded image(s) were not processed")
    if embedded:
        notes.append(f"{embedded} embedded file(s) were emptied")
    if removals:
        notes.append(f"{removals} tracked deletion(s) were removed")
    return Output(buffer.getvalue(), ".docx", notes)


def _docx_attributes(xml: str, anonymizer: Anonymizer) -> str:
    """Who made each revision and comment, and the descriptions on pictures and content controls."""

    def author(match: re.Match[str]) -> str:
        value = html.unescape(match.group(2))
        if not value.strip():
            return match.group()
        return match.group(1) + xml_escape(anonymizer.placeholder_for("PERSON", value)) + match.group(3)

    def descriptive(match: re.Match[str]) -> str:
        return match.group(1) + xml_escape(anonymizer.anonymize(html.unescape(match.group(2))), {'"': "&quot;"}) + match.group(3)

    xml = _REVISION_AUTHOR.sub(author, xml)
    xml = _REVISION_IDENTITY.sub(r"\1\2", xml)
    return _PICTURE_NAME.sub(descriptive, _DESCRIPTIVE.sub(descriptive, xml))


def _paragraphs(xml: str) -> list[list[tuple[int, int]]]:
    """Each paragraph, as the stretches of the part that are its own.

    A text box puts whole paragraphs inside a paragraph, so a stretch stops where an inner one starts."""
    paragraphs: list[list[tuple[int, int]]] = []
    stack: list[list[tuple[int, int]]] = []
    starts: list[int] = []
    for match in _PARAGRAPH_TAG.finditer(xml):
        if match.group().startswith("</"):
            if not stack:
                continue
            own = stack.pop()
            own.append((starts.pop(), match.start()))
            paragraphs.append(own)
            if starts:
                starts[-1] = match.end()  # The outer paragraph goes on after this one.
        elif not match.group(1):  # Not an empty <w:p/>.
            if starts:
                stack[-1].append((starts[-1], match.start()))
            stack.append([])
            starts.append(match.end())
    return paragraphs


def _docx_text_part(xml: str, anonymizer: Anonymizer) -> str:
    runs = [[run for start, end in own for run in _RUN_TEXT.finditer(xml, start, end)] for own in _paragraphs(xml)]
    runs = [found for found in runs if found]
    texts = [html.unescape("".join(run.group(1) for run in found)) for found in runs]
    wanted = [index for index, text in enumerate(texts) if text.strip()]
    results = dict(zip(wanted, anonymizer.anonymize_segments([texts[index] for index in wanted])))

    changes: list[tuple[int, int, str]] = []
    for index, new in results.items():
        if new == texts[index]:
            continue  # Nothing to hide here: formatting stays exactly as it was.
        # A paragraph's whole new text goes in its first run, and the others are emptied.
        for position, run in enumerate(runs[index]):
            changes.append((*run.span(), f'<w:t xml:space="preserve">{xml_escape(new)}</w:t>' if position == 0 else "<w:t></w:t>"))
    result = _apply_changes(xml, changes)

    codes = list(_FIELD_CODE.finditer(result))
    cleaned = anonymizer.anonymize_segments([html.unescape(code.group(2)) for code in codes])
    return _apply_changes(
        result,
        [
            (*code.span(), code.group(1) + xml_escape(new) + code.group(3))
            for code, new in zip(codes, cleaned)
            if new != html.unescape(code.group(2))
        ],
    )


def _apply_changes(text: str, changes: list[tuple[int, int, str]]) -> str:
    out: list[str] = []
    position = 0
    for start, end, new in sorted(changes):
        out.append(text[position:start])
        out.append(new)
        position = end
    out.append(text[position:])
    return "".join(out)


def _link_targets(xml: str, anonymizer: Anonymizer) -> str:
    """Where links go, which can name a person. A web address keeps its host, so a link with nothing to hide
    still works. Parts inside the document aren't touched, or it wouldn't open."""

    def replace(match: re.Match[str]) -> str:
        address = html.unescape(match.group(2))
        web = _LINK_ADDRESS.fullmatch(address)
        new = web.group(1) + anonymizer.anonymize(web.group(2)) if web else anonymizer.anonymize(address)
        return match.group(1) + xml_escape(new, {'"': "&quot;"}) + match.group(3)

    return _RELATIONSHIP.sub(lambda match: _TARGET.sub(replace, match.group()) if _OUTSIDE_LINK.search(match.group()) else match.group(), xml)


# --- PDF ---------------------------------------------------------------------------------------------


def _pdf(data: bytes, anonymizer: Anonymizer) -> Output:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SourceError("reading PDF needs an extra package; install datanyx[pdf]") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SourceError("the PDF is encrypted")
        pages = [page.extract_text() or "" for page in reader.pages]
    except SourceError:
        raise
    except Exception as error:
        raise SourceError(f"input isn't a readable PDF ({type(error).__name__})") from None
    notes = ["the result is the text of the pages, not a PDF"]
    if not any(page.strip() for page in pages):
        notes.append("no text was found: scanned pages are images, which can't be read")
    cleaned = anonymizer.anonymize_segments(pages)
    text = (chr(10) + chr(10)).join(f"--- page {number} ---{chr(10)}{page}" for number, page in enumerate(cleaned, start=1))
    return Output(text.encode("utf-8"), ".txt", notes)


# --- WhatsApp ----------------------------------------------------------------------------------------

_WHATSAPP_LINE = re.compile(
    r"^‎?(?P<stamp>\[?\d{1,4}[./-]\d{1,2}[./-]\d{1,4},?\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[APap]\.?[Mm]\.?)?\]?)"
    r"(?:\s-\s|\s)(?P<rest>.*)$"
)
_SENDER = re.compile(r"^(?P<sender>[^:]{1,60}): (?P<message>.*)$", re.DOTALL)


def looks_like_whatsapp(text: str) -> bool:
    lines = [line for line in text.splitlines()[:60] if line.strip()]
    return sum(bool(_WHATSAPP_LINE.match(line)) for line in lines) >= 3


def _whatsapp(text: str, anonymizer: Anonymizer) -> str:
    """Each message's sender becomes one placeholder, and the messages are anonymized as text."""
    lines = text.split(chr(10))
    plan: list[tuple[str, str | None, str | None]] = []  # (stamp prefix, sender placeholder, text)
    for line in lines:
        carriage = "\r" if line.endswith("\r") else ""
        body = line[:-1] if carriage else line
        header = _WHATSAPP_LINE.match(body)
        if not header:
            plan.append(("", None, body + carriage))
            continue
        prefix = body[: header.start("rest")]
        sender = _SENDER.match(header.group("rest"))
        if sender:
            kind = "PHONE" if re.fullmatch(r"[+\d][\d\s()-]{5,}", sender.group("sender").strip()) else "PERSON"
            plan.append((prefix, anonymizer.placeholder_for(kind, sender.group("sender").strip()), sender.group("message") + carriage))
        else:
            plan.append((prefix, None, header.group("rest") + carriage))
    texts = [item[2] or "" for item in plan]
    cleaned = anonymizer.anonymize_segments(texts)
    out = []
    for (prefix, who, _), new in zip(plan, cleaned):
        out.append(prefix + (f"{who}: " if who else "") + new)
    return chr(10).join(out)


# --- Slack -------------------------------------------------------------------------------------------

_SLACK_KEEP = frozenset({"ts", "thread_ts", "event_ts", "latest_reply", "type", "subtype", "created", "updated", "deleted_ts"})
_SLACK_IDS = frozenset({"user", "user_id", "parent_user_id", "inviter", "bot_id", "creator", "reply_users", "members", "users", "team", "team_id"})
_SLACK_NAMES = frozenset({"real_name", "display_name", "first_name", "last_name", "real_name_normalized", "display_name_normalized"})
_SLACK_CONTACT = {"email": "EMAIL", "phone": "PHONE", "skype": "ID"}


def _is_slack(data: Any) -> bool:
    if not isinstance(data, list) or not data or not all(isinstance(item, dict) for item in data[:20]):
        return False
    sample = data[:50]
    messages = any("ts" in item and ("text" in item or item.get("type") == "message") for item in sample)
    people = any(("real_name" in item or "profile" in item) and "id" in item for item in sample)
    channels = any("members" in item and "creator" in item for item in sample)
    return messages or people or channels


def _slack(text: str, anonymizer: Anonymizer) -> str | None:
    """Replace the people, ids and text in a Slack export; ordinary JSON isn't touched."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not _is_slack(data):
        return None
    slots: list[tuple[Any, Any]] = []  # Where each text value sits, so they can be anonymized together.

    def value_for(key: str, value: str, parent: dict[str, Any] | None, container: Any, where: Any) -> str:
        if not value.strip() or key in _SLACK_KEEP:
            return value
        if key in _SLACK_IDS:
            return anonymizer.placeholder_for("ID", value)
        if key in _SLACK_NAMES or (key in ("name", "username", "user_name") and _is_person_object(parent)):
            return anonymizer.placeholder_for("PERSON", value)
        if key in _SLACK_CONTACT:
            return anonymizer.placeholder_for(_SLACK_CONTACT[key], value)
        slots.append((container, where))
        return value

    def walk(node: Any, key: str = "", parent: dict[str, Any] | None = None) -> Any:
        if isinstance(node, dict):
            copy: dict[str, Any] = {}
            for name, value in node.items():
                copy[name] = value_for(name, value, node, copy, name) if isinstance(value, str) else walk(value, name, node)
            return copy
        if isinstance(node, list):
            items: list[Any] = []
            for index, value in enumerate(node):
                items.append(value_for(key, value, parent, items, index) if isinstance(value, str) else walk(value, key, parent))
            return items
        return node

    result = walk(data)
    values = [container[where] for container, where in slots]
    for (container, where), new in zip(slots, anonymizer.anonymize_segments(values)):
        container[where] = new
    return json.dumps(result, ensure_ascii=False, indent=2 if chr(10) in text.strip() else None)


def _is_person_object(parent: dict[str, Any] | None) -> bool:
    if not parent:
        return False
    return any(key in parent for key in ("real_name", "profile", "is_bot", "team_id", "display_name"))


# --- Restoring ---------------------------------------------------------------------------------------

_ESCAPED_PLACEHOLDER = re.compile(r"&lt;[A-Z_]+_\d+&gt;")


def restore_bytes(data: bytes, suffix: str, mapping: dict[str, str]) -> bytes:
    """Put real values back into a file `anonymize_file` made. HTML and Word keep their markup."""
    if suffix == ".docx":
        return _restore_docx(data, mapping)
    text = data.decode("utf-8-sig")
    if suffix in (".html", ".htm"):
        # A stylesheet holds placeholders as they are; everywhere else they are escaped.
        raw = PLACEHOLDER.sub(lambda m: mapping.get(m.group(), m.group()), text)
        return _restore_escaped(raw, mapping, quote=True).encode("utf-8")
    return PLACEHOLDER.sub(lambda m: mapping.get(m.group(), m.group()), text).encode("utf-8")


def _restore_escaped(text: str, mapping: dict[str, str], quote: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        key = html.unescape(match.group())
        value = mapping.get(key)
        return match.group() if value is None else (html.escape(value, quote=quote) if quote else xml_escape(value))

    return _ESCAPED_PLACEHOLDER.sub(replace, text)


def _restore_docx(data: bytes, mapping: dict[str, str]) -> bytes:
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SourceError("input isn't a valid .docx file") from None
    buffer = io.BytesIO()
    with source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                content = _restore_escaped(content.decode("utf-8"), mapping, quote=False).encode("utf-8")
            target.writestr(info, content)
    return buffer.getvalue()
