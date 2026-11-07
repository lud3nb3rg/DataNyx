"""`datanyx protect` and the `hook` it installs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..sources import SourceError


def _run_hook(args: argparse.Namespace) -> int:
    from ..protect import run_hook

    decision = run_hook(sys.stdin.buffer.read().decode("utf-8", errors="replace"), require_config=args.require_config)
    if decision:
        sys.stdout.write(decision)
    return 0


def _run_protect(args: argparse.Namespace) -> int:
    from ..protect import apply, claude_code_warning, plan_protect, plan_remove, self_test, split_command

    project = Path(args.dir).resolve()
    command = split_command(args.launcher)
    targets: list[Path] = []
    if args.remove:
        if args.paths:
            raise SourceError("--remove takes no paths")
        changes = plan_remove(project)
    else:
        if not args.paths:
            raise SourceError("name at least one file or folder to protect")
        targets = [Path(path).resolve() for path in args.paths]
        for target in targets:
            if not target.exists():
                print(f"note: {target} doesn't exist yet; it is protected once it does", file=sys.stderr)
        changes = plan_protect(project, targets, command, args.local, not args.allow_broad_search, not args.no_mcp)

    if not changes:
        print("Already up to date.")
        return 0
    for change in changes:
        action = "delete" if change.after is None else ("create" if not change.before else "update")
        shown = os.path.relpath(change.path, project)
        print(f"{action} {shown}" if args.write else f"would {action} {shown}")
        if not args.write:
            print(change.diff())
            print()
    if not args.write:
        print("Nothing was changed. Run it again with --write to apply this.")
        return 0

    apply(changes)
    if args.remove:
        print("Protection removed.")
        return 0
    problem = self_test(project, command, targets[0])
    print("Checked: the guard hook refuses protected files." if problem is None else f"warning: {problem}")
    print(
        "note: this is a guardrail, not a sandbox: a script the agent runs can still read the data. "
        "For an agent that has no access to it at all, see docs/agents.md#hard-isolation-with-containers"
    )
    version_warning = claude_code_warning()
    if version_warning:
        print(f"warning: {version_warning}", file=sys.stderr)
    return 0 if problem is None else 1


def add_protect(commands: argparse._SubParsersAction) -> None:
    protect = commands.add_parser(
        "protect",
        help="keep an agent from reading data directly (Claude Code permissions, a guard hook, an MCP server)",
        description=(
            "Set up Claude Code so the agent can't read the named files and folders with its own "
            "tools, and can only work with them through DataNyx: schema, synth, check, scrub and "
            "run. By default this only shows what it would change; pass --write to apply it. "
            "It is a guardrail, not a sandbox: a script the agent runs can still read the data."
        ),
    )
    protect.add_argument("paths", nargs="*", metavar="PATH", help="files or folders to protect")
    protect.add_argument("--dir", default=".", metavar="PROJECT", help="the project folder (default: the current folder)")
    protect.add_argument(
        "--command",
        dest="launcher",  # Not "command": that is already the name of the subcommand.
        default="datanyx",
        help="how Claude Code starts datanyx, such as 'uv run datanyx' (default: datanyx)",
    )
    protect.add_argument("--local", action="store_true", help="use .claude/settings.local.json, which isn't shared, instead of settings.json")
    protect.add_argument("--allow-broad-search", action="store_true", help="don't refuse searches of a folder that has protected data inside it")
    protect.add_argument("--no-mcp", action="store_true", help="don't write .mcp.json")
    protect.add_argument("--remove", action="store_true", help="take protection out again")
    protect.add_argument("--write", action="store_true", help="apply the changes (the default is to show them)")


def add_hook(commands: argparse._SubParsersAction) -> None:
    hook = commands.add_parser(
        "hook",
        help="the Claude Code hook that enforces `protect` (reads a tool call on stdin)",
        description="Used by Claude Code, not by hand: reads a PreToolUse payload and refuses calls that touch protected data.",
    )
    hook.add_argument("--require-config", action="store_true", help="refuse everything if the protection settings are missing; `protect` installs the hook with this")
