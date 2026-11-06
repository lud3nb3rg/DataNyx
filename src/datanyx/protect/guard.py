"""Decide whether a tool call from an agent may touch protected data.

This runs as a Claude Code `PreToolUse` hook. It is a guardrail, not a sandbox:

- It stops the agent *looking at* the protected files with its own tools: Read, Grep,
  Glob, and shell commands that name them. Running a script that reads them is fine,
  and is the intended way to work: `datanyx run -- python report.py`.
- It can't see inside a script, or follow a path built at run time from a variable the
  command sets itself (D=data; cat $D/x.csv).
- It lets `datanyx schema`, `synth`, `check`, `scrub`, `anonymize` and `run` through,
  because those are the ways to work with protected data that don't reveal it.
- It also refuses changes to the files that set the protection up.

Claude Code's own deny rules (written by `datanyx protect`) cover Read, Edit and Write
too; this hook adds Bash, Grep and Glob, and fails closed if it breaks.

Shell commands are parsed the way a shell reads them (quotes, escapes, redirections,
substitutions and nested shells) rather than matched as text, and paths are compared after
resolving them, so `da''ta/x.csv` and `./.claude/./settings.json` mean what they would mean.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path

from .config import CONFIG_DIR, ProtectConfig, find_config

_GLOB_CHARACTERS = re.compile(r"[*?\[]")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
# Words that come before the program. They run what follows, so they aren't the program themselves.
_WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "command", "exec", "nice", "{", "!", "then", "do", "else", "elif", "if", "while", "until"})
_READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path"}
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_SHELLS = frozenset({"Bash", "PowerShell"})
_SHELL_PROGRAMS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "powershell", "pwsh", "cmd"})
_SHELL_CODE_FLAGS = frozenset({"-c", "-lc", "-ic", "-command", "/c", "/k"})
_RECURSIVE_SEARCH = frozenset({"rg", "ag", "ack"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_CHANGE_DIRECTORY = frozenset({"cd", "pushd", "chdir", "set-location", "sl"})
# DataNyx commands that work with protected data without showing it.
_SAFE_COMMANDS = frozenset(
    {"schema", "synth", "check", "scrub", "anonymize", "run", "scan", "diff", "audit", "config", "doctor", "hook", "mcp"}
)
# Options that loosen what the user decided: they show names the rules hid, leave terms visible,
# choose weaker detectors, or point at a settings file the agent wrote. Many also work as a way
# to test guesses. `--hide` is here for that reason: whether a term comes back as <TERM_1> says
# whether it is in the data.
_WEAKENING = frozenset(
    {
        "--keep-name", "--keep-names", "--keep", "--keep-entity", "--allow", "--keep-numbers", "--no-ner", "--show-paths",
        "--reveal", "--config", "--no-config", "--allow-urls", "--allow-env", "--include-examples", "--allow-unscanned",
        "--hide", "--model", "--backend", "--detectors",
    }
)
_OUTPUT_OPTIONS = ("-o", "--output", "--audit-log")
_READ_ONLY_PROGRAMS = frozenset(
    {"cat", "head", "tail", "less", "more", "type", "grep", "egrep", "fgrep", "rg", "ls", "dir", "wc", "diff", "jq", "stat", "get-content", "gc", "get-childitem"}
)
_READ_ONLY_GIT = frozenset({"status", "diff", "log", "show", "blame", "ls-files", "ls-tree", "rev-parse", "describe", "grep", "shortlog", "cat-file"})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_DESTRUCTIVE_PROGRAMS = frozenset({"rm", "rmdir", "del", "erase", "rd", "ri", "mv", "move", "ren", "rename", "remove-item", "move-item", "rename-item", "rsync", "shred", "unlink", "trash"})
_SETTINGS_NAMES = frozenset({"datanyx.toml", "datanyx.local.toml"})
_PYTHONS = re.compile(r"(python|pypy)[\d.]*|py")
_UV_VALUE_OPTIONS = frozenset({"--project", "--directory", "--python", "-p", "--env-file"})
_PACKAGE = Path(__file__).resolve().parent.parent  # The code of this very hook.
_MAX_NESTING = 6
_MAX_GLOB_MATCHES = 20_000

DENY_TOOLS = "Read|Grep|Glob|Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|NotebookRead"


def decide(payload: dict, config: ProtectConfig) -> str | None:
    """Why the tool call must be refused, or None if it may go ahead."""
    inputs = payload.get("tool_input") or {}
    tool = payload.get("tool_name")
    base = Path(payload.get("cwd") or config.root)
    guard = _Guard(config, base)
    if tool in _READ_TOOLS:
        return guard.file(inputs.get(_READ_TOOLS[tool]), "read")
    if tool in _WRITE_TOOLS:
        path = inputs.get(_WRITE_TOOLS[tool])
        return guard.file(path, "change") or guard.tamper(path)
    if tool == "Grep":
        return guard.search(inputs.get("path"))
    if tool == "Glob":
        return guard.glob(inputs.get("path"), inputs.get("pattern") or "")
    if tool in _SHELLS:
        return guard.shell(inputs.get("command") or "")
    return None


def run_hook(
    stdin_text: str,
    environment: Mapping[str, str] | None = None,
    fallback: Path | None = None,
    require_config: bool = False,
) -> str:
    """The hook's whole job: read the payload, return the JSON to print (empty to allow).

    `datanyx protect` installs the hook with `require_config`: the hook being there means the
    project is protected, so a missing settings file means someone removed it, not that there
    is nothing to protect. Claude Code also keeps a session's hooks when their settings go.
    """
    environment = os.environ if environment is None else environment
    try:
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        return _deny("DataNyx couldn't read this tool call, so it was blocked to be safe.")
    try:
        start = Path(environment.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or fallback or Path.cwd())
        config = find_config(start.resolve())
        if config is None:
            if require_config:
                return _deny(
                    "DataNyx's protection settings (.datanyx/protect.json) are missing, so this was blocked to be safe. "
                    "Ask the user to run `datanyx protect` again, or to restart Claude Code if protection was removed on purpose."
                )
            return ""  # Not a protected project.
        reason = decide(payload, config)
    except Exception:
        return _deny("DataNyx's guard failed on this tool call, so it was blocked to be safe.")
    return _deny(reason) if reason else ""


def _deny(reason: str) -> str:
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    )


# --- reading a shell command -------------------------------------------------------


@dataclass
class _Segment:
    """One simple command: its words with the shell's quoting removed, and what it redirects."""

    text: str
    words: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)


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


