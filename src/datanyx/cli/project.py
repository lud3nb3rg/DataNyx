"""`datanyx init`, `config`, `audit` and `doctor`."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..audit import read_log, summarize_log
from ..config import CONFIG_NAME, LOCAL_NAME, STARTER, STARTER_LOCAL, describe
from ..sources import SourceError
from ..text import (
    DEFAULT_MODEL,
)
from ..text.backends import availability as backend_availability
from ..text.packs import PACKS
from .common import (
    _cfg,
)


def _run_init(args: argparse.Namespace) -> int:
    name = LOCAL_NAME if args.local else CONFIG_NAME
    target = Path(name)
    if target.exists():
        raise SourceError(f"{name} already exists here")
    target.write_text(STARTER_LOCAL if args.local else STARTER, encoding="utf-8")
    print(f"Created {name}")
    if args.local:
        _keep_out_of_git(target.resolve())
    return 0


def _keep_out_of_git(path: Path) -> None:
    """Add a file to the .gitignore of the repository it is in, wherever in the repository that is."""
    root = next((folder for folder in path.parents if (folder / ".git").exists()), None)
    if root is None:
        return
    entry = path.relative_to(root).as_posix()
    ignore = root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if entry not in existing.splitlines():
        separator = "" if not existing or existing.endswith(chr(10)) else chr(10)
        ignore.write_text(existing + separator + entry + chr(10), encoding="utf-8")
        print(f"Added {entry} to .gitignore, so the terms in it stay private.")


def _run_audit(args: argparse.Namespace) -> int:
    records, unreadable = read_log(args.log, args.since)
    print(summarize_log(records, unreadable, args.format))
    return 0


def _run_config(args: argparse.Namespace) -> int:
    print(describe(_cfg(args), args.reveal))
    return 0


_OPTIONAL = (
    ("CSV, TSV, JSON, XML, SQLite", "sqlalchemy", "built in"),
    ("YAML", "yaml", "datanyx[yaml]"),
    ("Excel", "openpyxl", "datanyx[excel]"),
    ("Parquet and ORC", "pyarrow", "datanyx[parquet] or datanyx[orc]"),
    ("Avro", "fastavro", "datanyx[avro]"),
    ("DuckDB", "duckdb", "datanyx[duckdb]"),
    ("SAS, Stata and SPSS", "pyreadstat", "datanyx[stats]"),
    ("MongoDB", "pymongo", "datanyx[mongodb]"),
    ("Files at a URL (https)", "aiohttp", "datanyx[remote]"),
    ("Files in S3", "s3fs", "datanyx[s3]"),
    ("Files in Google Cloud Storage", "gcsfs", "datanyx[gcs]"),
    ("Files in Azure storage", "adlfs", "datanyx[azure]"),
    ("Large JSON streaming", "ijson", "datanyx[streaming]"),
    ("MCP server", "mcp", "datanyx[mcp]"),
    ("PostgreSQL", "psycopg", "datanyx[postgres]"),
    ("MySQL / MariaDB", "pymysql", "datanyx[mysql]"),
    ("SQL Server", "pymssql", "datanyx[mssql]"),
    ("Oracle", "oracledb", "datanyx[oracle]"),
    ("Text anonymization", "spacy", "datanyx[text]"),
)


def _run_doctor(args: argparse.Namespace) -> int:
    import importlib.util

    for label, module, install in _OPTIONAL:
        found = importlib.util.find_spec(module) is not None
        print(f"{'ok     ' if found else 'missing'}  {label}" + ("" if found else f'  ->  pip install "{install}"'))
    if importlib.util.find_spec("spacy") is not None:
        import spacy.util

        found = spacy.util.is_package(args.model)
        print(f"{'ok     ' if found else 'missing'}  NER model {args.model}" + ("" if found else f"  ->  python -m spacy download {args.model}"))
    for name, installed in backend_availability().items():
        hint = "" if installed else ('  ->  pip install "datanyx[presidio]"' if name == "presidio" else "  ->  pip install gliner")
        print(f"{'ok     ' if installed else 'missing'}  backend {name}{hint}")
    print(f"        packs: {', '.join(sorted(PACKS))}")
    return 0


def add_init(commands: argparse._SubParsersAction) -> None:
    init = commands.add_parser("init", help="create a datanyx.toml of commented settings in this folder")
    init.add_argument(
        "--local", action="store_true", help="create datanyx.local.toml instead, for terms to hide that shouldn't be committed"
    )


def add_audit(commands: argparse._SubParsersAction) -> None:
    audit = commands.add_parser(
        "audit",
        help="summarise the audit log an MCP server wrote: which tools were called, on what, and what failed",
    )
    audit.add_argument("log", help="the file given to `datanyx mcp --audit-log`")
    audit.add_argument("--since", metavar="TIME", help="only calls from then on, such as 2025-01-31 or 2025-01-31T09:30:00")
    audit.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")


def add_config(commands: argparse._SubParsersAction) -> None:
    show = commands.add_parser("config", help="show the settings in effect and which files they come from")
    show.add_argument("--reveal", action="store_true", help="also show the terms to hide, which are otherwise only counted")


def add_doctor(commands: argparse._SubParsersAction) -> None:
    doctor = commands.add_parser("doctor", help="check which optional packages and models are installed")
    doctor.add_argument("--model", default=DEFAULT_MODEL, help="spaCy NER model to look for (default: %(default)s)")
