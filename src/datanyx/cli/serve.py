"""`datanyx mcp`."""

from __future__ import annotations

import argparse
import os

from ..sources import SourceError
from .common import (
    _add_model_and_detectors,
    _backend_detectors,
    _cfg,
    _custom_detectors,
    _models,
)


def _run_mcp(args: argparse.Namespace) -> int:
    from ..mcp_server import run

    config = _cfg(args)
    audit = args.audit_log or (str(config.audit_log) if config.audit_log else None)
    http = _host_and_port(args.http) if args.http else None
    token = os.environ.get(args.token_env, "") if http else None
    if http and not token:
        raise SourceError(f"set the {args.token_env} environment variable to the token clients must send")
    run(
        args.root or [str(root) for root in config.mcp_roots] or ["."],
        args.allow_urls or bool(config.mcp_allow_urls),
        _models(args),
        [*config.keep_entities, *args.keep_entity],
        hide=config.hide,
        detectors=[*_custom_detectors(args), *_backend_detectors(args)],
        audit_log=audit,
        http=http,
        token=token,
        scale_hints=bool(args.scale_hints or config.scale_hints),
        infer_keys=bool(args.infer_keys or config.infer_keys),
        column_hints=bool(args.column_hints or config.column_hints),
        views=bool(args.views or config.views),
        max_tables=args.max_tables,
        max_rows=args.max_rows or config.max_rows,
        debug_log=args.debug_log or (str(config.debug_log) if config.debug_log else None),
        allow_env=[*config.mcp_allow_env, *args.allow_env],
        scan_settings={
            "excludes": list(config.scan_exclude),
            "max_bytes": int((config.scan_max_size or 5.0) * 1024 * 1024),
            "include_examples": bool(config.scan_include_examples),
        },
    )
    return 0


def _host_and_port(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host.strip("[]") or not port.isdigit() or not 0 < int(port) < 65536:
        raise SourceError("--http takes HOST:PORT, for example 127.0.0.1:8808")
    return host.strip("[]"), int(port)


def add_mcp(commands: argparse._SubParsersAction) -> None:
    serve = commands.add_parser(
        "mcp",
        help="run an MCP server that gives an agent schemas and anonymized text, not data",
        description=(
            "Serve `describe_data` and `anonymize_file` over MCP (stdio, or HTTP with --http). The agent can only "
            "read files under --root, and never receives values, a mapping, or a restore tool."
        ),
    )
    serve.add_argument(
        "--root", action="append", default=[], metavar="DIR", help="folder the agent may read (repeatable; default: current folder)"
    )
    serve.add_argument("--allow-urls", action="store_true", help="accept database and file URLs from the agent (they may hold passwords or tokens, and the server fetches them)")
    serve.add_argument(
        "--allow-env", action="append", default=[], metavar="NAME",
        help="let the agent use this environment variable as a database URL, as env:NAME (repeatable); no other variable can be used",
    )
    _add_model_and_detectors(serve)
    serve.add_argument(
        "--keep-entity", action="append", default=[], metavar="KIND",
        help="leave this kind of entity visible in anonymized text (repeatable)",
    )
    serve.add_argument("--scale-hints", action="store_true", help="give agents each table's size as an order of magnitude")
    serve.add_argument("--infer-keys", action="store_true", help="give agents yes/no guesses about unique columns and joins")
    serve.add_argument("--column-hints", action="store_true", help="give agents coarse ranges for each column: distinct values, missing values, text length")
    serve.add_argument("--views", action="store_true", help="let agents see database views (names and columns only)")
    serve.add_argument(
        "--max-rows", type=int, metavar="N",
        help="read at most N rows of a data file, so one huge file can't hold the server up (sizes and key guesses are then left out)",
    )
    serve.add_argument(
        "--debug-log", metavar="FILE",
        help="append where each unexpected failure happened (file, line and function, never a message or value) to this private file",
    )
    serve.add_argument(
        "--max-tables", type=int, default=200, metavar="N",
        help="most tables describe_data returns at once, so a huge database can't flood the agent (default: %(default)s)",
    )
    serve.add_argument(
        "--http", metavar="HOST:PORT",
        help="serve over HTTP at /mcp instead of stdio, so a team can share one server; needs a token (--token-env)",
    )
    serve.add_argument(
        "--token-env", metavar="NAME", default="DATANYX_MCP_TOKEN",
        help="environment variable holding the bearer token HTTP clients must send (default: %(default)s)",
    )
    serve.add_argument("--audit-log", metavar="FILE", help="append a record of every tool call, never values, to this file")