def _parse_shell(command: str) -> tuple[list[_Segment], list[str]]:
    """The simple commands in `command`, and the code inside any `$(...)`, backticks or `<(...)`."""
    segments: list[_Segment] = []
    inner: list[str] = []
    current = _Segment("")
    buffer: list[str] = []
    in_word = False
    pending: str | None = None  # What the next word is: "read", "write" or "ignore" (a heredoc's tag).
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
            elif pending is None:
                current.words.append(word)
            pending = None
        buffer.clear()
        in_word = False

    def end_segment(at: int) -> None:
        nonlocal current, start, pending
        end_word()
        pending = None
        if current.words or current.reads or current.writes:
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
            position = _double_quoted(command, position, buffer, inner)
            in_word = True
        elif char == "\\":
            if command[position + 1 : position + 2] in ("\n", "\r"):
                position += 2  # A line continuation.
            else:
                buffer.append(command[position : position + 2])  # Kept: on Windows it separates a path.
                position += 2
                in_word = True
        elif char == "`":
            closing = command.find("`", position + 1)
            if closing == -1:
                buffer.append(char)
                position += 1
            else:
                inner.append(command[position + 1 : closing])
                buffer.append(command[position : closing + 1])
                position = closing + 1
            in_word = True
        elif command.startswith("$(", position):
            closing = _closing(command, position + 2)
            inner.append(command[position + 2 : closing])
            buffer.append(command[position : closing + 1])
            position = closing + 1
            in_word = True
        elif command.startswith(("<(", ">("), position):
            end_word()
            closing = _closing(command, position + 2)
            inner.append(command[position + 2 : closing])
            position = closing + 1
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
        elif char in "<>":
            if in_word and "".join(buffer).isdigit():  # The descriptor in 2>file or 0<file.
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
            pending = "ignore" if operator.startswith("<<") else "read" if operator == "<" else "write"
            position = stop
        else:
            buffer.append(char)
            in_word = True
            position += 1
    end_segment(length)
    return segments, inner


