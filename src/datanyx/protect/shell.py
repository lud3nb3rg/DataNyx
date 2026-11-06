"""Reading a shell command the way a shell would: quotes, escapes, redirections, substitutions, heredocs and braces."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
# Words that come before the program. They run what follows, so they aren't the program themselves.
_WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "command", "exec", "nice", "timeout", "xargs", "{", "!", "then", "do", "else", "elif", "if", "while", "until"})
_WRAPPER_VALUES = frozenset({"-u", "-g", "-n", "-C", "-p", "-I", "-L", "-P", "-s", "-k", "--user", "--group", "--adjustment"})
_KEY_VALUE = re.compile(r"[\w.-]+=(.+)")
_MAX_BRACE_RESULTS = 256


@dataclass
class _Segment:
    """One simple command: its words with the shell's quoting removed, and what it redirects."""

    text: str
    words: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)  # Here-documents and here-strings: text, not paths.
    inner: list[str] = field(default_factory=list)  # Code in substitutions and subshells: run from this command's folder.


def _closing(text: str, start: int) -> int:
    """Where the parenthesis opened just before `start` closes, or the end of the text."""
    depth, position = 1, start
    quote = None
    while position < len(text):
        char = text[position]
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "\\":
            position += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return len(text)


def _parse_shell(command: str, powershell: bool = False) -> list[_Segment]:
    """The simple commands in `command`. Each holds the code inside its `$(...)`, backticks, `<(...)` or `(...)`."""
    segments: list[_Segment] = []
    current = _Segment("")
    buffer: list[str] = []
    in_word = False
    pending: str | None = None  # What the next word is: "read", "write", "input" or "heredoc" (its tag).
    heredocs: list[tuple[str, _Segment]] = []  # Tags whose text starts on the next line.
    start = position = 0
    length = len(command)

    def end_word() -> None:
        nonlocal in_word, pending
        if in_word:
            word = "".join(buffer)
            if pending == "read":
                current.reads.append(word)
            elif pending == "write":
                current.writes.append(word)
            elif pending == "input":
                current.inputs.append(word)
            elif pending == "heredoc":
                heredocs.append((word, current))
            elif pending is None:
                current.words.append(word)
            pending = None
        buffer.clear()
        in_word = False

    def end_segment(at: int) -> None:
        nonlocal current, start, pending
        end_word()
        pending = None
        if current.words or current.reads or current.writes or current.inputs or current.inner:
            current.text = command[start:at]
            segments.append(current)
        current = _Segment("")
        start = at + 1

    while position < length:
        char = command[position]
        if char == "'":
            closing = command.find("'", position + 1)
            if closing == -1:  # An apostrophe in plain text, not a quote.
                buffer.append(char)
                position += 1
            else:
                buffer.append(command[position + 1 : closing])
                position = closing + 1
            in_word = True
        elif char == '"':
            position = _double_quoted(command, position, buffer, current.inner, powershell)
            in_word = True
        elif char == "\\":
            if command[position + 1 : position + 2] in ("\n", "\r"):
                position += 2  # A line continuation.
            else:
                buffer.append(command[position : position + 2])  # Kept: on Windows it separates a path.
                position += 2
                in_word = True
        elif char == "`" and powershell:  # There it is the escape character.
            buffer.append(command[position + 1 : position + 2])
            position += 2
            in_word = True
        elif char == "`":
            closing = command.find("`", position + 1)
            if closing == -1:
                buffer.append(char)
                position += 1
            else:
                current.inner.append(command[position + 1 : closing])
                buffer.append(command[position : closing + 1])
                position = closing + 1
            in_word = True
        elif command.startswith("$(", position):
            closing = _closing(command, position + 2)
            current.inner.append(command[position + 2 : closing])
            buffer.append(command[position : closing + 1])
            position = closing + 1
            in_word = True
        elif command.startswith(("<(", ">("), position):
            end_word()
            closing = _closing(command, position + 2)
            current.inner.append(command[position + 2 : closing])
            position = closing + 1
        elif char == "(" and not in_word:  # A subshell: its `cd` doesn't outlast it.
            closing = _closing(command, position + 1)
            current.inner.append(command[position + 1 : closing])
            position = closing + 1
        elif char == "#" and not in_word:  # A comment, which nothing runs.
            newline = command.find("\n", position)
            position = length if newline == -1 else newline
        elif char in " \t":
            end_word()
            position += 1
        elif command.startswith("&>", position):
            end_word()
            pending = "write"
            position += 3 if command.startswith("&>>", position) else 2
        elif char in "\r\n;|&()":
            end_segment(position)
            position += 1
            if char == "\n" and heredocs:
                position = _read_heredocs(command, position, heredocs)
                start = position
        elif char in "<>":
            if (in_word and "".join(buffer).isdigit()) or (powershell and char == ">" and "".join(buffer) == "*"):  # The descriptor in 2>file or 0<file.
                buffer.clear()
                in_word = False
            end_word()
            stop = position
            while stop < length and command[stop] in "<>":
                stop += 1
            operator = command[position:stop]
            if command[stop : stop + 1] == "&":  # 2>&1, or a descriptor with no file.
                stop += 1
                while stop < length and (command[stop].isdigit() or command[stop] == "-"):
                    stop += 1
                position = stop
                continue
            if command[stop : stop + 1] == "|":
                stop += 1
            if operator == "<<<":
                pending = "input"
            elif operator.startswith("<<"):
                pending = "heredoc"
                if command[stop : stop + 1] == "-":
                    stop += 1
            else:
                pending = "read" if operator == "<" else "write"
            position = stop
        else:
            buffer.append(char)
            in_word = True
            position += 1
    end_segment(length)
    return segments


