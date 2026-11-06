"""Check pandas code against the schema, without running it.

This is static and deliberately cautious. A DataFrame is tracked only while it is
clearly one: read straight from a file the schema covers, or copied, filtered or
sorted from one that is. Anything else (a merge, a function's return value) stops the
tracking, so the checker stays quiet where it can't be sure.

Checked on tracked frames: column names used as `df["a"]`, `df[["a", "b"]]` and
`df.a`; names passed to `groupby`, `sort_values`, `merge`, `pivot_table`, `drop`,
`rename` and similar; and a few type mistakes that pandas doesn't flag (taking the mean
of a text column, `.dt` on text, comparing a text column with a number). SQL in string
literals passed to `execute` or `read_sql` is checked too, when sqlglot is installed.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from .model import NUMERIC, TEMPORAL, TEXT, Column, Diagnostic, Table, suggest
from .sql_check import check_sql, sqlglot_available

_READERS = frozenset(
    {"read_csv", "read_json", "read_excel", "read_parquet", "read_table", "read_feather", "read_xml", "read_orc", "read_fwf"}
)
_SQL_CALLS = frozenset({"execute", "executemany", "read_sql", "read_sql_query", "sql"})
# Methods whose result has the same columns as the frame they are called on.
_SAME_COLUMNS = frozenset(
    {"copy", "sort_values", "dropna", "drop_duplicates", "head", "tail", "sample", "fillna", "query", "reset_index", "astype"}
)
_CHAINABLE = _SAME_COLUMNS | {"groupby"}
# method -> (position of a column argument, keywords that hold column names)
_COLUMN_ARGUMENTS: dict[str, tuple[int | None, tuple[str, ...]]] = {
    "groupby": (0, ("by",)),
    "sort_values": (0, ("by",)),
    "set_index": (0, ("keys",)),
    "drop_duplicates": (None, ("subset",)),
    "dropna": (None, ("subset",)),
    "value_counts": (None, ("subset",)),
    "pivot_table": (None, ("index", "columns", "values")),
    "pivot": (None, ("index", "columns", "values")),
    "drop": (None, ("columns",)),
    "explode": (0, ("column",)),
}
_NUMERIC_ONLY = frozenset({"mean", "median", "std", "var"})
# What `df.name` can mean other than a column. Listed so that a check needs no pandas installed, and
# doesn't pay for importing it. Includes names older versions of pandas have.
_DATAFRAME_ATTRIBUTES = frozenset(
    ["T", "abs", "add", "add_prefix", "add_suffix", "agg", "aggregate", "align", "all", "any", "append", "apply", "applymap", "asfreq", "asof", "assign", "astype", "at", "at_time", "attrs", "axes", "backfill", "between_time", "bfill", "bool", "boxplot", "clip", "columns", "combine", "combine_first", "compare", "convert_dtypes", "copy", "corr", "corrwith", "count", "cov", "cummax", "cummin", "cumprod", "cumsum", "describe", "diff", "div", "divide", "dot", "drop", "drop_duplicates", "droplevel", "dropna", "dtypes", "duplicated", "empty", "eq", "equals", "eval", "ewm", "expanding", "explode", "ffill", "fillna", "filter", "first", "first_valid_index", "flags", "floordiv", "from_arrow", "from_dict", "from_records", "ge", "get", "groupby", "gt", "head", "hist", "iat", "idxmax", "idxmin", "iloc", "index", "infer_objects", "info", "insert", "interpolate", "isetitem", "isin", "isna", "isnull", "items", "iteritems", "iterrows", "itertuples", "join", "keys", "kurt", "kurtosis", "last", "last_valid_index", "le", "loc", "lookup", "lt", "mad", "map", "mask", "max", "mean", "median", "melt", "memory_usage", "merge", "min", "mod", "mode", "mul", "multiply", "ndim", "ne", "nlargest", "notna", "notnull", "nsmallest", "nunique", "pad", "pct_change", "pipe", "pivot", "pivot_table", "plot", "pop", "pow", "prod", "product", "quantile", "query", "radd", "rank", "rdiv", "reindex", "reindex_like", "rename", "rename_axis", "reorder_levels", "replace", "resample", "reset_index", "rfloordiv", "rmod", "rmul", "rolling", "round", "rpow", "rsub", "rtruediv", "sample", "select", "select_dtypes", "sem", "set_axis", "set_flags", "set_index", "shape", "shift", "size", "skew", "slice_shift", "sort_index", "sort_values", "sparse", "squeeze", "stack", "std", "style", "sub", "subtract", "sum", "swapaxes", "swaplevel", "tail", "take", "to_clipboard", "to_csv", "to_dict", "to_excel", "to_feather", "to_gbq", "to_hdf", "to_html", "to_iceberg", "to_json", "to_latex", "to_markdown", "to_numpy", "to_orc", "to_parquet", "to_period", "to_pickle", "to_records", "to_sql", "to_stata", "to_string", "to_timestamp", "to_xarray", "to_xml", "transform", "transpose", "truediv", "truncate", "tshift", "tz_convert", "tz_localize", "unstack", "update", "value_counts", "values", "var", "where", "xs"]
)


@dataclass
class _Frame:
    table: Table
    columns: dict[str, Column]
    open: bool

    def copy(self) -> _Frame:
        return _Frame(self.table, dict(self.columns), self.open)


@dataclass
class _State:
    tables: Sequence[Table]
    dialect: str | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    skipped_sql: bool = False


def check_python(code: str, tables: Sequence[Table], dialect: str | None = None) -> list[Diagnostic]:
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [Diagnostic("error", f"Python syntax error: {error.msg}", error.lineno or 1, error.offset)]
    state = _State(tables, dialect)
    _Visitor(state).visit(tree)
    if state.skipped_sql:
        state.diagnostics.append(
            Diagnostic("warning", "SQL in this script wasn't checked: install datanyx[check] to check it", 1)
        )
    return sorted(state.diagnostics, key=lambda item: (item.line, item.column or 0))


class _Visitor(ast.NodeVisitor):
    def __init__(self, state: _State) -> None:
        self.state = state
        self.frames: dict[str, _Frame] = {}
        self.pandas_names: set[str] = set()
        self.reader_names: set[str] = set()

    # --- reporting -------------------------------------------------------------

    def add(self, severity: str, message: str, node: ast.AST, hint: str | None = None) -> None:
        self.state.diagnostics.append(
            Diagnostic(severity, message, getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1, hint)
        )

    def unknown_column(self, frame: _Frame, name: str, node: ast.AST) -> None:
        label = frame.table.name
        hint = suggest(name, frame.columns, case_sensitive=True)
        if frame.open:
            self.add(
                "warning",
                f"column {name!r} isn't in the schema of {label!r}, which hides some column names, so it may be one of those",
                node,
                hint,
            )
        else:
            self.add("error", f"column {name!r} isn't in {label!r}", node, hint)

    def check_columns(self, frame: _Frame | None, names: list[tuple[str, ast.AST]]) -> None:
        if frame is None:
            return
        for name, node in names:
            if name not in frame.columns:
                self.unknown_column(frame, name, node)

    # --- imports ---------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "pandas":
                self.pandas_names.add(alias.asname or "pandas")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "pandas":
            for alias in node.names:
                if alias.name in _READERS:
                    self.reader_names.add(alias.asname or alias.name)

    # --- tracking frames -------------------------------------------------------

    def find_table(self, path: str) -> Table | None:
        base = os.path.basename(path.replace("\\", "/")).lower()
        for table in self.state.tables:
            if base == table.name.lower() or base in {alias.lower() for alias in table.aliases}:
                return table
        only = list(self.state.tables)
        return only[0] if len(only) == 1 and only[0].name == "data" else None

    def frame_of(self, node: ast.AST) -> _Frame | None:
        """The tracked frame an expression is, or a chain of calls that keeps its columns."""
        if isinstance(node, ast.Name):
            return self.frames.get(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _CHAINABLE:
            return self.frame_of(node.func.value)
        if isinstance(node, ast.Subscript) and not _names(node.slice) and not isinstance(node.slice, (ast.Constant, ast.Slice)):
            return self.frame_of(node.value)  # A filter, df[df["a"] > 1], keeps every column.
        return None

    def derive(self, value: ast.AST) -> _Frame | None:
        """The frame an assigned value is, if it is clearly one."""
        if isinstance(value, ast.Call):
            func = value.func
            is_reader = (isinstance(func, ast.Attribute) and func.attr in _READERS and isinstance(func.value, ast.Name)
                         and func.value.id in self.pandas_names) or (isinstance(func, ast.Name) and func.id in self.reader_names)
            if is_reader and value.args and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str):
                table = self.find_table(value.args[0].value)
                return _Frame(table, dict(table.columns), table.open) if table else None
            if isinstance(func, ast.Attribute) and func.attr in _SAME_COLUMNS:
                inner = self.frame_of(func.value)
                if inner is None:
                    return None
                frame = inner.copy()
                if func.attr == "reset_index":
                    frame.columns.setdefault("index", Column())
                if func.attr == "astype":
                    frame.columns = {name: Column() for name in frame.columns}
                return frame
            return None
        if isinstance(value, ast.Name):
            inner = self.frames.get(value.id)
            return inner.copy() if inner else None
        if isinstance(value, ast.Subscript):
            inner = self.frame_of(value.value)
            if inner is None:
                return None
            names = _names(value.slice)
            if names and isinstance(value.slice, (ast.List, ast.Tuple)):
                return _Frame(inner.table, {n: inner.columns[n] for n, _ in names if n in inner.columns}, inner.open)
            if not names and not isinstance(value.slice, (ast.Constant, ast.Slice)):
                return inner.copy()  # A filter: df[df["a"] > 1].
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        derived = self.derive(node.value)
        for target in node.targets:
            self.assign(target, derived)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self.assign(node.target, self.derive(node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Subscript):
            self.read_subscript(node.target)  # `df["a"] += 1` reads the column first.
        self.assign(node.target, None, keep=True)

    def assign(self, target: ast.AST, derived: _Frame | None, keep: bool = False) -> None:
        if isinstance(target, ast.Name):
            if derived is not None:
                self.frames[target.id] = derived
            elif not keep:
                self.frames.pop(target.id, None)
        elif isinstance(target, ast.Subscript):
            frame = self.frame_of(target.value)
            if frame is not None:
                for name, _ in _names(target.slice):
                    frame.columns[name] = Column()  # New or replaced: what it holds now isn't known.
            else:
                self.visit(target)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self.assign(item, None)

    # --- checks ----------------------------------------------------------------

    def read_subscript(self, node: ast.Subscript) -> None:
        self.check_columns(self.frame_of(node.value), _names(node.slice))

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Load):
            self.read_subscript(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and isinstance(node.ctx, ast.Load):
            frame = self.frames.get(node.value.id)
            if (
                frame
                and not node.attr.startswith("_")
                and node.attr not in frame.columns
                and node.attr not in _DATAFRAME_ATTRIBUTES
            ):
                self.unknown_column(frame, node.attr, node)
        self.series_accessor(node)

    def series_accessor(self, node: ast.Attribute) -> None:
        """`df["a"].dt` and `.str`, which need a date or text column."""
        reference = self.column_reference(node.value)
        if reference is None or node.attr not in ("dt", "str"):
            return
        frame, name, column = reference
        text_source = frame.table.source_kind in ("csv", "tsv", "json", "jsonl", "yaml", "xml")
        if node.attr == "dt":
            from_text = column.kind in TEMPORAL and text_source
            if column.kind in NUMERIC | TEXT | {"boolean"} or from_text:
                self.add(
                    "warning",
                    f"{name!r} is read as text, so .dt will fail",
                    node,
                    f"convert it first: pd.to_datetime(df[{name!r}])",
                )
        elif column.kind in NUMERIC | {"boolean"}:
            self.add("warning", f".str on {column.kind} column {name!r}", node, ".str only works on text columns")

    def column_reference(self, node: ast.AST) -> tuple[_Frame, str, Column] | None:
        """`df["a"]` on a tracked frame, when `a` is a column with a known kind."""
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            frame = self.frame_of(node.value)
            column = frame.columns.get(node.slice.value) if frame else None
            if frame is not None and column is not None and column.known:
                return frame, node.slice.value, column
        return None

    def visit_Compare(self, node: ast.Compare) -> None:
        self.generic_visit(node)
        if len(node.comparators) != 1:
            return
        reference = self.column_reference(node.left)
        other = node.comparators[0]
        if reference is None or not isinstance(other, ast.Constant) or isinstance(other.value, bool):
            return
        _, name, column = reference
        value, op = other.value, node.ops[0]
        if column.kind in NUMERIC and isinstance(value, str):
            self.add("warning", f"comparing {column.kind} column {name!r} with the text {value!r}", node)
        elif column.kind in TEXT and isinstance(value, (int, float)):
            effect = "is always False" if isinstance(op, (ast.Eq, ast.NotEq)) else "raises TypeError"
            self.add("warning", f"comparing text column {name!r} with the number {value} {effect}", node, "quote the number")

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            self.visit(func.value)  # Not the method name itself: `df.sum` isn't a column.
            self.method(node, func)
        else:
            self.visit(func)
            if isinstance(func, ast.Name) and func.id in _SQL_CALLS:
                self.sql_argument(node)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def method(self, node: ast.Call, func: ast.Attribute) -> None:
        if func.attr in _SQL_CALLS:
            self.sql_argument(node)
        reference = self.column_reference(func.value)
        if reference and func.attr in _NUMERIC_ONLY:
            _, name, column = reference
            if column.kind in TEXT | TEMPORAL | {"boolean"}:
                self.add("warning", f".{func.attr}() on {column.kind} column {name!r}", node, "it isn't numeric")
        frame = self.frame_of(func.value)
        if frame is None:
            return
        method = func.attr
        if method in _COLUMN_ARGUMENTS:
            position, keywords = _COLUMN_ARGUMENTS[method]
            if position is not None and len(node.args) > position:
                self.check_columns(frame, _names(node.args[position]))
            for keyword in node.keywords:
                if keyword.arg in keywords:
                    self.check_columns(frame, _names(keyword.value))
        elif method == "merge":
            self.merge(node, frame)
        elif method == "join":
            # `on` is a column here and the other frame's index there, so the other frame has no say.
            for keyword in node.keywords:
                if keyword.arg == "on":
                    self.check_columns(frame, _names(keyword.value))
        elif method in ("rename", "astype", "agg", "aggregate"):
            for keyword in node.keywords:
                if method == "rename" and keyword.arg == "columns" and isinstance(keyword.value, ast.Dict):
                    self.check_columns(frame, _dict_keys(keyword.value))
            if method in ("astype", "agg", "aggregate") and node.args and isinstance(node.args[0], ast.Dict):
                self.check_columns(frame, _dict_keys(node.args[0]))

    def merge(self, node: ast.Call, frame: _Frame) -> None:
        other = self.frame_of(node.args[0]) if node.args else None
        for keyword in node.keywords:
            if keyword.arg == "on":
                self.check_columns(frame, _names(keyword.value))
                self.check_columns(other, _names(keyword.value))
            elif keyword.arg == "left_on":
                self.check_columns(frame, _names(keyword.value))
            elif keyword.arg == "right_on":
                self.check_columns(other, _names(keyword.value))

    def sql_argument(self, node: ast.Call) -> None:
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            return  # Built at run time (an f-string, say), so it can't be read here.
        if not sqlglot_available():
            self.state.skipped_sql = True
            return
        self.state.diagnostics.extend(
            check_sql(node.args[0].value, self.state.tables, self.state.dialect, node.args[0].lineno - 1)
        )


def _names(node: ast.AST) -> list[tuple[str, ast.AST]]:
    """String constants in `"a"` or `["a", "b"]`, with the nodes they came from."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(node.value, node)]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [(item.value, item) for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return []


def _dict_keys(node: ast.Dict) -> list[tuple[str, ast.AST]]:
    return [(key.value, key) for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
