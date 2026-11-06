"""Decide whether a tool call from an agent may touch protected data.

This runs as a Claude Code `PreToolUse` hook. It is a guardrail, not a sandbox:

- It stops the agent *looking at* the protected files with its own tools: Read, Grep,
  Glob, and shell commands that name them. Running a script that reads them is fine,
  and is the intended way to work: `datanyx run -- python report.py`.
- It reads shell syntax (see `shell.py`), but it can't see inside a script, follow a path
  built at run time ($DATA/x.csv), or see what a pipe feeds to another program.
- It lets `datanyx schema`, `synth`, `check`, `scrub`, `anonymize` and `run` through,
  because those are the ways to work with protected data that don't reveal it.
- It also refuses changes to the files that set the protection up, or to their folders.

Claude Code's own deny rules (written by `datanyx protect`) cover Read, Edit and Write
too; this hook adds Bash, Grep and Glob, and fails closed if it breaks.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from .config import ProtectConfig, find_config
from .shell import Command, expand_braces, parse

_GLOB_CHARACTERS = re.compile(r"[*?\[]")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
_KEY_VALUE = re.compile(r"[\w.-]+=(.+)")
_DRIVE_PATH = re.compile(r"/(?:cygdrive/|mnt/)?([A-Za-z])(/.*)?")
_RUN_TIME_PATHS = re.compile(r"\$(?:\{(PWD|HOME)\}|(PWD|HOME)\b)", re.IGNORECASE)
_PYTHON = re.compile(r"(?:python|pypy)[\d.]*|py")
_KEYWORDS = frozenset({"{", "}", "!", "if", "then", "elif", "else", "fi", "do", "done", "while", "until"})
_WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "command", "exec", "nice", "timeout", "xargs"})
_WRAPPER_VALUES = frozenset({"-u", "-g", "-n", "-C", "-p", "-I", "-L", "-P", "-s", "-k", "--user", "--group", "--adjustment"})
_READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path"}
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_SHELLS = frozenset({"Bash", "PowerShell"})
_RECURSIVE_SEARCH = frozenset({"rg", "ag", "ack"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_CHANGE_FOLDER = frozenset({"cd", "pushd", "chdir", "set-location", "sl"})
_POSIX_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "fish"})
_POWERSHELLS = frozenset({"powershell", "pwsh"})
# Options of launchers that take a value, so that the program they start can be told from it.
_LAUNCHER_VALUES = frozenset(
    {
        "--project", "--directory", "--with", "--with-editable", "--with-requirements", "--python", "-p", "--package",
        "--group", "--extra", "--env-file", "--index", "--from", "--spec",
    }
)
# DataNyx commands that work with protected data without showing it.
_SAFE_COMMANDS = frozenset(
    {"schema", "synth", "check", "scrub", "anonymize", "run", "scan", "diff", "audit", "config", "doctor", "hook", "mcp"}
)
# Options that loosen what the user decided: they show names the rules hid, leave terms visible,
# or point at a settings file the agent wrote. Each also works as a way to test guesses.
_WEAKENING = frozenset(
    {
        "--keep-name", "--keep", "--keep-entity", "--allow", "--keep-numbers", "--no-ner", "--show-paths",
        "--reveal", "--config", "--no-config", "--allow-urls", "--include-examples",
    }
)
_RUN_VALUES = frozenset(
    {
        "--names-from", "--allow", "--hide", "--model", "--pack", "--backend", "--detectors", "--stdout", "--timeout",
        "--save-stdout", "--save-stderr",
    }
)
_READ_ONLY_PROGRAMS = frozenset(
    {
        "cat", "head", "tail", "less", "more", "type", "grep", "egrep", "fgrep", "rg", "ls", "dir", "wc", "diff", "jq", "stat",
        "get-content", "gc", "select-string", "sls", "get-childitem", "gci",
    }
)
# Programs that can take a whole folder with them, so naming a folder that holds the setup is enough.
_REMOVERS = frozenset({"rm", "rmdir", "rd", "del", "erase", "unlink", "shred", "trash", "remove-item", "ri"})
_MOVERS = frozenset({"mv", "move", "ren", "rename", "move-item", "mi", "rename-item", "rni"})
# Git subcommands that rewrite files in the working folder when they are given paths.
_GIT_PATH_WRITERS = frozenset({"checkout", "restore", "reset", "rm", "mv", "clean"})
# Git subcommands that print file contents from the whole repository unless paths narrow them.
_GIT_CONTENT = frozenset({"diff", "show", "archive", "format-patch", "bundle", "cat-file"})
_GIT_PATCH_OPTIONAL = frozenset({"log", "whatchanged"})  # These print contents only if asked to.
_GIT_PATCH_FLAGS = frozenset({"-p", "-u", "--patch", "--patch-with-stat", "--patch-with-raw"})
_GIT_SUMMARIES = frozenset({"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--summary", "-s", "--no-patch"})
_GIT_GLOBAL_VALUES = frozenset({"-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
_MAX_WALKED = 5000

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
        return guard.shell(inputs.get("command") or "", guard.base, tool == "PowerShell")
    return None


def run_hook(stdin_text: str, environment: Mapping[str, str] | None = None, fallback: Path | None = None) -> str:
    """The hook's whole job: read the payload, return the JSON to print (empty to allow)."""
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
            return ""  # Not a protected project.
        reason = decide(payload, config)
    except Exception:
        return _deny("DataNyx's guard failed on this tool call, so it was blocked to be safe.")
    return _deny(reason) if reason else ""


