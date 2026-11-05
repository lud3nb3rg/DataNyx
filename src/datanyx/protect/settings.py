"""Write the files that turn protection on, and take them out again.

For a project this touches, and only with `--write`:

- `.claude/settings.json` (or `settings.local.json`): deny rules for Read and Edit on the
  protected paths, and the guard hook. Anything else already there is kept.
- `.mcp.json`: a `datanyx` server limited to the protected paths.
- `.datanyx/protect.json`: the list of protected paths, read by the hook.

Everything added is marked so that running it again replaces it, and `--remove` finds it.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..sources.base import SourceError
from .config import CONFIG_DIR, config_path, load_config, stored_form
from .guard import DENY_TOOLS

HOOK_LABEL = "DataNyx guard"
MCP_NAME = "datanyx"
_RULE_TOOLS = ("Read", "Edit")  # Edit is the rule name for every tool that writes a file.


@dataclass
class Change:
    """One file's before and after. `after` is None when the file is to be deleted."""

    path: Path
    before: str
    after: str | None

    @property
    def changed(self) -> bool:
        return self.before != (self.after or "")

    def diff(self) -> str:
        new = self.after or ""
        lines = difflib.unified_diff(
            self.before.splitlines(), new.splitlines(), f"{self.path.name} (before)", f"{self.path.name} (after)", lineterm=""
        )
        return chr(10).join(lines)


def split_command(text: str) -> list[str]:
    """`uv run datanyx` as a list. Quotes around a path with spaces are honoured on every system."""
    parts = shlex.split(text, posix=os.name != "nt")
    command = [part[1:-1] if len(part) > 1 and part[0] == part[-1] and part[0] in "'\"" else part for part in parts]
    if not command:
        raise SourceError("the command to run datanyx is empty")
    return command


def plan_protect(
    project: Path,
    targets: list[Path],
    command: list[str],
    local: bool = False,
    block_broad_search: bool = True,
    with_mcp: bool = True,
) -> list[Change]:
    stored = [stored_form(project, target) for target in targets]
    settings_path = _settings_path(project, local)
    other_path = _settings_path(project, not local)

    changes = [
        _edit_json(settings_path, lambda data: _add_settings(data, project, targets, command)),
        _edit_json(other_path, _strip_settings(project, targets), only_if_exists=True),
        _edit_json(project / ".mcp.json", lambda data: _add_mcp(data, command, stored) if with_mcp else data),
        Change(config_path(project), _read(config_path(project)), _config_text(stored, block_broad_search)),
    ]
    ignore = _gitignore_change(project, settings_path) if local else None
    return [change for change in [*changes, ignore] if change is not None and change.changed]


def plan_remove(project: Path) -> list[Change]:
    if not config_path(project).is_file():
        raise SourceError("nothing is protected in this folder")
    targets = list(load_config(project).paths)
    changes = [
        _edit_json(_settings_path(project, False), _strip_settings(project, targets), only_if_exists=True),
        _edit_json(_settings_path(project, True), _strip_settings(project, targets), only_if_exists=True),
        _edit_json(project / ".mcp.json", _strip_mcp, only_if_exists=True),
        Change(config_path(project), _read(config_path(project)), None),
    ]
    return [change for change in changes if change is not None and change.changed]


def apply(changes: list[Change]) -> None:
    for change in changes:
        if change.after is None:
            change.path.unlink(missing_ok=True)
            _remove_if_empty(change.path.parent)
            continue
        change.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = change.path.with_name(change.path.name + ".tmp")
        temporary.write_text(change.after, encoding="utf-8", newline="")
        os.replace(temporary, change.path)


def self_test(project: Path, command: list[str], target: Path) -> str | None:
    """Run the hook the way Claude Code will and check that it refuses. None means it works."""
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(target / "x.csv")}, "cwd": str(project)}
    environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    try:
        result = subprocess.run(
            [*command, "hook"], input=json.dumps(payload), capture_output=True, text=True, timeout=60, env=environment, check=False
        )
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return (
            f"couldn't run `{' '.join(command)} hook`. The guard hook blocks tool calls when it fails, so "
            "fix the command (try --command 'uv run datanyx') before using Claude Code here."
        )
    return None if decision == "deny" else "the guard hook ran but didn't refuse a protected file"


# --- settings.json -------------------------------------------------------------------