def _double_quoted(command: str, start: int, buffer: list[str], inner: list[str]) -> int:
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
    while tokens and (_ASSIGNMENT.match(tokens[0]) or _prog(tokens[0]) in _WRAPPERS):
        tokens.pop(0)
    return tokens


# --- paths ---------------------------------------------------------------------


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(path: Path | str, folder: Path | str) -> bool:
    inner, outer = _key(path), _key(folder)
    return inner == outer or inner.startswith(outer.rstrip(os.sep) + os.sep)


class _Guard:
    def __init__(self, config: ProtectConfig, base: Path) -> None:
        self.config = config
        self.base = base

    def resolve(self, text: str, base: Path | None = None) -> Path:
        path = Path(os.path.expanduser(os.path.expandvars(text)))
        return path if path.is_absolute() else (base or self.base) / path

    def protected_hit(self, path: Path) -> Path | None:
        return next((p for p in self.config.all_protected if _inside(path, p)), None)

    def covers_protected(self, path: Path) -> Path | None:
        """A protected path that lies inside `path`, so searching `path` would reach it."""
        return next((p for p in self.config.all_protected if _inside(p, path)), None)

    def message(self, target: Path) -> str:
        shown = self.config.display(target)
        if target.name == "datanyx.local.toml":
            return "datanyx.local.toml lists the terms DataNyx hides, so it can't be read from here. Ask the user."
        return (
            f"{shown} is protected by DataNyx, so it can't be read directly. To work with it without seeing it: "
            f"`datanyx schema {shown}` for its structure, `datanyx synth {shown} --rows 20` for fake rows, "
            f"`datanyx check CODE --against {shown}` to check code, and `datanyx run -- COMMAND` to run a script "
            "against the real data and see only a scrubbed error."
        )

    # --- what sets the protection up ------------------------------------------------

    def is_setup_file(self, path: Path) -> bool:
        """A file that sets the protection up. A settings file counts wherever it is, since
        a command reads the nearest one, so a new one in a sub-folder would replace the real one."""
        if path.name.lower() in _SETTINGS_NAMES or any(_key(path) == _key(guarded) for guarded in self.config.guarded_files):
            return True
        return _inside(path, _PACKAGE) or (_prog(sys.argv[0]) == "datanyx" and _key(path) == _key(sys.argv[0]))

    def is_setup_folder(self, path: Path) -> bool:
        key = _key(path)
        return any(key == _key(folder) for folder in (self.config.root / ".claude", self.config.root / CONFIG_DIR, Path.home() / ".claude"))

    def holds_setup(self, path: Path) -> bool:
        return any(_inside(guarded, path) for guarded in self.config.guarded_files)

    def touches_setup(self, token: str, current: Path, destructive: bool) -> bool:
        for candidate in _candidates(token):
            paths = self.expand(candidate, current)
            for path in paths:
                if self.is_setup_file(path) or self.is_setup_folder(path) or _inside(path, self.config.root / CONFIG_DIR):
                    return True
                if destructive and self.holds_setup(path):
                    return True
        return False

    def expand(self, candidate: str, current: Path) -> list[Path]:
        if _GLOB_CHARACTERS.search(candidate):
            return [self.resolve(match, current) for match in self.glob_matches(candidate, current)]
        return [self.resolve(candidate, current)]

    # --- tools -------------------------------------------------------------------

    def file(self, path: str | None, verb: str) -> str | None:
        if not path:
            return None
        hit = self.protected_hit(self.resolve(path))
        if hit is None:
            return None
        if verb == "change":
            return f"{self.config.display(hit)} is protected by DataNyx: the real data shouldn't be changed from here."
        return self.message(hit)

    def tamper(self, path: str | None) -> str | None:
        if path and self.is_setup_file(self.resolve(path)):
            return "That file sets up DataNyx's protection. Ask the user to change it."
        return None

    def search(self, path: str | None) -> str | None:
        root = self.resolve(path) if path else self.base
        hit = self.protected_hit(root)
        if hit is not None:
            return self.message(hit)
        covered = self.covers_protected(root)
        if covered is not None and self.config.block_broad_search:
            return self.broad(covered)
        return None

    def broad(self, covered: Path) -> str:
        return (
            f"This search would also read {self.config.display(covered)}, which DataNyx protects. "
            "Search a narrower folder that leaves it out."
        )

    def glob(self, path: str | None, pattern: str) -> str | None:
        root = self.resolve(path) if path else self.base
        hit = self.protected_hit(root) or self.protected_hit(self.resolve(pattern.split("*")[0] or "."))
        if hit is not None:
            return self.message(hit)
        return self.embedded(pattern) or self.glob_hit(pattern, root)

    # --- shell commands ----------------------------------------------------------

    def shell(self, command: str, base: Path | None = None, depth: int = 0) -> str | None:
        if depth > _MAX_NESTING:
            return "That command is nested too deeply for DataNyx to check, so it was blocked to be safe."
        if "DATANYX_CONFIG" in command.upper():
            return "`DATANYX_CONFIG` points DataNyx at another settings file, so the agent can't set it. Ask the user."
        current = base or self.base
        segments, inner = _parse_shell(command)
        for segment in segments:
            tokens = _without_wrappers(segment.words)
            reason = self.redirections(segment, current)
            if reason:
                return reason
            if tokens and _prog(tokens[0]) in _CHANGE_DIRECTORY and len(tokens) > 1:
                current = self.resolve(_plain(tokens[1]), current)
                hit = self.protected_hit(current)
                if hit is not None:
                    return self.message(hit)
                continue
            if not tokens:
                continue
            reason = self.nested(tokens, current, depth) or self.segment(segment, tokens, current)
            if reason:
                return reason
        for code in inner:
            reason = self.shell(code, current, depth + 1)
            if reason:
                return reason
        return None

    def nested(self, tokens: list[str], current: Path, depth: int) -> str | None:
        """Code handed to another shell, or to eval, is checked as a command of its own."""
        program = _prog(tokens[0])
        if program == "eval":
            return self.shell(" ".join(tokens[1:]), current, depth + 1)
        if program in _SHELL_PROGRAMS:
            for position, token in enumerate(tokens[1:], start=1):
                if token.lower() in _SHELL_CODE_FLAGS:
                    return self.shell(" ".join(tokens[position + 1 :]), current, depth + 1)
        return None

    def redirections(self, segment: _Segment, current: Path) -> str | None:
        for target in segment.writes:
            if self.touches_setup(target, current, destructive=True):
                return "That command changes a file that sets up DataNyx's protection. Ask the user to change it."
            hit = self.protected_hit(self.resolve(_plain(target), current))
            if hit is not None:
                return f"{self.config.display(hit)} is protected by DataNyx: the real data shouldn't be changed from here."
        return self.references(segment.reads, current)

    def segment(self, segment: _Segment, tokens: list[str], current: Path) -> str | None:
        program = _prog(tokens[0])
        if program == "git" and _is_git_cleanup(tokens):
            return "`git clean` and `git stash --include-untracked` can delete DataNyx's protection files. Ask the user to run it."
        if not _is_read_only(tokens):
            destructive = program in _DESTRUCTIVE_PROGRAMS
            mentioned = self.guard_files_mentioned(segment.text) or any(self.touches_setup(token, current, destructive) for token in tokens[1:])
            if mentioned:
                return "That command changes a file that sets up DataNyx's protection. Ask the user to change it."

        if "$(" not in segment.text and "`" not in segment.text:
            sub = _datanyx_subcommand(tokens)
            if sub is not None:
                return self.datanyx(sub, current)
        return self.references(tokens, current) or self.recursive_search(program, tokens, current)

    def guard_files_mentioned(self, text: str) -> bool:
        flat = text.replace("\\", "/").lower()
        return any(self.relative(path) in flat for path in self.config.guarded_files)

    def relative(self, path: Path) -> str:
        return self.config.display(path).replace("\\", "/").lower()

    def datanyx(self, sub: tuple[str, list[str]], current: Path) -> str | None:
        command, rest = sub
        if not command:
            return None  # Just `datanyx`: it prints its help.
        if command not in _SAFE_COMMANDS:
            return f"`datanyx {command}` isn't available to the agent. Ask the user to run it."
        options_part = rest[: rest.index("--")] if "--" in rest else rest
        weakening = next((o for o in options_part if o.split("=")[0] in _WEAKENING), None)
        if weakening is not None:
            return f"`{weakening.split('=')[0]}` loosens what the user set up, so the agent can't use it. Ask the user to run it."
        for value in _option_values(options_part, ("--names-from",)):
            # Names from the protected data are the user's. Names from a file the agent wrote are guesses being tested.
            if self.protected_hit(self.resolve(_plain(value), current)) is None and not value.startswith("env:"):
                return "`--names-from` can only name the protected data here: a file of guessed names would be a way to test them."
        for value in _option_values(options_part, _OUTPUT_OPTIONS):
            hit = self.protected_hit(self.resolve(_plain(value), current))
            if hit is not None:
                return f"{self.config.display(hit)} is protected by DataNyx: the real data shouldn't be changed from here."
        if command == "run":
            if any(option.split("=")[0] in ("--save-stdout", "--save-stderr") for option in options_part):
                return "`datanyx run --save-stdout/--save-stderr` writes the real output to a file, so the agent can't use it."
            # Everything that isn't the value of --names-from: `run` also takes a command with no `--` before it.
            return self.references(_without_option_values(rest, ("--names-from",)), current)
        if command == "anonymize" and any(option.split("=")[0] == "--mapping" for option in rest):
            return "`datanyx anonymize --mapping` saves the real values to a file, so the agent can't use it."
        return None

    def references(self, tokens: Iterable[str], current: Path) -> str | None:
        """A token that names a protected path, directly, by glob, or inside quoted code."""
        for token in tokens:
            for candidate in _candidates(token):
                if _GLOB_CHARACTERS.search(candidate):
                    reason = self.glob_hit(candidate, current)
                else:
                    hit = self.protected_hit(self.resolve(candidate, current))
                    reason = self.message(hit) if hit is not None else None
                if reason:
                    return reason
            reason = self.embedded(token)
            if reason:
                return reason
        return None

    def glob_matches(self, pattern: str, current: Path) -> list[str]:
        expanded = Path(os.path.expanduser(pattern))
        try:
            found = (
                glob.iglob(str(expanded), recursive=True)
                if expanded.is_absolute()
                else glob.iglob(pattern, root_dir=str(current), recursive=True)
            )
            return list(islice(found, _MAX_GLOB_MATCHES))
        except (OSError, ValueError):
            return []

    def glob_hit(self, pattern: str, current: Path) -> str | None:
        for match in self.glob_matches(pattern, current):
            hit = self.protected_hit(self.resolve(match, current))
            if hit is not None:
                return self.message(hit)
        return None

    def embedded(self, text: str) -> str | None:
        """A protected path written inside other text, such as python -c "open('data/x.csv')"."""
        flat = text.replace("\\", "/")
        for protected in self.config.all_protected:
            absolute = protected.as_posix()
            if absolute.lower() in flat.lower() or _key(protected).replace(os.sep, "/") in flat.lower():
                return self.message(protected)
            relative = re.escape(self.config.display(protected).replace("\\", "/"))
            if re.search(rf"""['"](?:\./)?{relative}(?:/|['"])""", flat, re.IGNORECASE):
                return self.message(protected)
        return None

    def recursive_search(self, program: str, tokens: list[str], current: Path) -> str | None:
        """grep -r, rg and find -exec over a folder that has protected data somewhere inside."""
        if not self.config.block_broad_search:
            return None
        arguments = tokens[1:]
        if program in _GREPS:
            if not any(re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", a) or a == "--recursive" for a in arguments):
                return None
        elif program == "find":
            if not any(a in ("-exec", "-execdir", "-ok", "-okdir") for a in arguments):
                return None
        elif program not in _RECURSIVE_SEARCH:
            return None
        positional = [a for a in arguments if not a.startswith("-")]
        paths = positional if program == "find" else positional[1:]  # The first is the pattern.
        roots = [self.resolve(p, current) for p in paths if os.path.exists(self.resolve(p, current))] or [current]
        for root in roots:
            covered = self.covers_protected(root)
            if covered is not None:
                return self.broad(covered)
        return None


def _prog(token: str) -> str:
    name = os.path.basename(token.replace("\\", "/")).lower()
    return name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name


def _plain(token: str) -> str:
    """A token without shell escapes, unless they are what separates a Windows path."""
    return token if os.name == "nt" else token.replace("\\", "")


def _candidates(token: str) -> list[str]:
    """The paths a token may mean: itself, without shell escapes, and the value of --option=value."""
    if token.startswith("-"):
        if "=" not in token:
            return []
        token = token.split("=", 1)[1]
    unescaped = token.replace("\\", "")
    return [token, unescaped] if unescaped != token else [token]


def _is_read_only(tokens: list[str]) -> bool:
    program = _prog(tokens[0])
    if program == "git":
        return _git_subcommand(tokens) in _READ_ONLY_GIT
    return program in _READ_ONLY_PROGRAMS


def _git_subcommand(tokens: list[str]) -> str | None:
    position = 1
    while position < len(tokens) and tokens[position].startswith("-"):
        position += 2 if tokens[position] in _GIT_VALUE_OPTIONS else 1
    return tokens[position] if position < len(tokens) else None


def _is_git_cleanup(tokens: list[str]) -> bool:
    subcommand = _git_subcommand(tokens)
    if subcommand == "clean":
        return True
    return subcommand == "stash" and any(t in ("-u", "-a", "--include-untracked", "--all") for t in tokens)


def _option_values(options: list[str], names: Iterable[str]) -> Iterable[str]:
    """The values given to these options, as `--name value`, `--name=value` or `-ovalue`."""
    names = tuple(names)
    for position, option in enumerate(options):
        if option in names:
            if position + 1 < len(options):
                yield options[position + 1]
        elif "=" in option and option.split("=", 1)[0] in names:
            yield option.split("=", 1)[1]
        elif "-o" in names and option.startswith("-o") and not option.startswith("--") and len(option) > 2:
            yield option[2:]


def _without_option_values(options: list[str], names: tuple[str, ...]) -> list[str]:
    kept: list[str] = []
    skip = False
    for option in options:
        if skip:
            skip = False
        elif option in names:
            skip = True
        elif option.split("=", 1)[0] not in names:
            kept.append(option)
    return kept


def _datanyx_subcommand(tokens: list[str]) -> tuple[str, list[str]] | None:
    """(subcommand, the arguments after it) if this command runs datanyx, and nothing else.

    The word has to be the thing being run: `ls -m datanyx data` and `python -c "..." datanyx`
    merely mention it, and get none of the allowances datanyx does.
    """
    if not tokens:
        return None
    program = _prog(tokens[0])
    if program == "datanyx":
        return _split_command(tokens[1:])
    if _PYTHONS.fullmatch(program):
        position = 1
        while position < len(tokens) and tokens[position].startswith("-") and tokens[position] not in ("-m", "-c"):
            position += 1
        if tokens[position : position + 2] == ["-m", "datanyx"]:
            return _split_command(tokens[position + 2 :])
        return None
    if program == "uvx":
        return _datanyx_subcommand(tokens[1:]) if tokens[1:2] == ["datanyx"] else None
    if program in ("uv", "pipx") and tokens[1:2] == ["run"]:
        position = 2
        while position < len(tokens) and tokens[position].startswith("-"):
            position += 2 if tokens[position] in _UV_VALUE_OPTIONS else 1
        return _datanyx_subcommand(tokens[position:])
    return None


def _split_command(rest: list[str]) -> tuple[str, list[str]]:
    """(command, every other argument) for `datanyx [--config FILE] [--no-config] COMMAND ...`.

    The options before the command are returned with the rest, so they are checked too."""
    index = 0
    while index < len(rest) and rest[index].startswith("--"):
        index += 2 if rest[index] == "--config" else 1
    if index >= len(rest):
        return "", rest
    return rest[index], [*rest[:index], *rest[index + 1 :]]
