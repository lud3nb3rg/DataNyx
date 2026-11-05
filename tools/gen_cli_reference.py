"""Write docs/cli-reference.md from the real argument parser, so it can't drift from the code.

    uv run python tools/gen_cli_reference.py            # rewrite the page
    uv run python tools/gen_cli_reference.py --check    # exit 1 if the page is out of date

The page is built from the parser's own data (not its --help text), so it reads the same on every
Python version.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "cli-reference.md"
PIPE = "\\|"


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _help_text(action: argparse.Action) -> str:
    text = action.help or ""
    if text is argparse.SUPPRESS:
        return ""
    params = dict(vars(action), prog="datanyx")
    try:
        text = text % params
    except (KeyError, TypeError, ValueError):
        pass
    return " ".join(text.split()).replace("|", PIPE)


def _cell(action: argparse.Action) -> str:
    if action.option_strings:
        names = ", ".join(f"`{name}`" for name in action.option_strings)
        if action.nargs != 0:
            names += f" `{action.metavar or action.dest.upper()}`"
        return names
    return f"`{action.metavar or action.dest}`"


def _table(parser: argparse.ArgumentParser) -> list[str]:
    rows = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)) or action.help is argparse.SUPPRESS:
            continue
        text = _help_text(action)
        if action.choices and not isinstance(action.choices, dict):
            text = text.rstrip(".") + ". Choices: " + ", ".join(f"`{choice}`" for choice in action.choices) + "."
        rows.append(f"| {_cell(action)} | {text.strip()} |")
    if not rows:
        return []
    return ["| Argument | Meaning |", "|---|---|", *rows, ""]


def _usage(name: str, parser: argparse.ArgumentParser) -> str:
    parts = [f"datanyx {name}"]
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction) or action.help is argparse.SUPPRESS:
            continue
        if not action.option_strings:
            parts.append(str(action.metavar or action.dest))
    return " ".join(parts) + " [options]"


def render() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from datanyx.cli import build_parser

    parser = build_parser()
    helps = {
        choice.dest: choice.help or ""
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for choice in action._choices_actions
    }
    out = [
        "# Command reference",
        "",
        "Every command and option, generated from the program itself. For what each command is for, see its",
        "own page; `datanyx COMMAND --help` prints the same information in a terminal.",
        "",
        "## Options for every command",
        "",
        *_table(parser),
    ]
    for name, sub in _subparsers(parser).items():
        out += [f"## `datanyx {name}`", ""]
        summary = " ".join((sub.description or "").split()) or helps.get(name, "")
        if summary:
            out += [summary, ""]
        out += ["```", _usage(name, sub), "```", ""]
        out += _table(sub)
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in args:
        if not PAGE.exists() or PAGE.read_text(encoding="utf-8") != text:
            print("docs/cli-reference.md is out of date: run `uv run python tools/gen_cli_reference.py`", file=sys.stderr)
            return 1
        return 0
    PAGE.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
