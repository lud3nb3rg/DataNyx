"""Check SQL against the schema: tables, columns and the types they are used with.

Needs sqlglot (`datanyx[check]`). Nothing is executed: the query is only parsed and
compared with names and kinds from the schema.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ..sources.base import SourceError
from .model import NUMERIC, TEMPORAL, TEXT, Column, Diagnostic, Table, suggest

_DATE_LIKE = re.compile(r"\d{4}-\d{2}(-\d{2})?")
_TIME_LIKE = re.compile(r"\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*([AaPp][Mm]|[Zz]|[+-]\d{2}(:?\d{2})?)?")
_NUMBER_LIKE = re.compile(r"[+-]?\d+(\.\d+)?")


def sqlglot_available() -> bool:
    try:
        import sqlglot  # noqa: F401
    except ImportError:
        return False
    return True


def check_sql(
    code: str, tables: Sequence[Table], dialect: str | None = None, line_offset: int = 0
) -> list[Diagnostic]:
    try:
        import sqlglot
        from sqlglot import errors as sql_errors
    except ImportError:
        raise SourceError("checking SQL needs sqlglot; install datanyx[check]") from None

    try:
        statements = sqlglot.parse(code, read=dialect)
    except sql_errors.ParseError as error:
        first = error.errors[0] if error.errors else {}
        return [
            Diagnostic(
                "error",
                f"SQL syntax error: {first.get('description', 'could not parse the query')}",
                first.get("line", 1) + line_offset,
                first.get("col"),
            )
        ]
    except sql_errors.TokenError:
        return [Diagnostic("error", "SQL syntax error: could not read the query", 1 + line_offset)]
    except (ValueError, KeyError):
        raise SourceError("unknown SQL dialect") from None

    checker = _Checker(tables, line_offset)
    for statement in statements:
        if statement is not None:
            checker.statement(statement)
    return checker.diagnostics


class _Checker:
    def __init__(self, tables: Sequence[Table], line_offset: int) -> None:
        from sqlglot import exp

        self.exp = exp
        self.tables = list(tables)
        self.line_offset = line_offset
        self.diagnostics: list[Diagnostic] = []
        self.kinds: dict[int, Column] = {}  # id(column node) -> what it resolved to
        self.reported: set[tuple[str, int, str]] = set()

    # --- helpers -------------------------------------------------------------

    def table(self, name: str) -> Table | None:
        return next((table for table in self.tables if table.answers_to(name)), None)

    def lookup(self, node: Any) -> Table | None:
        """The table a FROM item names. `customers.csv` reads as schema `customers`, table `csv`,
        so the dotted name is tried first; a schema in front of a real table name is ignored."""
        if node.args.get("db"):
            dotted = self.table(f"{node.args['db'].name}.{node.name}")
            if dotted is not None:
                return dotted
        return self.table(node.name)

    def add(self, severity: str, message: str, node: Any, hint: str | None = None) -> None:
        line, column = self.position(node)
        key = (message, line, str(column))
        if key not in self.reported:
            self.reported.add(key)
            self.diagnostics.append(Diagnostic(severity, message, line, column, hint))

    def position(self, node: Any) -> tuple[int, int | None]:
        exp = self.exp
        for identifier in node.find_all(exp.Identifier):
            meta = identifier.meta
            if "line" in meta:
                start = meta["col"] - (meta.get("end", 0) - meta.get("start", 0))
                return meta["line"] + self.line_offset, max(start, 1)
        return 1 + self.line_offset, None

    # --- statements ----------------------------------------------------------

    def statement(self, statement: Any) -> None:
        exp = self.exp
        from sqlglot.optimizer.scope import traverse_scope

        ctes = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        for table_node in statement.find_all(exp.Table):
            if (
                table_node.name
                and table_node.name.lower() not in ctes
                and not table_node.args.get("db")
                and self.lookup(table_node) is None
            ):
                self.add(
                    "error",
                    f"unknown table {table_node.name!r}",
                    table_node,
                    suggest(table_node.name, [t.name for t in self.tables]) or _list_names(self.tables),
                )

        try:
            scopes = traverse_scope(statement)
        except Exception:  # sqlglot can't scope every kind of statement; names are still checked.
            scopes = []
        if not scopes:
            self.simple_statement(statement)
        for scope in scopes:
            self.scope(scope)
        self.type_rules(statement)

    def simple_statement(self, statement: Any) -> None:
        """UPDATE, DELETE and INSERT: columns are checked against the one table involved."""
        exp = self.exp
        targets = [self.lookup(t) for t in statement.find_all(exp.Table) if self.lookup(t) is not None]
        if len(targets) != 1 or targets[0] is None:
            return
        table = targets[0]
        for column in statement.find_all(exp.Column):
            self.check_name(column, table, column.name)
        if isinstance(statement, exp.Insert) and isinstance(statement.this, exp.Schema):
            for identifier in statement.this.expressions:
                self.check_name(identifier, table, identifier.name)

    def scope(self, scope: Any) -> None:
        exp = self.exp
        sources = {alias.lower(): source for alias, source in scope.sources.items()}
        for column in scope.columns:
            if isinstance(column.this, exp.Star):
                continue
            name, qualifier = column.name, column.table
            if qualifier:
                source = self.find_source(scope, qualifier)
                if source is _MISSING:
                    self.add("error", f"unknown table or alias {qualifier!r}", column, suggest(qualifier, list(scope.sources)))
                else:
                    self.resolve_in(column, source, name)
            else:
                self.resolve_unqualified(scope, sources, column, name)

    def find_source(self, scope: Any, alias: str) -> Any:
        """The source an alias names here, or in an enclosing query (correlated subqueries)."""
        probe = scope
        while probe is not None:
            for known, source in probe.sources.items():
                if known.lower() == alias.lower():
                    return source
            probe = probe.parent
        return _MISSING

    def columns_of(self, source: Any) -> tuple[Table | dict[str, Column] | None, str]:
        """What a source offers: a schema table, the names a subquery selects, or None if unknown."""
        exp = self.exp
        if isinstance(source, exp.Table):
            table = self.lookup(source)
            return (table, table.name if table else source.name)
        expression = getattr(source, "expression", None)
        if expression is not None and hasattr(expression, "named_selects"):
            names = expression.named_selects
            if "*" in names or any(not name for name in names):
                return None, ""
            return {name: Column() for name in names}, ""
        return None, ""

    def select_aliases(self, scope: Any) -> set[str]:
        """Names given with AS in the select list. A plain selected column isn't an alias."""
        exp = self.exp
        return {item.alias.lower() for item in getattr(scope.expression, "expressions", []) if isinstance(item, exp.Alias)}

    def resolve_in(self, column: Any, source: Any, name: str) -> None:
        offered, label = self.columns_of(source)
        if isinstance(offered, Table):
            found = offered.column(name)
            if found is not None:
                self.kinds[id(column)] = found
            else:
                self.report_unknown(column, name, offered, label or offered.name)
        elif isinstance(offered, dict) and not any(key.lower() == name.lower() for key in offered):
            self.add("error", f"column {name!r} is not selected by the subquery it comes from", column, suggest(name, offered))

    def check_name(self, node: Any, table: Table, name: str) -> None:
        found = table.column(name)
        if found is None:
            self.report_unknown(node, name, table, table.name)

    def resolve_unqualified(self, scope: Any, sources: dict[str, Any], column: Any, name: str) -> None:
        found: list[tuple[str, Column]] = []
        unknown_source = False
        open_source = False
        candidates: list[str] = []
        probe = scope
        while probe is not None and not found:
            for alias, source in probe.sources.items():
                offered, _ = self.columns_of(source)
                if offered is None:
                    unknown_source = True
                elif isinstance(offered, Table):
                    open_source = open_source or offered.open
                    candidates.extend(offered.columns)
                    match = offered.column(name)
                    if match is not None:
                        found.append((alias, match))
                else:
                    candidates.extend(offered)
                    if any(key.lower() == name.lower() for key in offered):
                        found.append((alias, Column()))
            if name.lower() in self.select_aliases(probe):
                return  # A select alias, used in ORDER BY, GROUP BY or HAVING.
            probe = probe.parent
        if len(found) == 1:
            self.kinds[id(column)] = found[0][1]
        elif len(found) > 1:
            where = " and ".join(repr(alias) for alias, _ in found)
            self.add("error", f"column {name!r} is ambiguous: it exists in {where}", column, "put the table name in front of it")
        elif not unknown_source:
            self.report_unknown(column, name, None, None, candidates, open_source)

    def report_unknown(
        self,
        node: Any,
        name: str,
        table: Table | None,
        label: str | None,
        candidates: Sequence[str] = (),
        is_open: bool = False,
    ) -> None:
        options = list(table.columns) if table is not None else list(dict.fromkeys(candidates))
        is_open = table.open if table is not None else is_open
        where = f" in {label!r}" if label else ""
        if is_open:
            self.add(
                "warning",
                f"column {name!r} isn't in the schema{where}, which hides some column names, so it may be one of those",
                node,
                suggest(name, options),
            )
        else:
            self.add("error", f"unknown column {name!r}{where}", node, suggest(name, options))

    # --- types ---------------------------------------------------------------

    def kind_of(self, node: Any) -> str | None:
        exp = self.exp
        if isinstance(node, exp.Column):
            column = self.kinds.get(id(node))
            return column.kind if column is not None and column.known else None
        if isinstance(node, exp.Literal):
            return "text-literal" if node.is_string else "number-literal"
        return None

    def type_rules(self, statement: Any) -> None:
        exp = self.exp
        comparisons = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
        for node in statement.walk():
            if isinstance(node, comparisons):
                self.comparison(node)
            elif isinstance(node, (exp.Like, exp.ILike)):
                kind = self.kind_of(node.this)
                if kind in NUMERIC:
                    self.add("warning", f"LIKE on {kind} column {node.this.sql()}", node, "LIKE is for text; cast the column first")
            elif isinstance(node, (exp.Sum, exp.Avg)):
                # SUM(DISTINCT x) holds x in the DISTINCT's own list.
                target = node.this.expressions[0] if isinstance(node.this, exp.Distinct) and len(node.this.expressions) == 1 else node.this
                kind = self.kind_of(target)
                if kind in TEXT | TEMPORAL | {"boolean"}:
                    name = "SUM" if isinstance(node, exp.Sum) else "AVG"
                    self.add("warning", f"{name} over {kind} column {target.sql()}", node, "it is not a number")

    def comparison(self, node: Any) -> None:
        exp = self.exp
        left, right = node.left, node.right
        if isinstance(node, (exp.EQ, exp.NEQ)) and (isinstance(left, exp.Null) or isinstance(right, exp.Null)):
            operator = "IS NULL" if isinstance(node, exp.EQ) else "IS NOT NULL"
            self.add("warning", "comparing with NULL using = or <> is never true", node, f"use {operator}")
            return
        first, second = self.kind_of(left), self.kind_of(right)
        if first is None or second is None:
            return
        if second.endswith("-literal"):
            self.column_vs_literal(node, left, first, right, second)
        elif first.endswith("-literal"):
            self.column_vs_literal(node, right, second, left, first)
        else:
            groups = [group for group in (NUMERIC, TEXT, TEMPORAL) if first in group or second in group]
            if len(groups) > 1 and not (first in groups[0] and second in groups[0]):
                self.add("warning", f"comparing {first} column {left.sql()} with {second} column {right.sql()}", node)

    def column_vs_literal(self, node: Any, column: Any, kind: str, literal: Any, literal_kind: str) -> None:
        value = literal.name
        if kind in NUMERIC and literal_kind == "text-literal" and not _NUMBER_LIKE.fullmatch(value.strip()):
            self.add("warning", f"comparing {kind} column {column.sql()} with the text {value!r}", node)
        elif kind in TEXT and literal_kind == "number-literal":
            self.add("warning", f"comparing text column {column.sql()} with the number {value}", node, "quote it, or cast the column")
        elif kind in TEMPORAL and literal_kind == "number-literal":
            example = "a time such as '12:30:00'" if kind == "time" else "a date such as '2024-01-31'"
            self.add("warning", f"comparing {kind} column {column.sql()} with the number {value}", node, f"use {example}")
        elif kind == "time":
            if not _TIME_LIKE.fullmatch(value.strip()):
                self.add("warning", f"comparing time column {column.sql()} with {value!r}, which isn't a time", node)
        elif kind in TEMPORAL and not _DATE_LIKE.match(value):
            self.add("warning", f"comparing {kind} column {column.sql()} with {value!r}, which isn't a date", node)


class _Missing:
    pass


_MISSING = _Missing()


def _list_names(tables: Sequence[Table]) -> str | None:
    return ("known tables: " + ", ".join(repr(t.name) for t in tables[:12])) if tables else None
