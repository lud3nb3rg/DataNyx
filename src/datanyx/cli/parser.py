"""The argument parser: one subcommand per module."""

from __future__ import annotations

import argparse

from .. import __version__
from ..text import DEFAULT_MODEL  # noqa: F401
from .anonymize import add_anonymize, add_restore
from .check import add_check
from .common import _Parser
from .diff import add_diff
from .evaluate import add_eval
from .project import add_audit, add_config, add_doctor, add_init
from .protect import add_hook, add_protect
from .pseudonymize import add_pseudonymize
from .scan import add_scan
from .schema import add_schema
from .scrub import add_run, add_scrub
from .serve import add_mcp
from .synth import add_synth


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="datanyx",
        description="Describe data to an AI agent without showing it the data.",
    )
    parser.add_argument("--version", action="version", version=f"datanyx {__version__}")
    parser.add_argument("--config", dest="config_file", metavar="FILE", help="settings file to use instead of the nearest datanyx.toml")
    parser.add_argument("--no-config", action="store_true", help="ignore datanyx.toml")
    commands = parser.add_subparsers(dest="command", required=True)

    add_schema(commands)
    add_synth(commands)
    add_pseudonymize(commands)
    add_eval(commands)
    add_scan(commands)
    add_protect(commands)
    add_hook(commands)
    add_check(commands)
    add_scrub(commands)
    add_run(commands)
    add_anonymize(commands)
    add_restore(commands)
    add_init(commands)
    add_diff(commands)
    add_audit(commands)
    add_config(commands)
    add_doctor(commands)
    add_mcp(commands)
    return parser
