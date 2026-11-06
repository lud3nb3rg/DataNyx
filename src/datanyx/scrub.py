"""Remove data from error messages and program output, keeping what's needed to debug.

When an agent's script runs against real data, its errors and output can hold that
data: `KeyError: 'jane@example.com'`, a traceback through a path with a username in
it, a printed row. This module reduces such text to its skeleton: exception types,
line numbers, file names in your own code and the code itself stay, and the rest goes.

What it does, in order:

1. Secrets: values of environment variables that look like credentials, and the
   password inside a URL, are replaced.
2. Paths: your home folder, working folder and username are shortened.
3. In a Python traceback, the code under a `File` line is left alone apart from terms to
   hide, and a `File` line loses only what its path gives away. Every other line is treated
   as a message.
4. In messages, anything in quotes is replaced unless it is a name the schema made
   public: error messages quote values far more often than names.
5. Detectors (emails, IDs, and names if a NER model is used) replace what they find.
6. Long numbers and decimals are replaced. Short integers stay, because they are
   mostly line numbers and counts.

This is best effort, like text anonymization. A value that is neither quoted nor
recognised, such as a bare name in a log line, gets through unless NER catches it.
"""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field

from .schema import Array, DataSchema, Node, Object, Union
from .text import Anonymizer, Detector, PatternDetector, SecretDetector
from .text.secrets import redact_url_credentials

VALUE = "<VALUE>"
NUMBER = "<NUMBER>"
SECRET = "<SECRET>"

_SECRET_NAME = re.compile(
    r"(?i)(key|token|secret|passw|credential|auth|dsn|connection|database_url|_url$|^url$)"
)
_MIN_SECRET_LENGTH = 6

_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):\s*$")
_FILE_LINE = re.compile(r'^\s+File ".*", line \d+')
_REPEATED_LINE = re.compile(r"^\s+\[Previous line repeated \d+ more times?\]\s*$")
_CODE_LINE = re.compile(r"^ {4}")
# Python 3.13 prints a statement that spans lines in full, with the middle cut, so a frame can have a few.
_MAX_CODE_LINES = 12
_CHAIN = re.compile(r"^(During handling of the above exception|The above exception was the direct cause)")
_EXCEPTION_LINE = re.compile(
    r"^((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Warning|Exit|Interrupt|Failure))(:\s*)(.*)$"
)
_QUOTED = re.compile(r"""(?<!\w)(?:[bBrRuUfF]{1,2})?(['"])((?:(?!\1)[^\n\\]|\\.){0,100000})\1(?!\w)""")
_NUMBER = re.compile(r"(?<![\w.])(?:\d{4,}|\d+\.\d+)(?![\w])")
_FILE_PATH = re.compile(r'^(\s+File ")(.*?)(".*)$')

# Words that are about how the data is stored rather than what is in it.
_COMMON_WORDS = frozenset(
    {
        "object", "int64", "int32", "float64", "float32", "bool", "boolean", "string", "category",
        "utf-8", "utf8", "r", "w", "rb", "wb", "a", "None", "True", "False", "datetime64[ns]", "str", "int", "float",
    }
)


@dataclass(frozen=True)
class ScrubPolicy:
    """What to keep and what to look for.

    allowed_names: quoted words to leave alone, normally names from a schema.
    hide_terms: terms to always remove, ignoring case.
    detectors: what finds sensitive text. Add `SpacyDetector` to catch names.
    """

    allowed_names: frozenset[str] = frozenset()
    hide_terms: tuple[str, ...] = ()
    detectors: tuple[Detector, ...] = field(default_factory=lambda: (PatternDetector(), SecretDetector()))
    redact_numbers: bool = True


