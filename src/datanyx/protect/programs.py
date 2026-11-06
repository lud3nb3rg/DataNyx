"""What particular programs do with their arguments: which only read, how git and DataNyx are called."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .shell import _prog, _split_command

_READ_ONLY_PROGRAMS = frozenset(
    {"cat", "head", "tail", "less", "more", "type", "grep", "egrep", "fgrep", "rg", "ls", "dir", "wc", "diff", "jq", "stat", "get-content", "gc", "get-childitem"}
)
_READ_ONLY_GIT = frozenset({"status", "diff", "log", "show", "blame", "ls-files", "ls-tree", "rev-parse", "describe", "grep", "shortlog", "cat-file"})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
# Git subcommands that print file contents from the whole repository unless paths narrow them.
_GIT_CONTENT = frozenset({"diff", "show", "archive", "format-patch", "bundle", "cat-file"})
_GIT_PATCH_OPTIONAL = frozenset({"log", "whatchanged"})  # These print contents only if asked to.
_GIT_PATCH_FLAGS = frozenset({"-p", "-u", "--patch", "--patch-with-stat", "--patch-with-raw"})
_GIT_SUMMARIES = frozenset({"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--summary", "-s", "--no-patch"})
_RUN_VALUES = frozenset({"--names-from", "--allow", "--hide", "--model", "--pack", "--backend", "--detectors", "--stdout", "--timeout", "--save-stdout", "--save-stderr"})
_PYTHONS = re.compile(r"(python|pypy)[\d.]*|py")
_UV_VALUE_OPTIONS = frozenset({"--project", "--directory", "--python", "-p", "--env-file", "--with", "--with-editable", "--with-requirements", "--package", "--group", "--extra", "--index", "--from", "--spec"})


def _run_program(rest: list[str]) -> list[str]:
    """The command `datanyx run` will start, with or without the `--` before it."""
    if "--" in rest:
        return rest[rest.index("--") + 1 :]
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in _RUN_VALUES else 1
    return rest[index:]


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


def _git_parts(arguments: list[str], current: Path) -> tuple[str, list[str], Path]:
    """(subcommand, what follows it, the folder it runs in), past git's own options such as -C."""
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index]
        if option == "-C" and index + 1 < len(arguments):
            current = current / arguments[index + 1]
            index += 2
        else:
            index += 2 if option in _GIT_VALUE_OPTIONS else 1
    if index >= len(arguments):
        return "", [], current
    return arguments[index].lower(), arguments[index + 1 :], current


def _git_prints_contents(sub: str, rest: list[str]) -> bool:
    if set(rest) & _GIT_SUMMARIES:
        return False
    if sub in _GIT_PATCH_OPTIONAL:
        return bool(set(rest) & _GIT_PATCH_FLAGS)
    return sub in _GIT_CONTENT


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
            position += 2 if tokens[position] in ("-X", "-W") else 1
        if tokens[position : position + 2] == ["-m", "datanyx"]:
            return _split_command(tokens[position + 2 :])
        return None
    if program == "uvx":
        position = 1
        while position < len(tokens) and tokens[position].startswith("-"):
            position += 2 if tokens[position] in _UV_VALUE_OPTIONS else 1
        return _datanyx_subcommand(tokens[position:]) if tokens[position : position + 1] == ["datanyx"] else None
    if program in ("uv", "pipx") and tokens[1:2] == ["run"]:
        position = 2
        while position < len(tokens) and tokens[position].startswith("-"):
            position += 2 if tokens[position] in _UV_VALUE_OPTIONS else 1
        return _datanyx_subcommand(tokens[position:])
    return None
