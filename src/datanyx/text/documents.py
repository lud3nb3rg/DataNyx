"""Anonymize documents in the formats people actually have, keeping their structure.

| Format | Result |
|---|---|
| text, Markdown, logs, CSV, JSON and similar | the same file, with placeholders |
| HTML | the same page: tags and layout stay, text and some attributes change |
| Word (.docx) | the same document: styles, tables and layout stay; a paragraph that had something to hide loses the formatting inside it; author and company are cleared |
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
        return Output(_html(_decode(data), anonymizer).encode("utf-8"), suffix)
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
_COMMENT = re.compile(r"(<!--)(.*?)(-->)", re.DOTALL)
_ATTRIBUTE = re.compile(
    r"""(\s(?:title|alt|placeholder|value|content|href|src|srcset|action|cite|poster|aria-[\w-]+|label|summary|data-[\w.:-]*)\s*=\s*)"""
    r"""(?:(["'])(.*?)\2|([^\s"'=<>`]+))""",
    re.IGNORECASE | re.DOTALL,
)
_SKIPPED_TAG = re.compile(r"<(script|style)\b[^>]*>|</(script|style)\s*>", re.IGNORECASE)
# In a script or stylesheet only strings and comments can hold data: the code around them stays.
_CODE_TEXT = re.compile(
    r""""(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`|/\*.*?\*/|(?<![:\w])//[^\n]*""",
    re.DOTALL,
)
_SLOT = re.compile("\ue000(\\d+)\ue001")
# How a slot's new text goes back: as page text or a quoted attribute (escaped), an unquoted
# attribute (escaped, and given quotes), or raw, inside a comment or script.
_TEXT, _QUOTED, _UNQUOTED, _RAW = "text", "quoted", "unquoted", "raw"


def _html(text: str, anonymizer: Anonymizer) -> str:
    """Anonymize a page's text, comments, script strings and descriptive attributes. The markup stays."""
    raws: list[str] = []  # What each slot held, as the anonymizer should read it.
    originals: list[str] = []
    modes: list[str] = []

    def slot(raw: str, original: str, mode: str) -> str:
        raws.append(raw if mode == _RAW else html.unescape(raw))
        originals.append(original)
        modes.append(mode)
        return f"\ue000{len(raws) - 1}\ue001"

    def attribute(match: re.Match[str]) -> str:
        prefix, quote, value, bare = match.groups()
        if bare is not None:
            return f"{prefix}{slot(bare, bare, _UNQUOTED)}"
        return f"{prefix}{quote}{slot(value, value, _QUOTED)}{quote}"

    def comment(match: re.Match[str]) -> str:
        return match.group(1) + slot(match.group(2), match.group(2), _RAW) + match.group(3)

    def code_text(match: re.Match[str]) -> str:
        whole = match.group()
        inner = whole[2:-2] if whole.startswith("/*") else whole[2:] if whole.startswith("//") else whole[1:-1]
        at = whole.index(inner) if inner else 0
        return whole[:at] + (slot(inner, inner, _RAW) if inner.strip() else inner) + whole[at + len(inner) :]

    out: list[str] = []
    position = 0
    in_code = False
    for match in _TAG.finditer(text):
        between = text[position : match.start()]
        if in_code:
            out.append(_CODE_TEXT.sub(code_text, between))
        elif not between.strip():
            out.append(between)
        else:
            lead = between[: len(between) - len(between.lstrip())]
            trail = between[len(between.rstrip()) :]
            out.append(lead + slot(between.strip(), between.strip(), _TEXT) + trail)
        tag = match.group()
        skipper = _SKIPPED_TAG.fullmatch(tag)
        if skipper and not tag.endswith("/>"):
            in_code = skipper.group(1) is not None
        if tag.startswith("<!--"):
            out.append(_COMMENT.sub(comment, tag))
        else:
            out.append(_ATTRIBUTE.sub(attribute, tag))
        position = match.end()
    tail = text[position:]
    if in_code:
        out.append(_CODE_TEXT.sub(code_text, tail))
    elif tail.strip():
        out.append(tail[: len(tail) - len(tail.lstrip())] + slot(tail.strip(), tail.strip(), _TEXT))
        out.append(tail[len(tail.rstrip()) :])
    else:
        out.append(tail)

    results = anonymizer.anonymize_segments(raws)

    def fill(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if results[index] == raws[index]:
            return originals[index]  # Unchanged, so the original spelling (and entities) stay.
        if modes[index] == _RAW:
            return results[index]
        escaped = html.escape(results[index], quote=modes[index] != _TEXT)
        return f'"{escaped}"' if modes[index] == _UNQUOTED else escaped

    return _SLOT.sub(fill, "".join(out))


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
# Text with no place in the visible paragraph: field codes, and what tracked changes deleted.
_SEPARATE_TEXT = re.compile(r"(<w:(?:instrText|delInstrText|delText)(?:\s[^>]*)?>)(.*?)(</w:(?:instrText|delInstrText|delText)>)", re.DOTALL)
_TEXT_PART = re.compile(r"word/(?:glossary/)?(document|header\d*|footer\d*|comments\w*|footnotes|endnotes)\.xml")
_PERSONAL_PROPERTIES = re.compile(
    r"(<(?:dc:creator|cp:lastModifiedBy|dc:title|dc:subject|cp:keywords|dc:description|Company|Manager|HyperlinkBase)\b[^>]*>).*?(</)",
    re.DOTALL,
)
_PEOPLE_ATTRIBUTE = re.compile(r"""(\b[wW]\d*:(?:author|initials|userId|providerId)=")([^"]*)(")""")
_DESCRIBING_ATTRIBUTE = re.compile(r"""(\b(?:descr|title|w:tooltip|w:alias)=")([^"]*)(")""")
_PICTURE_NAME = re.compile(r"""(<(?:wp:docPr|pic:cNvPr)\b[^>]*?\bname=")([^"]*)(")""")
_XML_TEXT = re.compile(r">[^<]+<")
_RELATIONSHIP = re.compile(r"<Relationship\b[^>]*>")
_TARGET = re.compile(r'(Target=")([^"]*)(")')
_OUTSIDE_LINK = re.compile(r'TargetMode="External"|Target="mailto:', re.IGNORECASE)
_DATA_PART = re.compile(r"customXml/item\d*\.xml|docProps/custom\.xml")
_UNREAD_PART = re.compile(r"word/(?:media|embeddings)/.+|docProps/thumbnail\.\w+")


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
        unread = 0
        cleared_data = False
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                name = info.filename
                try:
                    content = source.read(name)
                except (zipfile.BadZipFile, EOFError, OSError, NotImplementedError, RuntimeError, ValueError):
                    raise SourceError("the .docx file is damaged") from None
                except Exception as error:  # zlib.error, for one.
                    raise SourceError(f"the .docx file is damaged ({type(error).__name__})") from None
                if _UNREAD_PART.fullmatch(name):
                    unread += 1
                elif _TEXT_PART.fullmatch(name):
                    content = _docx_text_part(_xml_text(content), anonymizer).encode("utf-8")
                elif name in ("docProps/core.xml", "docProps/app.xml"):
                    content = _PERSONAL_PROPERTIES.sub(r"\1\2", _xml_text(content)).encode("utf-8")
                elif _DATA_PART.fullmatch(name):
                    content = _XML_TEXT.sub("><", _xml_text(content)).encode("utf-8")
                    cleared_data = True
                elif name.endswith(".rels"):
                    content = _external_targets(_xml_text(content), anonymizer).encode("utf-8")
                elif name.endswith(".xml") and name.startswith("word/"):
                    content = _people_attributes(_xml_text(content), anonymizer).encode("utf-8")
                target.writestr(info, content)
    notes = ["document properties such as author and company were cleared, and tracked-change authors replaced"]
    if cleared_data:
        notes.append("custom properties and embedded data parts were cleared")
    if unread:
        notes.append(f"{unread} embedded image(s), object(s) or preview(s) were not processed")
    return Output(buffer.getvalue(), ".docx", notes)


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

    codes = list(_SEPARATE_TEXT.finditer(result))
    cleaned = anonymizer.anonymize_segments([html.unescape(code.group(2)) for code in codes])
    result = _apply_changes(
        result,
        [
            (*code.span(), code.group(1) + xml_escape(new) + code.group(3))
            for code, new in zip(codes, cleaned)
            if new != html.unescape(code.group(2))
        ],
    )
    return _people_attributes(_described_attributes(result, anonymizer), anonymizer)


def _apply_changes(text: str, changes: list[tuple[int, int, str]]) -> str:
    out: list[str] = []
    position = 0
    for start, end, new in sorted(changes):
        out.append(text[position:start])
        out.append(new)
        position = end
    out.append(text[position:])
    return "".join(out)


def _people_attributes(xml: str, anonymizer: Anonymizer) -> str:
    """Who made a tracked change or comment, wherever Word records it."""

    def replace(match: re.Match[str]) -> str:
        value = html.unescape(match.group(2)).strip()
        return match.group(1) + (xml_escape(anonymizer.placeholder_for("PERSON", value)) if value else "") + match.group(3)

    return _PEOPLE_ATTRIBUTE.sub(replace, xml)


def _described_attributes(xml: str, anonymizer: Anonymizer) -> str:
    """Alt text, titles and picture names are text someone wrote."""

    def replace(match: re.Match[str]) -> str:
        value = html.unescape(match.group(2))
        return match.group(1) + xml_escape(anonymizer.anonymize(value), {'"': "&quot;"}) + match.group(3)

    return _PICTURE_NAME.sub(replace, _DESCRIBING_ATTRIBUTE.sub(replace, xml))


def _external_targets(xml: str, anonymizer: Anonymizer) -> str:
    """Where links go, which can name a person. Parts inside the document stay, or it wouldn't open."""

    def target(match: re.Match[str]) -> str:
        address = html.unescape(match.group(2))
        return match.group(1) + xml_escape(anonymizer.anonymize(address), {'"': "&quot;"}) + match.group(3)

    def relationship(match: re.Match[str]) -> str:
        whole = match.group()
        return _TARGET.sub(target, whole) if _OUTSIDE_LINK.search(whole) else whole

    return _RELATIONSHIP.sub(relationship, xml)


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
        # Comments and scripts hold placeholders as they are; everywhere else they are escaped.
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
            if info.filename.endswith((".xml", ".rels")):
                content = _restore_escaped(content.decode("utf-8"), mapping, quote=False).encode("utf-8")
            target.writestr(info, content)
    return buffer.getvalue()
