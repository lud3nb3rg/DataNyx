"""Decide whether a tool call from an agent may touch protected data.

This runs as a Claude Code `PreToolUse` hook. It is a guardrail, not a sandbox:

- It stops the agent *looking at* the protected files with its own tools: Read, Grep,
  Glob, and shell commands that name them. Running a script that reads them is fine,
  and is the intended way to work: `datanyx run -- python report.py`.
- It can't see inside a script, or follow a path built at run time ($DATA/x.csv).
- It lets `datanyx schema`, `synth`, `check`, `scrub`, `anonymize` and `run` through,
  because those are the ways to work with protected data that don't reveal it.
- It also refuses edits to the files that set the protection up.

Claude Code's own deny rules (written by `datanyx protect`) cover Read, Edit and Write
too; this hook adds Bash, Grep and Glob, and fails closed if it breaks.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path

from .config import ProtectConfig, find_config

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\r?\n)\s*")
_GLOB_CHARACTERS = re.compile(r"[*?\[]")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
_WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "command", "exec", "nice"})
_READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path"}
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_SHELLS = frozenset({"Bash", "PowerShell"})
_RECURSIVE_SEARCH = frozenset({"rg", "ag", "ack"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
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
_READ_ONLY_PROGRAMS = frozenset(
    {"cat", "head", "tail", "less", "more", "type", "grep", "egrep", "fgrep", "rg", "ls", "dir", "wc", "diff", "git", "jq", "stat"}
)

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


class _Guard:
    def __init__(self, config: ProtectConfig, base: Path) -> None:
        self.config = config
        self.base = base

    def resolve(self, text: str, base: Path | None = None) -> Path:
        path = Path(os.path.expanduser(text))
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
        hit = self.protected_hit(root) or self.protected_hit(self.resolve(pattern.split("*")[0] or "."))
        return self.message(hit) if hit is not None else self.embedded(pattern)

    # --- shell commands ----------------------------------------------------------

    def shell(self, command: str) -> str | None:
        current = self.base
        for segment in _SPLIT.split(command):
            tokens = _tokens(segment)
            if not tokens:
                continue
            if _prog(tokens[0]) == "cd" and len(tokens) > 1:
                current = self.resolve(tokens[1], current)
                hit = self.protected_hit(current)
                if hit is not None:
                    return self.message(hit)
                continue
            reason = self.segment(segment, tokens, current)
            if reason:
                return reason
        return None

    def segment(self, text: str, tokens: list[str], current: Path) -> str | None:
        program = _prog(tokens[0])
        guarded = self.guard_files_mentioned(text)
        if guarded and program not in _READ_ONLY_PROGRAMS:
            return "That command changes a file that sets up DataNyx's protection. Ask the user to change it."

        if "$(" not in text and "`" not in text:
            sub = _datanyx_subcommand(tokens)
            if sub is not None:
                return self.datanyx(sub, tokens, current)
        return self.references(tokens, current) or self.recursive_search(program, tokens, current)

    def guard_files_mentioned(self, text: str) -> bool:
        flat = text.replace("\\", "/").lower()
        return any(self.relative(path) in flat for path in self.config.guarded_files)

    def relative(self, path: Path) -> str:
        return self.config.display(path).replace("\\", "/").lower()

    def datanyx(self, sub: tuple[str, list[str]], tokens: list[str], current: Path) -> str | None:
        command, rest = sub
        if not command:
            return None  # Just `datanyx`: it prints its help.
        if command not in _SAFE_COMMANDS:
            return f"`datanyx {command}` isn't available to the agent. Ask the user to run it."
        options_part = rest[: rest.index("--")] if "--" in rest else rest
        weakening = next((o for o in options_part if o.split("=")[0] in _WEAKENING), None)
        if weakening is not None:
            return f"`{weakening.split('=')[0]}` loosens what the user set up, so the agent can't use it. Ask the user to run it."
        if command == "run":
            options = rest[: rest.index("--")] if "--" in rest else rest
            if any(option.split("=")[0] in ("--save-stdout", "--save-stderr") for option in options):
                return "`datanyx run --save-stdout/--save-stderr` writes the real output to a file, so the agent can't use it."
            if "--" in rest:
                return self.references(rest[rest.index("--") + 1 :], current)
        if command == "anonymize" and any(option.split("=")[0] == "--mapping" for option in rest):
            return "`datanyx anonymize --mapping` saves the real values to a file, so the agent can't use it."
        return None

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
        expanded = Path(os.path.expanduser(pattern))
        try:
            matches = glob.glob(str(expanded), recursive=True) if expanded.is_absolute() else glob.glob(
                pattern, root_dir=str(current), recursive=True
            )
        except (OSError, ValueError):
            return None
        for match in matches[:5000]:
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


def _tokens(segment: str) -> list[str]:
    try:
        raw = shlex.split(segment, posix=False)  # Keeps backslashes, so Windows paths survive.
    except ValueError:
        raw = segment.split()
    tokens = [token[1:-1] if len(token) > 1 and token[0] == token[-1] and token[0] in "'\"" else token for token in raw]
    while tokens and (_ASSIGNMENT.match(tokens[0]) or _prog(tokens[0]) in _WRAPPERS):
        tokens.pop(0)
    return tokens


def _candidates(token: str) -> list[str]:
    """The paths a token may mean: itself, the value of --option=value, and without < or >."""
    cleaned = token.lstrip("<>")
    options = [cleaned.split("=", 1)[1]] if cleaned.startswith("-") and "=" in cleaned else []
    return [] if cleaned.startswith("-") and not options else [cleaned, *options]


def _datanyx_subcommand(tokens: list[str]) -> tuple[str, list[str]] | None:
    """(subcommand, the arguments after it) if the command runs datanyx, in any usual way."""
    for position, token in enumerate(tokens[:8]):
        if _prog(token) == "datanyx" and (position == 0 or _prog(tokens[0]) in {"uv", "uvx", "pipx", "python", "python3", "py"}):
            return _split_command(tokens[position + 1 :])
        if token == "-m" and tokens[position + 1 : position + 2] == ["datanyx"]:
            return _split_command(tokens[position + 2 :])
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
