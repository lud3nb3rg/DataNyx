"""Read a shell command the way a shell would, well enough to see what it touches.

The guard has to know which words a command really receives. Splitting on whitespace misses
`cat<x.csv`, `da"ta"/x.csv`, `$(cat x.csv)`, `(cat x.csv)`, `{x,}.csv` and `a & cat x.csv`, so
this reads quotes, operators, redirections, substitutions, subshells, brace expansion and
here-documents. It doesn't run anything or follow variables, and it isn't a full grammar:
keywords such as `if` and `for` come through as ordinary words, which is enough here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MAX_DEPTH = 32
_MAX_BRACE_RESULTS = 256
_WORD_END = frozenset(" \t\r\n;&|<>()")
_POSIX_ESCAPABLE = frozenset(" \t\r\n;&|<>()'\"$#`")


@dataclass
class Command:
    """One simple command. `nested` holds substitutions and subshells, each with its own working folder."""

    words: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)  # < FILE
    writes: list[str] = field(default_factory=list)  # > FILE, >> FILE, &> FILE
    inputs: list[str] = field(default_factory=list)  # Here-documents and here-strings: text, not paths.
    nested: list[list[Command]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.words or self.reads or self.writes or self.inputs or self.nested)


def parse(text: str, powershell: bool = False) -> list[Command]:
    """The commands in `text`, in order. Raises ValueError on absurdly deep nesting."""
    return _Parser(text, powershell, 0).parse()


def expand_braces(word: str) -> list[str]:
    """`data/{a,b}.csv` as the shell would expand it. Words without a list are returned as they are."""
    results, pending = [], [word]
    while pending:
        current = pending.pop()
        span = _brace_list(current)
        if span is None:
            results.append(current)
            continue
        start, end, options = span
        pending.extend(current[:start] + option + current[end + 1 :] for option in reversed(options))
        if len(results) + len(pending) > _MAX_BRACE_RESULTS:  # Past this, keep what's left unexpanded.
            results.extend(pending)
            break
    return results


def _brace_list(word: str) -> tuple[int, int, list[str]] | None:
    """The first `{a,b}` in the word: where it starts and ends, and its options."""
    for start, character in enumerate(word):
        if character != "{" or (start and word[start - 1] == "$"):
            continue
        depth, begin = 0, start + 1
        options: list[str] = []
        for position in range(start, len(word)):
            current = word[position]
            if current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    if options:
                        options.append(word[begin:position])
                        return start, position, options
                    break
            elif current == "," and depth == 1:
                options.append(word[begin:position])
                begin = position + 1
    return None


class _Parser:
    def __init__(self, text: str, powershell: bool, depth: int) -> None:
        if depth > _MAX_DEPTH:
            raise ValueError("nested too deeply")
        self.text = text
        self.powershell = powershell
        self.depth = depth
        self.position = 0
        self.commands: list[Command] = []
        self.command = Command()
        self.word: str | None = None
        self.quoted = False
        self.pending: list[str] | None = None  # Where the next word goes, after a redirection.
        self.skips: dict[int, int] = {}  # Here-document bodies, which aren't commands.

    def parse(self) -> list[Command]:
        length = len(self.text)
        while self.position < length:
            if self.position in self.skips:
                self.end_command()
                self.position = self.skips[self.position]
                continue
            self.step(self.text[self.position])
        self.end_command()
        return self.commands

    def step(self, character: str) -> None:
        text, at = self.text, self.position
        if character in " \t":
            self.end_word()
        elif character in "\r\n;":
            self.end_command()
        elif character == "#" and self.word is None:
            newline = text.find("\n", at)
            self.position = len(text) if newline < 0 else newline
            return
        elif character == "'":
            self.single_quote()
            return
        elif character == '"':
            self.double_quote()
            return
        elif character == "\\":
            self.backslash()
            return
        elif character == "`":
            self.backtick()
            return
        elif character == "$" and text.startswith("$(", at):
            self.substitution(at + 1)
            return
        elif character == "$" and text.startswith("${", at):
            close = text.find("}", at)
            close = len(text) - 1 if close < 0 else close
            self.add(text[at : close + 1])
            self.position = close + 1
            return
        elif character == "&":
            if text.startswith("&>", at):
                self.end_word()
                self.pending = self.command.writes
                self.position += 3 if text.startswith("&>>", at) else 2
                return
            self.end_command()
            self.position += 2 if text.startswith("&&", at) else 1
            return
        elif character == "|":
            self.end_command()
            self.position += 2 if text.startswith(("||", "|&"), at) else 1
            return
        elif character in "<>":
            self.redirect(character)
            return
        elif character == "(":
            if self.word is None:
                self.substitution(at)
                return
            self.add(character)
        elif character == ")":
            self.end_word()
        else:
            self.add(character)
        self.position += 1

    # --- words -------------------------------------------------------------------

    def add(self, text: str) -> None:
        self.word = (self.word or "") + text

    def end_word(self) -> None:
        if self.word is None:
            return
        word, quoted = self.word, self.quoted
        self.word, self.quoted = None, False
        target = self.pending if self.pending is not None else self.command.words
        is_redirect, self.pending = self.pending is not None, None
        if is_redirect and target is not self.command.inputs and (word == "-" or word.isdigit()):
            return  # `2>&1` names a stream, not a file.
        target.extend([word] if quoted or self.powershell else expand_braces(word))

    def end_command(self) -> None:
        self.end_word()
        self.pending = None
        if not self.command.empty:
            self.commands.append(self.command)
        self.command = Command()

    # --- quoting -----------------------------------------------------------------

    def single_quote(self) -> None:
        close = self.text.find("'", self.position + 1)
        close = len(self.text) if close < 0 else close
        self.add(self.text[self.position + 1 : close])
        self.quoted = True
        self.position = close + 1

    def double_quote(self) -> None:
        text, at = self.text, self.position + 1
        self.add("")
        self.quoted = True
        while at < len(text) and text[at] != '"':
            character = text[at]
            if character == "\\" and not self.powershell and at + 1 < len(text) and text[at + 1] in '"\\$`':
                self.add(text[at + 1])
                at += 2
            elif character == "`" and self.powershell:
                self.add(text[at + 1 : at + 2])
                at += 2
            elif character == "`":
                close = self.closing_backtick(at)
                self.nest(text[at + 1 : close])
                at = close + 1
            elif text.startswith("$(", at):
                close = self.closing_paren(at + 1)
                self.nest(text[at + 2 : close])
                at = close + 1
            else:
                self.add(character)
                at += 1
        self.position = at + 1

    def backslash(self) -> None:
        following = self.text[self.position + 1 : self.position + 2]
        if self.powershell or not following:
            self.add("\\")
            self.position += 1
        elif following == "\n":
            self.position += 2  # A continued line.
        elif following in _POSIX_ESCAPABLE:
            self.add(following)
            self.quoted = True
            self.position += 2
        else:
            self.add("\\")  # Kept, so that Windows paths survive. The guard also tries the word without it.
            self.position += 1

    def backtick(self) -> None:
        at = self.position
        if self.powershell:  # There it's the escape character.
            self.add(self.text[at + 1 : at + 2])
            self.position += 2
            return
        close = self.closing_backtick(at)
        self.nest(self.text[at + 1 : close])
        self.position = close + 1

    def closing_backtick(self, opening: int) -> int:
        at = opening + 1
        while at < len(self.text) and self.text[at] != "`":
            at += 2 if self.text[at] == "\\" else 1
        return min(at, len(self.text))

    def closing_paren(self, opening: int) -> int:
        """The `)` that matches the `(` at `opening`, or the end of the text."""
        text, at, depth = self.text, opening, 0
        while at < len(text):
            character = text[at]
            if character == "\\":
                at += 2
                continue
            if character == "'":
                close = text.find("'", at + 1)
                at = len(text) if close < 0 else close + 1
                continue
            if character == '"':
                at += 1
                while at < len(text) and text[at] != '"':
                    at += 2 if text[at] == "\\" else 1
                at += 1
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return at
            at += 1
        return len(text)

    # --- things that hold other commands -----------------------------------------

    def substitution(self, opening: int) -> None:
        """A `(...)`, whether `$(...)`, `<(...)` or a subshell. `opening` is where its `(` is."""
        close = self.closing_paren(opening)
        self.nest(self.text[opening + 1 : close])
        self.position = close + 1

    def nest(self, inner: str) -> None:
        self.command.nested.append(_Parser(inner, self.powershell, self.depth + 1).parse())

    # --- redirections ------------------------------------------------------------

    def redirect(self, character: str) -> None:
        text, at = self.text, self.position
        if self.word is not None and self.word.isdigit() and not self.quoted:
            self.word, self.quoted = None, False  # `2>`: the number is the stream.
        elif self.powershell and self.word == "*" and character == ">":
            self.word = None  # PowerShell's `*>`: every stream.
        else:
            self.end_word()
        if text.startswith(("<(", ">("), at):
            self.substitution(at + 1)
        elif text.startswith("<<<", at):
            self.pending = self.command.inputs
            self.position += 3
        elif text.startswith("<<", at):
            self.here_document(at)
        elif character == "<":
            self.pending = self.command.reads
            self.position += 2 if text.startswith(("<&", "<>"), at) else 1
        else:
            self.pending = self.command.writes
            self.position += 2 if text.startswith((">>", ">&", ">|"), at) else 1

    def here_document(self, at: int) -> None:
        text = self.text
        start = at + (3 if text.startswith("<<-", at) else 2)
        while start < len(text) and text[start] in " \t":
            start += 1
        end = start
        while end < len(text) and text[end] not in _WORD_END:
            end += 1
        delimiter = text[start:end].strip("'\"\\")
        self.position = end
        newline = text.find("\n", end)
        if not delimiter or newline < 0:
            return
        last_end = self.skips.get(newline)  # Where the last body on this line ended, if it has several.
        body_start = newline + 1 if last_end is None else last_end + 1
        closing = re.compile(rf"^\t*{re.escape(delimiter)}\r?$", re.MULTILINE).search(text, body_start)
        self.command.inputs.append(text[body_start : closing.start()] if closing else text[body_start:])
        self.skips[newline] = closing.end() if closing else len(text)