def scrub(text: str, policy: ScrubPolicy | None = None) -> str:
    """`text` with data removed, as described in the module docstring."""
    policy = policy or ScrubPolicy()
    text = _hide_secrets(text)
    text = _shorten_paths(text)

    lines = text.split("\n")
    kinds = _classify(lines)
    messages = [index for index, kind in enumerate(kinds) if kind == "message"]
    structure = [index for index, kind in enumerate(kinds) if kind == "structure"]
    anonymizer = Anonymizer(policy.detectors, policy.hide_terms)

    prefixes: dict[int, str] = {}
    bodies: list[str] = []
    for index in messages:
        match = _EXCEPTION_LINE.match(lines[index])
        prefix, body = (match.group(1) + match.group(2), match.group(3)) if match else ("", lines[index])
        prefixes[index] = prefix
        bodies.append(_redact_quoted(body, policy.allowed_names))

    for index, body in zip(messages, _detect(bodies, anonymizer)):
        if policy.redact_numbers:
            body = _NUMBER.sub(NUMBER, body)
        lines[index] = prefixes[index] + body

    # A traceback's code is the agent's own, so it stays, except for terms to hide: "always remove
    # this term" has no exceptions. A `File` line's path isn't code, so patterns look at it too, though
    # not a language model, which would only mangle a path.
    patterns = [detector for detector in policy.detectors if not getattr(detector, "uses_model", False)]
    for index in structure:
        path = _FILE_PATH.match(lines[index])
        if path:
            lines[index] = path.group(1) + anonymizer.anonymize(path.group(2), detectors=patterns) + anonymizer.anonymize(path.group(3), detectors=())
        else:
            lines[index] = anonymizer.anonymize(lines[index], detectors=())
    return "\n".join(lines)


def shape(text: str) -> str:
    """How much output there was, without any of it."""
    if not text:
        return "no output"
    count = text.count("\n") + (0 if text.endswith("\n") else 1)
    return f"{count} line{'s' if count != 1 else ''}, {len(text):,} characters"


def visible_names(schema: DataSchema) -> frozenset[str]:
    """Every name the schema shows. These are public, so errors may keep them."""
    names: set[str] = set()
    for entity in schema.entities:
        if entity.name:
            names.update(_name_forms(entity.name))
        _collect(entity.node, names)
    return frozenset(names)


# --- steps -------------------------------------------------------------------


def _hide_secrets(text: str) -> str:
    # Longest first: a password inside a database URL mustn't break the URL's own match.
    values = {value for name, value in os.environ.items() if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LENGTH}
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, SECRET)
    return redact_url_credentials(text)


def _shorten_paths(text: str) -> str:
    """Home and working folders become `~` and `.`; the username becomes <USER>."""
    for folder, replacement in ((os.getcwd(), "."), (os.path.expanduser("~"), "~")):  # Longest first.
        if len(folder) > 3:
            for variant in {folder, folder.replace("\\", "/"), folder.replace("/", "\\")}:
                text = re.sub(re.escape(variant), replacement, text, flags=re.IGNORECASE)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) >= 3:
        text = re.sub(rf"(?<![\w]){re.escape(user)}(?![\w])", "<USER>", text, flags=re.IGNORECASE)
    return text


def _classify(lines: list[str]) -> list[str]:
    """Mark each line `structure` (a traceback's own lines) or `message`."""
    kinds = []
    in_traceback = False
    code_lines = _MAX_CODE_LINES  # How many more lines may be code under the last `File` line.
    for line in lines:
        if _TRACEBACK.match(line):
            kinds.append("structure")
            in_traceback, code_lines = True, 0
        elif _CHAIN.match(line):
            kinds.append("structure")
        elif in_traceback and (_FILE_LINE.match(line) or _REPEATED_LINE.match(line)):
            kinds.append("structure")
            code_lines = _MAX_CODE_LINES
        elif in_traceback and code_lines and _CODE_LINE.match(line):
            kinds.append("structure")  # The code line, and the ^^^^ markers.
            code_lines -= 1
        else:
            kinds.append("message")
            in_traceback = False
    return kinds


def _redact_quoted(text: str, allowed: frozenset[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        inner = match.group(2)
        # An empty string holds no data, and "got an empty string" helps in debugging.
        return match.group(0) if not inner or inner in allowed or inner in _COMMON_WORDS else VALUE

    return _QUOTED.sub(replace, text)


def _detect(bodies: list[str], anonymizer: Anonymizer) -> list[str]:
    """Run the detectors over all message text at once, so one person keeps one placeholder."""
    if not bodies:
        return bodies
    result = anonymizer.anonymize("\n".join(bodies)).split("\n")
    if len(result) == len(bodies):
        return result
    # A match spanned lines, which breaks the pairing, so do each line on its own.
    return [anonymizer.anonymize(body) for body in bodies]


def _name_forms(name: str) -> set[str]:
    stem = re.split(r"[./#\\]", name)[0] if "." in name else name
    return {name, stem, *re.split(r"[/#\\]", name)}


def _collect(node: Node, names: set[str]) -> None:
    if isinstance(node, Object):
        for name, item in node.fields.items():
            names.add(name)
            _collect(item.node, names)
        if node.extra is not None:
            _collect(node.extra, names)
    elif isinstance(node, Array):
        _collect(node.items, names)
    elif isinstance(node, Union):
        for option in node.options:
            _collect(option, names)