def _read_heredocs(command: str, position: int, heredocs: list[tuple[str, _Segment]]) -> int:
    """Give each waiting here-document its text, which isn't commands. Returns the position after them."""
    for tag, segment in heredocs:
        closing = re.compile(rf"^\t*{re.escape(tag)}\r?$", re.MULTILINE).search(command, position)
        segment.inputs.append(command[position : closing.start()] if closing else command[position:])
        position = closing.end() + 1 if closing else len(command)
    heredocs.clear()
    return position


def _double_quoted(command: str, start: int, buffer: list[str], inner: list[str], powershell: bool = False) -> int:
    """Read a double-quoted stretch into `buffer`. Returns the position after it."""
    closing = _closing_quote(command, start + 1)
    if closing == -1:  # Not really a quote.
        buffer.append('"')
        return start + 1
    position = start + 1
    while position < closing:
        char = command[position]
        if char == "\\" and command[position + 1 : position + 2] in ('"', "\\", "$", "`"):
            buffer.append(command[position + 1])
            position += 2
        elif command.startswith("$(", position):
            end = _closing(command, position + 2)
            inner.append(command[position + 2 : end])
            buffer.append(command[position : end + 1])
            position = end + 1
        elif char == "`" and powershell:
            buffer.append(command[position + 1 : position + 2])
            position += 2
        elif char == "`":
            end = command.find("`", position + 1)
            end = closing if end == -1 else end
            inner.append(command[position + 1 : end])
            buffer.append(command[position : end + 1])
            position = end + 1
        else:
            buffer.append(char)
            position += 1
    return closing + 1


def _closing_quote(command: str, position: int) -> int:
    while position < len(command):
        char = command[position]
        if char == "\\":
            position += 2
            continue
        if char == '"':
            return position
        if command.startswith("$(", position):
            position = _closing(command, position + 2) + 1
            continue
        position += 1
    return -1


def _without_wrappers(words: list[str]) -> list[str]:
    tokens = list(words)
    while tokens:
        if _ASSIGNMENT.match(tokens[0]):
            tokens.pop(0)
        elif _prog(tokens[0]) in _WRAPPERS:
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):  # `sudo -u me rm`: the program is still to come.
                option = tokens.pop(0)
                if option in _WRAPPER_VALUES and tokens:
                    tokens.pop(0)
        else:
            break
    return tokens


def _expand_braces(word: str) -> list[str]:
    """`data/{a,b}.csv` as a shell would expand it. Words without a list are returned as they are."""
    results: list[str] = []
    pending = [word]
    while pending:
        current = pending.pop()
        found = _brace_list(current)
        if found is None:
            results.append(current)
            continue
        opening, closing, options = found
        pending.extend(current[:opening] + option + current[closing + 1 :] for option in reversed(options))
        if len(results) + len(pending) > _MAX_BRACE_RESULTS:  # Past this, keep what's left unexpanded.
            results.extend(pending)
            break
    return results


def _brace_list(word: str) -> tuple[int, int, list[str]] | None:
    """The first `{a,b}` in the word: where it starts and ends, and its options."""
    for opening, character in enumerate(word):
        if character != "{" or (opening and word[opening - 1] == "$"):
            continue
        depth, begin = 0, opening + 1
        options: list[str] = []
        for position in range(opening, len(word)):
            current = word[position]
            if current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    if options:
                        options.append(word[begin:position])
                        return opening, position, options
                    break
            elif current == "," and depth == 1:
                options.append(word[begin:position])
                begin = position + 1
    return None


def _prog(token: str) -> str:
    name = os.path.basename(token.replace("\\", "/")).lower()
    return name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name


def _plain(token: str) -> str:
    """A token without shell escapes, unless they are what separates a Windows path."""
    return token if os.name == "nt" else token.replace("\\", "")


def _candidates(token: str) -> list[str]:
    """The paths a token may mean: itself, its brace expansions, without shell escapes, and an option's value
    (--opt=x, -fx, if=x, @x)."""
    found: list[str] = []
    if token.startswith("-"):
        if "=" in token:
            found.append(token.split("=", 1)[1])
        elif not token.startswith("--") and len(token) > 2 and ("/" in token or "\\" in token or "." in token):
            found.append(token[2:])  # grep -fdata/x.csv
    else:
        found.append(token)
        assignment = _KEY_VALUE.fullmatch(token)
        if assignment:
            found.append(assignment[1])  # dd if=data/x.csv
    candidates: list[str] = []
    for value in found:
        for expanded in _expand_braces(value):
            candidates.append(expanded)
            if expanded.startswith("@"):
                candidates.append(expanded[1:])  # curl -d @data/x.csv
            if "\\" in expanded:
                candidates.append(expanded.replace("\\", ""))  # da\\ta/x.csv is data/x.csv to a POSIX shell.
    return [candidate for candidate in candidates if candidate]


def _abbreviates(option: str, name: str) -> bool:
    """Whether a command-line word is this option, or a shortened form of a long one."""
    given = option.split("=")[0]
    return given == name or (len(given) > 2 and given.startswith("--") and name.startswith(given))


def _has_encoded_command(tokens: list[str]) -> bool:
    return any(len(word) > 2 and "-encodedcommand".startswith(word.lower()) for word in tokens[1:])


def _split_command(rest: list[str]) -> tuple[str, list[str]]:
    """(command, every other argument) for `datanyx [--config FILE] [--no-config] COMMAND ...`.

    The options before the command are returned with the rest, so they are checked too."""
    index = 0
    while index < len(rest) and rest[index].startswith("--"):
        index += 2 if rest[index] == "--config" else 1
    if index >= len(rest):
        return "", rest
    return rest[index], [*rest[:index], *rest[index + 1 :]]