def _deny(reason: str) -> str:
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    )


# --- paths ---------------------------------------------------------------------


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(path: Path | str, folder: Path | str) -> bool:
    inner, outer = _key(path), _key(folder)
    return inner == outer or inner.startswith(outer.rstrip(os.sep) + os.sep)


def _native(text: str) -> str:
    """Git Bash and WSL write C:\\Users as /c/Users or /mnt/c/Users. On Windows, that is what they mean."""
    if os.name == "nt":
        match = _DRIVE_PATH.fullmatch(text.replace("\\", "/"))
        if match:
            return f"{match[1]}:{match[2] or '/'}"
    return text


def _drive_form(path: Path) -> str:
    """A Windows path as Git Bash writes it, in lower case. Empty if it has no drive."""
    posix = path.as_posix()
    return f"/{posix[0]}{posix[2:]}".lower() if len(posix) > 1 and posix[1] == ":" else ""


def _glob_matches(pattern: list[str], path: list[str]) -> bool:
    """Whether a glob's `/`-separated parts match a path's parts, with `**` for any number of folders."""
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(_glob_matches(pattern[1:], path[skip:]) for skip in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatch(path[0].lower(), pattern[0].lower()) and _glob_matches(pattern[1:], path[1:])


class _Guard:
    def __init__(self, config: ProtectConfig, base: Path) -> None:
        self.config = config
        self.base = base
        self.tracked: dict[Path, bool] = {}

    def resolve(self, text: str, base: Path | None = None) -> Path:
        folder = base or self.base
        known = {"PWD": str(folder), "HOME": os.path.expanduser("~")}
        expanded = _RUN_TIME_PATHS.sub(lambda m: known[(m[1] or m[2]).upper()], text)
        path = Path(os.path.expanduser(_native(expanded)))
        return path if path.is_absolute() else folder / path

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
        if path and any(_inside(self.resolve(path), guarded) for guarded in self.config.guarded_files):
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
        hit = self.protected_hit(root)
        if hit is not None:
            return self.message(hit)
        for expansion in expand_braces(pattern):
            reason = self.embedded(expansion) or self.glob_reach(root, expansion)
            if reason:
                return reason
        return None

    def glob_reach(self, root: Path, pattern: str) -> str | None:
        """Refuse a pattern that names protected data, or would list what is inside it."""
        parts = pattern.replace("\\", "/").split("/")
        fixed = 0
        while fixed < len(parts) - 1 and not _GLOB_CHARACTERS.search(parts[fixed]):
            fixed += 1
        start = self.resolve("/".join(parts[:fixed]) or ".", root)
        hit = self.protected_hit(start) or (self.protected_hit(self.resolve(pattern, root)) if fixed == len(parts) - 1 else None)
        if hit is not None:
            return self.message(hit)
        if not self.config.block_broad_search:
            return None
        rest = parts[fixed:]
        for protected in self.config.paths:  # File names are often data too: jane_doe_medical.csv.
            if _inside(protected, start) and self.lists_something(start, protected, rest):
                return self.broad(protected)
        return None

    def lists_something(self, start: Path, protected: Path, pattern: list[str]) -> bool:
        """Whether `pattern`, from `start`, matches a file at or under `protected`."""
        files = [str(protected)] if os.path.isfile(protected) else []
        for folder, _folders, names in os.walk(protected):
            files.extend(os.path.join(folder, name) for name in names)
            if len(files) > _MAX_WALKED:
                return True  # Too many to check, so assume the worst.
        origin = os.path.realpath(start)
        return any(
            _glob_matches(pattern, os.path.relpath(os.path.realpath(name), origin).replace(os.sep, "/").split("/"))
            for name in files
        )

    # --- shell commands ----------------------------------------------------------

    def shell(self, command: str, current: Path, powershell: bool = False) -> str | None:
        return self.commands(parse(command, powershell), current, powershell)

    def commands(self, commands: list[Command], current: Path, powershell: bool) -> str | None:
        for command in commands:
            for scope in command.nested:  # A subshell's `cd` doesn't last, so each gets its own folder.
                reason = self.commands(scope, current, powershell)
                if reason:
                    return reason
            words = _strip_prefixes(command.words)
            if words and _prog(words[0]) in _CHANGE_FOLDER:
                target = next((word for word in words[1:] if not word.startswith("-")), None)
                if target:
                    current = self.resolve(target, current)
                    hit = self.protected_hit(current)
                    if hit is not None:
                        return self.message(hit)
                continue
            reason = self.simple(command, words, current, powershell)
            if reason:
                return reason
        return None

    def simple(self, command: Command, words: list[str], current: Path, powershell: bool) -> str | None:
        program = _prog(words[0]) if words else ""
        reason = self.tampering(command, words, program, current)
        if reason:
            return reason
        for target in command.writes:
            hit = self.protected_hit(self.resolve(target, current))
            if hit is not None:
                return f"{self.config.display(hit)} is protected by DataNyx: the real data shouldn't be changed from here."

        if program in _POWERSHELLS and _has_encoded_command(words):
            return "An encoded PowerShell command can't be checked, so it was blocked. Write the command out."
        script = _inline_script(words, program)
        if script is not None:
            reason = self.shell(script, current, program in _POWERSHELLS)
            if reason:
                return reason
        if program in _POSIX_SHELLS:  # `bash <<EOF` runs the text it is fed.
            for text in command.inputs:
                reason = self.shell(text, current)
                if reason:
                    return reason

        sub = None if command.nested else _datanyx_subcommand(words)
        if sub is not None:
            return self.datanyx(sub, current, powershell)

        reason = self.references([*words, *command.reads, *command.writes], current)
        if reason:
            return reason
        for text in command.inputs:
            reason = self.embedded(text)
            if reason:
                return reason
        if program == "git":
            return self.git_read(words[1:], current)
        return self.recursive_search(program, words, current)

    # --- changes to the setup ----------------------------------------------------

    def tampering(self, command: Command, words: list[str], program: str, current: Path) -> str | None:
        refusal = "That command changes a file that sets up DataNyx's protection. Ask the user to change it."
        for target in command.writes:
            if self.names_setup(target, current) or self.guard_files_mentioned(target):
                return refusal
        if program == "git":
            return refusal if self.git_tampers(words[1:], current) else None
        if not words or program in _READ_ONLY_PROGRAMS:
            return None
        text = " ".join([*words, *command.inputs, *command.reads])
        if self.guard_files_mentioned(text) or self.guard_folder_quoted(text):
            return refusal
        if any(self.names_setup(word, current) for word in words[1:]):
            return refusal
        return refusal if self.takes_setup_folder(words, current) else None

    def guard_files_mentioned(self, text: str) -> bool:
        flat = text.replace("\\", "/").lower()
        return any(self.relative(path) in flat for path in self.config.guarded_files)

    def guard_folder_quoted(self, text: str) -> bool:
        """shutil.rmtree('.datanyx'): code that names the folder holding the setup."""
        flat = text.replace("\\", "/").lower()
        return any(
            re.search(rf"""['"](?:\./)?{re.escape(self.relative(folder))}/?['"]""", flat)
            for folder in self.setup_folders
        )

    @property
    def setup_folders(self) -> set[Path]:
        return {path.parent for path in self.config.guarded_files if path.parent != self.config.root}

    def names_setup(self, word: str, current: Path) -> bool:
        return any(
            _inside(path, guarded)
            for candidate in _candidates(word)
            for path in self.paths_of(candidate, current)
            for guarded in self.config.guarded_files
        )

    def takes_setup_folder(self, words: list[str], current: Path) -> bool:
        """`rm -rf .datanyx`, `mv .claude x`, `find . -delete`: gone with a folder they sit in."""
        leading = {_prog(word) for word in words[:4]}
        arguments = words[1:]
        if not leading & _REMOVERS and "-delete" not in arguments:
            if not leading & _MOVERS:
                return False
            positional = [word for word in arguments if not word.startswith("-")]
            arguments = positional[:-1]  # The last is where it goes: `mv /tmp/x .` is fine.
        return any(self.holds_setup(word, current) for word in arguments)

    def holds_setup(self, word: str, current: Path) -> bool:
        return any(
            _inside(guarded, path)
            for candidate in _candidates(word)
            for path in self.paths_of(candidate, current)
            for guarded in self.config.guarded_files
        )

    def paths_of(self, candidate: str, current: Path) -> list[Path]:
        if not _GLOB_CHARACTERS.search(candidate):
            return [self.resolve(candidate, current)]
        return [self.resolve(match, current) for match in _glob(self.resolve(candidate, current))[:_MAX_WALKED]]

    def relative(self, path: Path) -> str:
        return self.config.display(path).replace("\\", "/").lower()

    # --- git ---------------------------------------------------------------------

    def git_tampers(self, arguments: list[str], current: Path) -> bool:
        sub, rest, current = _git_parts(arguments, current)
        if sub == "stash":
            return any(option in ("-u", "--include-untracked", "-a", "--all") for option in rest)
        if sub not in _GIT_PATH_WRITERS:
            return False
        paths = [word for word in rest if not word.startswith("-")]
        if sub == "clean" and not paths:
            return True  # It would delete an uncommitted setup.
        if sub == "reset" and "--hard" in rest:
            return True
        return any(self.names_setup(word, current) or self.holds_setup(word, current) for word in paths)

    def git_read(self, arguments: list[str], current: Path) -> str | None:
        sub, rest, current = _git_parts(arguments, current)
        for word in rest:  # HEAD:data/x.csv, :0:data/x.csv
            if ":" in word and not word.startswith("-") and word.index(":") != 1:
                inner = word.rsplit(":", 1)[1]
                for folder in (current, self.config.root):
                    hit = self.protected_hit(self.resolve(inner or ".", folder)) if inner else None
                    if hit is not None:
                        return self.message(hit)
        if sub == "grep":
            return self.git_grep(rest, current)
        if not self.git_prints_contents(sub, rest):
            return None
        paths = rest[rest.index("--") + 1 :] if "--" in rest else [
            word for word in rest if not word.startswith("-") and os.path.exists(self.resolve(word, current))
        ]
        roots = [self.resolve(path, current) for path in paths] or [self.config.root]
        for protected in self.config.paths:
            if any(_inside(protected, root) for root in roots) and self.is_tracked(protected):
                return self.broad(protected)
        return None

    def git_grep(self, rest: list[str], current: Path) -> str | None:
        if not self.config.block_broad_search:
            return None
        paths = rest[rest.index("--") + 1 :] if "--" in rest else None
        if paths is None:
            positional = [word for word in rest if not word.startswith("-")]
            candidates = positional if "-e" in rest else positional[1:]
            paths = [word for word in candidates if os.path.exists(self.resolve(word, current))]
        for root in [self.resolve(path, current) for path in paths] or [current]:
            covered = self.covers_protected(root)
            if covered is not None:
                return self.broad(covered)
        return None

    def git_prints_contents(self, sub: str, rest: list[str]) -> bool:
        if set(rest) & _GIT_SUMMARIES:
            return False
        if sub in _GIT_PATCH_OPTIONAL:
            return bool(set(rest) & _GIT_PATCH_FLAGS)
        return sub in _GIT_CONTENT

    def is_tracked(self, path: Path) -> bool:
        """Whether git holds this path now or in its history. Only then can git print it."""
        if path not in self.tracked:
            self.tracked[path] = _git_knows(self.config.root, path)
        return self.tracked[path]

    # --- datanyx -----------------------------------------------------------------

    def datanyx(self, sub: tuple[str, list[str]], current: Path, powershell: bool) -> str | None:
        command, rest = sub
        if not command:
            return None  # Just `datanyx`: it prints its help.
        if command not in _SAFE_COMMANDS:
            return f"`datanyx {command}` isn't available to the agent. Ask the user to run it."
        options = rest[: rest.index("--")] if "--" in rest else rest
        weakening = next((o for o in options if any(_abbreviates(o, name) for name in _WEAKENING)), None)
        if weakening is not None:
            return f"`{weakening.split('=')[0]}` loosens what the user set up, so the agent can't use it. Ask the user to run it."
        if command == "run":
            if any(_abbreviates(option, name) for option in options for name in ("--save-stdout", "--save-stderr")):
                return "`datanyx run --save-stdout/--save-stderr` writes the real output to a file, so the agent can't use it."
            program = _run_program(rest)
            return self.commands([Command(words=program)], current, powershell) if program else None
        if command == "anonymize" and any(_abbreviates(option, "--mapping") for option in options):
            return "`datanyx anonymize --mapping` saves the real values to a file, so the agent can't use it."
        return None

    # --- naming protected data ---------------------------------------------------

    def references(self, tokens: list[str], current: Path) -> str | None:
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

    def glob_hit(self, pattern: str, current: Path) -> str | None:
        for match in _glob(self.resolve(pattern, current))[:_MAX_WALKED]:
            hit = self.protected_hit(self.resolve(match, current))
            if hit is not None:
                return self.message(hit)
        return None

    def embedded(self, text: str) -> str | None:
        """A protected path written inside other text, such as python -c "open('data/x.csv')"."""
        flat = text.replace("\\", "/")
        lowered = flat.lower()
        for protected in self.config.all_protected:
            absolute = protected.as_posix()
            drive = _drive_form(protected)
            if absolute.lower() in lowered or _key(protected).replace(os.sep, "/") in lowered or (drive and drive in lowered):
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


def _glob(pattern: Path) -> list[str]:
    try:
        return glob.glob(str(pattern), recursive=True)
    except (OSError, ValueError):
        return []


def _prog(token: str) -> str:
    name = os.path.basename(token.replace("\\", "/")).lower()
    return name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name


def _strip_prefixes(words: list[str]) -> list[str]:
    """The command without what runs before it: keywords, `FOO=1`, and wrappers such as `sudo -u me`."""
    tokens = list(words)
    while tokens:
        if tokens[0] in _KEYWORDS or _ASSIGNMENT.match(tokens[0]):
            tokens.pop(0)
        elif _prog(tokens[0]) in _WRAPPERS:
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                option = tokens.pop(0)
                if option in _WRAPPER_VALUES and tokens:
                    tokens.pop(0)
        else:
            break
    return tokens


def _candidates(token: str) -> list[str]:
    """The paths a token may mean: itself, an option's value (--opt=x, -fx, if=x, @x), and without shell escapes."""
    found: list[str] = []
    if token.startswith("-"):
        if "=" in token:
            found.append(token.split("=", 1)[1])
        elif not token.startswith("--") and len(token) > 2 and ("/" in token or "\\" in token or "." in token):
            found.append(token[2:])  # grep -fdata/x.csv
    elif token:
        found.append(token)
        assignment = _KEY_VALUE.fullmatch(token)
        if assignment:
            found.append(assignment[1])  # dd if=data/x.csv
    for value in list(found):
        if value.startswith("@"):
            found.append(value[1:])  # curl -d @data/x.csv
        if "\\" in value:
            found.append(value.replace("\\", ""))  # da\ta/x.csv is data/x.csv to a POSIX shell.
    return [value for value in found if value]


def _abbreviates(option: str, name: str) -> bool:
    """Whether a command-line word is this long option, or a shortened form of it."""
    given = option.split("=")[0]
    return len(given) > 2 and given.startswith("--") and name.startswith(given)


def _inline_script(words: list[str], program: str) -> str | None:
    """The text of `bash -c TEXT`, `eval TEXT`, `pwsh -Command TEXT` or `cmd /c TEXT`."""
    if program == "eval":
        return " ".join(words[1:])
    for position, word in enumerate(words[1:], start=1):
        lowered = word.lower()
        if program in _POSIX_SHELLS:
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", word):
                return " ".join(words[position + 1 : position + 2])
            if not word.startswith(("-", "+")):
                return None
        elif program in _POWERSHELLS:
            if len(lowered) > 1 and "-command".startswith(lowered):
                return " ".join(words[position + 1 :])
        elif program == "cmd" and lowered in ("/c", "/k"):
            return " ".join(words[position + 1 :])
    return None


def _has_encoded_command(words: list[str]) -> bool:
    return any(len(word) > 1 and "-encodedcommand".startswith(word.lower()) and word.lower().startswith("-e") for word in words[1:])


def _skip_options(rest: list[str]) -> list[str]:
    """What follows a launcher's options, so `uv run --project tools datanyx` finds datanyx."""
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in _LAUNCHER_VALUES else 1
    return rest[index:]


def _datanyx_subcommand(tokens: list[str]) -> tuple[str, list[str]] | None:
    """(subcommand, the arguments after it) if the command runs datanyx, in any usual way.

    Only datanyx itself counts: `python script.py -m datanyx` and `uv run cat x datanyx` don't."""
    if not tokens:
        return None
    program, rest = _prog(tokens[0]), tokens[1:]
    if program == "datanyx":
        return _split_command(rest)
    if _PYTHON.fullmatch(program):
        return _python_module(rest)
    if program == "uv":
        rest = _skip_options(rest)
        return _datanyx_subcommand(_skip_options(rest[1:])) if rest[:1] == ["run"] else None
    if program == "uvx":
        return _datanyx_subcommand(_skip_options(rest))
    if program == "pipx":
        return _datanyx_subcommand(_skip_options(rest[1:])) if rest[:1] == ["run"] else None
    return None


def _python_module(rest: list[str]) -> tuple[str, list[str]] | None:
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        option = rest[index]
        if option == "-m":
            return _split_command(rest[index + 2 :]) if rest[index + 1 : index + 2] == ["datanyx"] else None
        if option == "-c":
            return None  # Everything after -c is the program's, whatever it looks like.
        index += 2 if option in ("-X", "-W") else 1
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


def _run_program(rest: list[str]) -> list[str]:
    """The command `datanyx run` will start, with or without the `--` before it."""
    if "--" in rest:
        return rest[rest.index("--") + 1 :]
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in _RUN_VALUES else 1
    return rest[index:]


def _git_parts(arguments: list[str], current: Path) -> tuple[str, list[str], Path]:
    """(subcommand, what follows it, the folder it runs in), past git's own options such as -C."""
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index]
        if option == "-C" and index + 1 < len(arguments):
            current = current / arguments[index + 1]
            index += 2
        elif option in _GIT_GLOBAL_VALUES:
            index += 2
        else:
            index += 1
    if index >= len(arguments):
        return "", [], current
    return arguments[index].lower(), arguments[index + 1 :], current


def _git_knows(root: Path, path: Path) -> bool:
    """Whether git tracks `path`, or did. A repository too slow to answer counts as yes, to be safe."""
    for arguments in (["ls-files", "--", str(path)], ["log", "-1", "--all", "--format=%h", "--", str(path)]):
        try:
            result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, timeout=5, check=False)
        except subprocess.TimeoutExpired:
            return True
        except OSError:
            return False
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False
