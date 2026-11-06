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
from itertools import islice
from pathlib import Path

from .config import CONFIG_DIR, ProtectConfig, find_config
from .paths import _drive_form, _inside, _key, _native
from .programs import (
    _datanyx_subcommand,
    _git_knows,
    _git_parts,
    _git_prints_contents,
    _is_read_only,
    _option_values,
    _run_program,
    _without_option_values,
)
from .shell import (
    _abbreviates,
    _candidates,
    _expand_braces,
    _has_encoded_command,
    _parse_shell,
    _plain,
    _prog,
    _Segment,
    _without_wrappers,
)

_GLOB_CHARACTERS = re.compile(r"[*?\[]")
_RUN_TIME_PWD = re.compile(r"\$(?:\{PWD\}|PWD\b)", re.IGNORECASE)
_READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path"}
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_SHELLS = frozenset({"Bash", "PowerShell"})
_SHELL_PROGRAMS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "powershell", "pwsh", "cmd"})
_SHELL_CODE_FLAGS = frozenset({"-c", "-lc", "-ic", "-command", "/c", "/k"})
_POSIX_SHELL_CODE_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
_POWERSHELLS = frozenset({"powershell", "pwsh"})
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
_DESTRUCTIVE_PROGRAMS = frozenset({"rm", "rmdir", "del", "erase", "rd", "ri", "mv", "move", "ren", "rename", "remove-item", "move-item", "rename-item", "rsync", "shred", "unlink", "trash"})
_MOVERS = frozenset({"mv", "move", "ren", "rename", "move-item", "rename-item"})  # The last path is where it goes: `mv /tmp/x .` is fine.
# Git subcommands that rewrite files in the working folder when they are given paths.
_GIT_PATH_WRITERS = frozenset({"checkout", "restore", "reset", "rm", "mv", "clean"})
_SETTINGS_NAMES = frozenset({"datanyx.toml", "datanyx.local.toml"})
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
        return guard.shell(inputs.get("command") or "", powershell=tool == "PowerShell")
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


