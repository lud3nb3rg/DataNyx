"""Command line interface.

An agent may be the one running this, so everything it prints counts as visible
to the agent. Unexpected errors are therefore reported by type only: a traceback
or exception message could quote the data being processed.
"""

from __future__ import annotations

import sys

from ..sources import SourceError
from .anonymize import _run_anonymize, _run_restore
from .check import _run_check
from .common import _use_utf8
from .diff import _run_diff
from .evaluate import _run_eval
from .parser import build_parser
from .project import _run_audit, _run_config, _run_doctor, _run_init
from .protect import _run_hook, _run_protect
from .pseudonymize import _run_pseudonymize
from .scan import _run_scan
from .schema import _run_schema
from .scrub import _run_command, _run_scrub
from .serve import _run_mcp
from .synth import _run_synth

__all__ = ["build_parser", "main"]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _use_utf8()
    try:
        commands = {"eval": _run_eval, "pseudonymize": _run_pseudonymize, "diff": _run_diff, "audit": _run_audit, "init": _run_init, "config": _run_config, "scan": _run_scan, "protect": _run_protect, "hook": _run_hook, "check": _run_check, "scrub": _run_scrub, "run": _run_command, "synth": _run_synth, "doctor": _run_doctor, "mcp": _run_mcp, "anonymize": _run_anonymize, "restore": _run_restore, "schema": _run_schema}
        return commands[args.command](args)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
    except RecursionError:
        print("error: input is too deeply nested", file=sys.stderr)
    except Exception as error:
        print(f"error: internal failure ({type(error).__name__})", file=sys.stderr)
    return 1