def _add_settings(data: dict, project: Path, targets: list[Path], command: list[str]) -> dict:
    data = _without_ours(data, project, targets)
    deny = data.setdefault("permissions", {}).setdefault("deny", [])
    deny.extend(rule for rule in _rules(project, targets) if rule not in deny)
    entry = {
        "matcher": DENY_TOOLS,
        "hooks": [
            {
                "type": "command",
                "command": command[0],
                "args": [*command[1:], "hook"],
                "onFailure": "block",
                "statusMessage": HOOK_LABEL,
            }
        ],
    }
    data.setdefault("hooks", {}).setdefault("PreToolUse", []).append(entry)
    return data


def _strip_settings(project: Path, targets: list[Path]):
    return lambda data: _without_ours(data, project, targets)


def _without_ours(data: dict, project: Path, targets: list[Path]) -> dict:
    ours = set(_rules(project, targets))
    permissions = data.get("permissions")
    if isinstance(permissions, dict) and isinstance(permissions.get("deny"), list):
        permissions["deny"] = [rule for rule in permissions["deny"] if rule not in ours]
        if not permissions["deny"]:
            del permissions["deny"]
        if not permissions:
            del data["permissions"]
    hooks = data.get("hooks")
    if isinstance(hooks, dict) and isinstance(hooks.get("PreToolUse"), list):
        hooks["PreToolUse"] = [entry for entry in hooks["PreToolUse"] if not _is_ours(entry)]
        if not hooks["PreToolUse"]:
            del hooks["PreToolUse"]
        if not hooks:
            del data["hooks"]
    return data


def _is_ours(entry: Any) -> bool:
    inner = entry.get("hooks") if isinstance(entry, dict) else None
    return isinstance(inner, list) and any(isinstance(hook, dict) and hook.get("statusMessage") == HOOK_LABEL for hook in inner)


def _rules(project: Path, targets: list[Path]) -> list[str]:
    """Deny rules for the targets that are inside the project. Others rely on the hook alone."""
    rules: list[str] = []
    for target in targets:
        shown = stored_form(project, target)
        if Path(shown).is_absolute() or (len(shown) > 1 and shown[1] == ":"):
            continue
        if target.is_dir():
            patterns = [f"{shown}/**"]
        elif target.is_file():
            patterns = [shown]
        else:
            patterns = [shown, f"{shown}/**"]
        rules.extend(f"{tool}({pattern})" for tool in _RULE_TOOLS for pattern in patterns)
    return rules


# --- .mcp.json -----------------------------------------------------------------------


def _add_mcp(data: dict, command: list[str], roots: list[str]) -> dict:
    servers = data.setdefault("mcpServers", {})
    servers[MCP_NAME] = {"command": command[0], "args": [*command[1:], "mcp", *[part for root in roots for part in ("--root", root)]]}
    return data


def _strip_mcp(data: dict) -> dict:
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and MCP_NAME in servers:
        del servers[MCP_NAME]
        if not servers:
            del data["mcpServers"]
    return data


# --- helpers -------------------------------------------------------------------------


def _settings_path(project: Path, local: bool) -> Path:
    return project / ".claude" / ("settings.local.json" if local else "settings.json")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _edit_json(path: Path, edit, only_if_exists: bool = False) -> Change | None:
    before = _read(path)
    if not before and only_if_exists:
        return None
    try:
        data = json.loads(before) if before.strip() else {}
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        raise SourceError(f"{path.name} isn't a JSON object, so it was left alone. Fix or remove it first") from None
    after = edit(data)
    text = json.dumps(after, indent=2, ensure_ascii=False) + chr(10) if after else ""
    return Change(path, before, text or None)


def _config_text(stored: list[str], block_broad_search: bool) -> str:
    return json.dumps({"version": 1, "paths": stored, "block_broad_search": block_broad_search}, indent=2) + chr(10)


def _gitignore_change(project: Path, settings_path: Path) -> Change | None:
    if settings_path.exists() or not (project / ".git").exists():
        return None
    path = project / ".gitignore"
    before = _read(path)
    entry = ".claude/settings.local.json"
    if entry in before.splitlines():
        return None
    separator = "" if not before or before.endswith(chr(10)) else chr(10)
    return Change(path, before, before + separator + entry + chr(10))


def _remove_if_empty(folder: Path) -> None:
    if folder.name in (CONFIG_DIR, ".claude"):
        try:
            folder.rmdir()
        except OSError:
            pass


__all__ = ["Change", "apply", "plan_protect", "plan_remove", "self_test", "split_command"]