class _Guard:
    def __init__(self, config: ProtectConfig, base: Path) -> None:
        self.config = config
        self.base = base

    def resolve(self, text: str, base: Path | None = None) -> Path:
        folder = base or self.base
        known = _RUN_TIME_PWD.sub(lambda _: str(folder), text)
        path = Path(os.path.expanduser(_native(os.path.expandvars(known))))
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
        for expansion in _expand_braces(pattern):
            hit = self.protected_hit(root) or self.protected_hit(self.resolve(expansion.split("*")[0] or ".", root))
            if hit is not None:
                return self.message(hit)
            reason = self.embedded(expansion) or self.glob_hit(expansion, root)
            if reason:
                return reason
        return None

    # --- shell commands ----------------------------------------------------------

    def shell(self, command: str, base: Path | None = None, depth: int = 0, powershell: bool = False) -> str | None:
        if depth > _MAX_NESTING:
            return "That command is nested too deeply for DataNyx to check, so it was blocked to be safe."
        if "DATANYX_CONFIG" in command.upper():
            return "`DATANYX_CONFIG` points DataNyx at another settings file, so the agent can't set it. Ask the user."
        current = base or self.base
        for segment in _parse_shell(command, powershell):
            for code in segment.inner:  # Each from where it runs: a later `cd` doesn't move it.
                reason = self.shell(code, current, depth + 1, powershell)
                if reason:
                    return reason
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
            reason = self.nested(tokens, segment, current, depth) or self.segment(segment, tokens, current, powershell, depth)
            if reason:
                return reason
        return None

    def nested(self, tokens: list[str], segment: _Segment, current: Path, depth: int) -> str | None:
        """Code handed to another shell, or to eval, is checked as a command of its own."""
        program = _prog(tokens[0])
        if program == "eval":
            return self.shell(" ".join(tokens[1:]), current, depth + 1)
        if program in _POWERSHELLS and _has_encoded_command(tokens):
            return "An encoded PowerShell command can't be checked, so it was blocked. Write the command out."
        if program in _SHELL_PROGRAMS:
            for position, token in enumerate(tokens[1:], start=1):
                if token.lower() in _SHELL_CODE_FLAGS or (program not in _POWERSHELLS and program != "cmd" and _POSIX_SHELL_CODE_FLAG.fullmatch(token)):
                    return self.shell(" ".join(tokens[position + 1 :]), current, depth + 1, program in _POWERSHELLS)
            for text in segment.inputs:  # `bash <<EOF` runs the text it is fed.
                reason = self.shell(text, current, depth + 1)
                if reason:
                    return reason
        return None

    def redirections(self, segment: _Segment, current: Path) -> str | None:
        for target in segment.writes:
            if self.touches_setup(target, current, destructive=True):
                return "That command changes a file that sets up DataNyx's protection. Ask the user to change it."
            hit = self.protected_hit(self.resolve(_plain(target), current))
            if hit is not None:
                return f"{self.config.display(hit)} is protected by DataNyx: the real data shouldn't be changed from here."
        return self.references(segment.reads, current)

    def segment(self, segment: _Segment, tokens: list[str], current: Path, powershell: bool = False, depth: int = 0) -> str | None:
        program = _prog(tokens[0])
        if program == "git" and self.git_tampers(tokens, current):
            return "That git command can delete or revert the files that set up DataNyx's protection. Ask the user to run it."
        if not _is_read_only(tokens):
            destructive = program in _DESTRUCTIVE_PROGRAMS or (program == "find" and "-delete" in tokens)
            if self.mentions_setup(segment, tokens, program) or self.setup_in_tokens(tokens, program, current, destructive):
                return "That command changes a file that sets up DataNyx's protection. Ask the user to change it."

        if not segment.inner:
            sub = _datanyx_subcommand(tokens)
            if sub is not None:
                return self.datanyx(sub, current, powershell, depth)
        reason = self.references(tokens, current)
        if reason:
            return reason
        for text in segment.inputs:
            reason = self.embedded(text)
            if reason:
                return reason
        if program == "git":
            return self.git_read(tokens, current)
        return self.recursive_search(program, tokens, current)

    def mentions_setup(self, segment: _Segment, tokens: list[str], program: str) -> bool:
        """A setup file named anywhere in the command, or its folder in quoted code. A commit message only talks about it."""
        text = " ".join(_without_option_values(tokens, ("-m", "--message"))) if program == "git" else segment.text
        text = " ".join([text, *segment.inputs])
        return self.guard_files_mentioned(text) or self.setup_folder_quoted(text)

    def setup_in_tokens(self, tokens: list[str], program: str, current: Path, destructive: bool) -> bool:
        last = None
        if program in _MOVERS:
            positional = [position for position, token in enumerate(tokens) if position and not token.startswith("-")]
            last = positional[-1] if positional else None
        return any(
            self.touches_setup(token, current, destructive and position != last)
            for position, token in enumerate(tokens)
            if position
        )

    def setup_folder_quoted(self, text: str) -> bool:
        """shutil.rmtree('.datanyx'): code that names a folder holding the setup."""
        flat = text.replace("\\", "/").lower()
        folders = {path.parent for path in self.config.guarded_files if path.parent != self.config.root}
        return any(re.search(rf"""['"](?:\./)?{re.escape(self.relative(folder))}/?['"]""", flat) for folder in folders)

    # --- git ---------------------------------------------------------------------

    def git_tampers(self, tokens: list[str], current: Path) -> bool:
        sub, rest, current = _git_parts(tokens[1:], current)
        if sub == "stash":
            return any(option in ("-u", "--include-untracked", "-a", "--all") for option in rest)
        if sub not in _GIT_PATH_WRITERS:
            return False
        paths = [word for word in rest if not word.startswith("-")]
        if sub == "clean" and not paths:
            return True  # It would delete an uncommitted setup.
        if sub == "reset" and "--hard" in rest:
            return True
        return any(self.touches_setup(word, current, destructive=True) for word in paths)

    def git_read(self, tokens: list[str], current: Path) -> str | None:
        sub, rest, current = _git_parts(tokens[1:], current)
        for word in rest:  # HEAD:data/x.csv, :0:data/x.csv
            if ":" in word and not word.startswith("-") and word.index(":") != 1:
                inner = word.rsplit(":", 1)[1]
                for folder in (current, self.config.root):
                    hit = self.protected_hit(self.resolve(inner, folder)) if inner else None
                    if hit is not None:
                        return self.message(hit)
        if sub == "grep":
            return self.git_grep(rest, current)
        if not _git_prints_contents(sub, rest):
            return None
        narrowed = rest[rest.index("--") + 1 :] if "--" in rest else [
            word for word in rest if not word.startswith("-") and os.path.exists(self.resolve(word, current))
        ]
        roots = [self.resolve(word, current) for word in narrowed] or [self.config.root]
        for protected in self.config.paths:
            if any(_inside(protected, root) for root in roots) and _git_knows(self.config.root, protected):
                return self.broad(protected)
        return None

    def git_grep(self, rest: list[str], current: Path) -> str | None:
        if not self.config.block_broad_search:
            return None
        narrowed = rest[rest.index("--") + 1 :] if "--" in rest else None
        if narrowed is None:
            positional = [word for word in rest if not word.startswith("-")]
            candidates = positional if "-e" in rest else positional[1:]
            narrowed = [word for word in candidates if os.path.exists(self.resolve(word, current))]
        for root in [self.resolve(word, current) for word in narrowed] or [current]:
            covered = self.covers_protected(root)
            if covered is not None:
                return self.broad(covered)
        return None

    # --- datanyx -----------------------------------------------------------------

    def guard_files_mentioned(self, text: str) -> bool:
        flat = text.replace("\\", "/").lower()
        return any(self.relative(path) in flat for path in self.config.guarded_files)

    def relative(self, path: Path) -> str:
        return self.config.display(path).replace("\\", "/").lower()

    def datanyx(self, sub: tuple[str, list[str]], current: Path, powershell: bool = False, depth: int = 0) -> str | None:
        command, rest = sub
        if not command:
            return None  # Just `datanyx`: it prints its help.
        if command not in _SAFE_COMMANDS:
            return f"`datanyx {command}` isn't available to the agent. Ask the user to run it."
        options_part = rest[: rest.index("--")] if "--" in rest else rest
        weakening = next((o for o in options_part if any(_abbreviates(o, name) for name in _WEAKENING)), None)
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
            if any(_abbreviates(option, name) for option in options_part for name in ("--save-stdout", "--save-stderr")):
                return "`datanyx run --save-stdout/--save-stderr` writes the real output to a file, so the agent can't use it."
            # Everything that isn't the value of --names-from: `run` also takes a command with no `--` before it.
            reason = self.references(_without_option_values(rest, ("--names-from",)), current)
            if reason:
                return reason
            program = _without_wrappers(_run_program(rest))
            if not program:
                return None
            started = _Segment(" ".join(program), words=program)
            return self.nested(program, started, current, depth) or self.segment(started, program, current, powershell, depth)
        if command == "anonymize" and any(_abbreviates(option, "--mapping") for option in rest):
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
            drive = _drive_form(protected)
            if absolute.lower() in flat.lower() or _key(protected).replace(os.sep, "/") in flat.lower() or (drive and drive in flat.lower()):
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
