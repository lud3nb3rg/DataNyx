"""Opt-in hints drawn from the values of a table, kept as yes/no flags and coarse buckets.

A schema from a file has no keys, so an agent can't tell that `orders.customer_id` points at `customers.id`, nor
whether `status` is a handful of categories or free text. Two options add that. The sources that read every row of a
table (CSV, TSV, JSON Lines, Excel and JSON arrays) hand each record to an `Evidence` object while they read. It keeps
hashes and counters in memory only, and what comes out is a flag or a bucket. Never a count, never a value.

`--infer-keys` (keys):

- only columns whose *names* look like keys (`id`, `user_id`, `uuid`, `code`, ...) are examined;
- a table needs at least `MIN_ROWS` rows, and a column no nulls and no repeats, to be called unique;
- two key-looking columns that are unique only together are a composite key, when neither is unique alone;
- a join needs a name match as well as the values being contained, because small integer ids are
  contained in each other all the time. For a composite key the names must be the same in both tables;
- a column with more than `MAX_DISTINCT` distinct values is given up on, which bounds the memory used.

`--column-hints` (profiles): for each column of a table of at least `MIN_ROWS` rows, how many distinct values it has
(as a range such as "10 to 99"), how often it is missing (never, rarely, often, mostly), and for text how long the
longest value is (as a range), and whether some values differ only by case or spacing. Distinct counting stops at
`MAX_PROFILE_DISTINCT`.

The flags are guesses from the data seen: the output says "inferred", and nothing enforces them. A table that was only
partly read (`--max-rows`) gets none of them.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from itertools import combinations
from typing import Any

from .schema import Array, DataSchema, Field, ForeignKey, Node, Object

MIN_ROWS = 20
MAX_DISTINCT = 200_000
MAX_PAIR_DISTINCT = 100_000
MAX_PAIR_COLUMNS = 5
MAX_PROFILE_DISTINCT = 1_000
_SEPARATOR = "#"
_SNAKE_KEY = re.compile(r"(^|[_\-\s.])(id|uuid|guid|key|code|ref|reference|number|num|no)$")
_CAMEL_KEY = re.compile(r"[a-z0-9]Id$")

#: (child table, child columns, parent table, parent columns)
Join = tuple[int, tuple[str, ...], int, tuple[str, ...]]


def is_key_name(name: str) -> bool:
    return bool(_SNAKE_KEY.search(name.lower()) or _CAMEL_KEY.search(name))


def _key_text(value: object) -> str | None:
    """The text a value counts as in a key, or None when it can't be part of one."""
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return str(value)


class _Column:
    """What was seen in one candidate column: hashes of its values, and how clean they were."""

    __slots__ = ("duplicate", "invalid", "non_null", "overflow", "values")

    def __init__(self) -> None:
        self.values: set[int] = set()
        self.non_null = 0
        self.duplicate = False
        self.overflow = False
        self.invalid = False  # A kind of value that can't be a key: a float, a list, a boolean.

    def add(self, value: object) -> None:
        if value is None or value == "" or self.invalid:
            return
        text = _key_text(value)
        if text is None:
            self.invalid = True
            self.values.clear()
            return
        self.non_null += 1
        if self.overflow:
            return
        digest = hash(text)
        if digest in self.values:
            self.duplicate = True
            return
        if len(self.values) >= MAX_DISTINCT:
            self.overflow = True
            self.values.clear()
            return
        self.values.add(digest)

    def usable(self) -> bool:
        return not self.invalid and not self.overflow


class _Pair:
    """Hashes of the pairs of values two key-looking columns hold in the same row."""

    __slots__ = ("count", "duplicate", "overflow", "values")

    def __init__(self) -> None:
        self.values: set[int] = set()
        self.count = 0
        self.duplicate = False
        self.overflow = False

    def add(self, first: str, second: str) -> None:
        self.count += 1
        if self.overflow:
            return
        digest = hash((first, second))
        if digest in self.values:
            self.duplicate = True
            return
        if len(self.values) >= MAX_PAIR_DISTINCT:
            self.overflow = True
            self.values.clear()
            return
        self.values.add(digest)


class _Profile:
    """Counters for one column: how many values were missing, how many different ones, how long the longest text was."""

    __slots__ = ("distinct", "folded", "longest", "missing", "overflow", "seen", "structured")

    def __init__(self) -> None:
        self.seen = 0
        self.missing = 0
        self.distinct: set[int] = set()
        #: The same values after folding case and spaces, so `distinct - folded` is how many are only a respelling.
        self.folded: set[int] = set()
        self.overflow = False
        self.longest = -1  # -1: no text seen.
        self.structured = False

    def add(self, value: object) -> None:
        self.seen += 1
        if value is None or value == "":
            self.missing += 1
            return
        if isinstance(value, (list, tuple, dict, set, frozenset)):
            self.structured = True
            return
        if isinstance(value, str) and len(value) > self.longest:
            self.longest = len(value)
        if not self.overflow:
            text = str(value)
            self.distinct.add(hash(text))
            self.folded.add(hash(" ".join(text.casefold().split()) if isinstance(value, str) else text))
            if len(self.distinct) >= MAX_PROFILE_DISTINCT:
                self.overflow = True
                self.distinct.clear()
                self.folded.clear()


def _distinct_phrase(profile: _Profile) -> str | None:
    if profile.structured or profile.seen == profile.missing:
        return None
    if profile.overflow:
        return "1,000 or more distinct values"
    count = len(profile.distinct)
    if count == 1:
        return "one value only"
    if count < 10:
        return "2 to 9 distinct values"
    if count < 100:
        return "10 to 99 distinct values"
    return "100 to 999 distinct values"


def _respelling_phrase(profile: _Profile) -> str | None:
    """Whether some values are the same word written two ways ("US", "us", " us"). Said only when it happens.

    The agent can't see the values, so it can't see that a column it means to group by is split. Folding case and
    spaces catches the common cases; it can't know that "USA" and "United States" are one place.
    """
    if profile.structured or profile.overflow or len(profile.distinct) < 2:
        return None
    extra = len(profile.distinct) - len(profile.folded)
    if extra <= 0:
        return None
    return "some values differ only by case or spacing" if extra * 3 < len(profile.distinct) else "many values differ only by case or spacing"


def _missing_phrase(profile: _Profile) -> str:
    share = profile.missing / profile.seen
    if profile.missing == 0:
        return "never missing"
    if share < 0.1:
        return "rarely missing"
    if share < 0.5:
        return "often missing"
    return "mostly missing"


def _length_phrase(profile: _Profile) -> str | None:
    if profile.longest < 0:
        return None
    if profile.longest < 10:
        return "text up to 9 characters"
    if profile.longest < 100:
        return "text up to 99 characters"
    if profile.longest < 1000:
        return "text up to 999 characters"
    return "text of 1,000 characters or more"


class TableEvidence:
    def __init__(self, keys: bool = True, profiles: bool = False) -> None:
        self.rows = 0
        #: Set when only the first rows were read. What holds for them may not hold for the rest, so nothing is claimed.
        self.partial = False
        self._keys = keys
        self._profiling = profiles
        self.columns: dict[str, _Column | None] = {}  # None: not a key-looking name.
        self.pairs: dict[tuple[str, str], _Pair] = {}
        self.profiles: dict[str, _Profile] = {}

    def add(self, record: Any) -> None:
        if not isinstance(record, dict):
            return
        self.rows += 1
        candidates: list[tuple[str, object]] = []
        for name, value in record.items():
            if not isinstance(name, str):
                continue
            if self._profiling:
                profile = self.profiles.get(name)
                if profile is None:
                    profile = self.profiles[name] = _Profile()
                profile.add(value)
            if not self._keys:
                continue
            if name not in self.columns:
                self.columns[name] = _Column() if is_key_name(name) else None
            column = self.columns[name]
            if column is not None:
                column.add(value)
                candidates.append((name, value))
        if self._keys and 2 <= len(candidates) <= MAX_PAIR_COLUMNS:
            self._add_pairs(candidates)

    def _add_pairs(self, candidates: list[tuple[str, object]]) -> None:
        texts = sorted((name, _key_text(value)) for name, value in candidates)
        for (first_name, first), (second_name, second) in combinations(texts, 2):
            if first is None or second is None:
                continue
            pair = self.pairs.get((first_name, second_name))
            if pair is None:
                pair = self.pairs[(first_name, second_name)] = _Pair()
            pair.add(first, second)

    def is_unique(self, name: str) -> bool:
        column = self.columns.get(name)
        return (
            not self.partial
            and column is not None
            and column.usable()
            and not column.duplicate
            and self.rows >= MIN_ROWS
            and column.non_null == self.rows
        )

    def can_reference(self, name: str) -> _Column | None:
        column = self.columns.get(name)
        if self.partial or column is None or not column.usable() or column.non_null < MIN_ROWS or len(column.values) < 2:
            return None
        return column

    def composite_unique(self) -> list[tuple[str, str]]:
        """Pairs of columns that are unique together though neither is alone."""
        if self.partial or self.rows < MIN_ROWS:
            return []
        found = []
        for (first, second), pair in self.pairs.items():
            first_column, second_column = self.columns.get(first), self.columns.get(second)
            if (
                first_column is None
                or second_column is None
                or pair.duplicate
                or pair.overflow
                or pair.count != self.rows
                or not first_column.usable()
                or not second_column.usable()
                or first_column.non_null != self.rows
                or second_column.non_null != self.rows
                or len(first_column.values) < 2
                or len(second_column.values) < 2
                or self.is_unique(first)
                or self.is_unique(second)
            ):
                continue
            found.append((first, second))
        return found

    def can_reference_pair(self, names: tuple[str, str]) -> _Pair | None:
        pair = self.pairs.get(names)
        if self.partial or pair is None or pair.overflow or pair.count < MIN_ROWS or len(pair.values) < 2:
            return None
        return pair

    def phrases(self, name: str) -> tuple[str, ...]:
        """Coarse words about one column, or nothing when too little was seen to say."""
        profile = self.profiles.get(name)
        if self.partial or profile is None or profile.seen < MIN_ROWS:
            return ()
        parts = (_distinct_phrase(profile), _missing_phrase(profile), _length_phrase(profile), _respelling_phrase(profile))
        return tuple(part for part in parts if part)


class Evidence:
    """The evidence of one `extract` call, keyed by the name each table ends up with."""

    def __init__(self, keys: bool = True, profiles: bool = False) -> None:
        #: Set by a folder reader to the file being read, so a table is named as the folder will name it.
        self.scope: str | None = None
        self.keys = keys
        self.profiles = profiles
        self.tables: dict[str | None, TableEvidence] = {}

    def table(self, name: str | None) -> TableEvidence:
        key = name if self.scope is None else (self.scope if name is None else f"{self.scope}{_SEPARATOR}{name}")
        if key not in self.tables:
            self.tables[key] = TableEvidence(self.keys, self.profiles)
        return self.tables[key]


def apply_evidence(schema: DataSchema, evidence: Evidence) -> DataSchema:
    """The schema with the hints the evidence supports: unique flags, composite keys, joins and column profiles."""
    entities = list(schema.entities)
    fields = [_fields_of(entity.node) for entity in entities]
    tables = [evidence.tables.get(entity.name) for entity in entities]

    unique: list[set[str]] = [set() for _ in entities]
    composite: list[list[tuple[str, str]]] = [[] for _ in entities]
    joins: list[Join] = []
    keys: list[list[ForeignKey]] = [list(entity.foreign_keys) for entity in entities]
    if evidence.keys:
        unique = [{name for name in fields[i] if table is not None and table.is_unique(name)} for i, table in enumerate(tables)]
        composite = [
            [pair for pair in table.composite_unique() if pair[0] in fields[i] and pair[1] in fields[i]] if table is not None else []
            for i, table in enumerate(tables)
        ]
        joins = _candidate_joins(entities, fields, tables, unique) + _candidate_pair_joins(fields, tables, composite)
        # Two tables that each hold the other's key under one name are a tie, not a join: neither is the parent.
        seen = set(joins)
        joins = [item for item in joins if (item[2], item[3], item[0], item[1]) not in seen]
        for child, names, parent, target in joins:
            keys[child].append(ForeignKey(names, entities[parent].name or "", target, inferred=True))

    profiles: list[dict[str, tuple[str, ...]]] = [{} for _ in entities]
    if evidence.profiles:
        for i, table in enumerate(tables):
            if table is not None:
                profiles[i] = {name: phrases for name in fields[i] if (phrases := table.phrases(name))}

    flagged = sum(len(columns) for columns in unique)
    pairs = sum(len(items) for items in composite)
    profiled = sum(len(items) for items in profiles)
    if not (flagged or pairs or joins or profiled):
        return schema
    result = [
        replace(
            entity,
            node=_decorate(entity.node, unique[i], profiles[i]),
            foreign_keys=tuple(keys[i]),
            inferred_unique_keys=tuple(composite[i]),
        )
        for i, entity in enumerate(entities)
    ]
    notes = list(schema.notes)
    if flagged or pairs or joins:
        extra = f", {pairs} composite unique key(s)" if pairs else ""
        notes.append(
            f"inferred {flagged} unique column(s){extra} and {len(joins)} join(s) from the values; "
            "they are guesses, and nothing enforces them"
        )
    if profiled:
        notes.append("column hints are ranges taken from the values; they describe the data as it was read")
    return DataSchema(schema.source_kind, tuple(result), tuple(notes))


def _fields_of(node: Node) -> dict[str, Any]:
    node = node.items if isinstance(node, Array) else node
    return dict(node.fields) if isinstance(node, Object) else {}


def _decorate(node: Node, unique: set[str], profiles: dict[str, tuple[str, ...]]) -> Node:
    if not unique and not profiles:
        return node
    if isinstance(node, Array):
        return Array(_decorate(node.items, unique, profiles))
    if isinstance(node, Object):
        marked: dict[str, Field] = {}
        for name, item in node.fields.items():
            if name in unique:
                item = replace(item, unique=True)
            if name in profiles:
                item = replace(item, profile=profiles[name])
            marked[name] = item
        return Object(marked, node.extra, node.key_format)
    return node


def _base(name: str | None) -> str:
    """`customers` from `data/customers.csv` or `book.xlsx#customers`."""
    if not name:
        return ""
    part = name.rsplit(_SEPARATOR, 1)[-1]
    return os.path.splitext(part.rsplit("/", 1)[-1])[0]


def _singular(word: str) -> str:
    lowered = word.lower()
    if lowered.endswith("ies") and len(lowered) > 3:
        return lowered[:-3] + "y"
    if lowered.endswith("ses") and len(lowered) > 3:
        return lowered[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss") and len(lowered) > 1:
        return lowered[:-1]
    return lowered


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_match(child_column: str, parent_table: str, parent_column: str) -> bool:
    """Whether the child's column name points at the parent: `user_id` with `users.id`, or `ref_id` with `ref_id`."""
    child, target = _norm(child_column), _norm(parent_column)
    if child == target and target != "id":
        return True  # The same specific name in both tables.
    return bool(parent_table) and child in {_norm(_singular(parent_table)) + target, _norm(parent_table) + target}


def _candidate_joins(entities, fields, tables, unique) -> list[Join]:
    """A join for every column that looks like a reference to another table's unique column."""
    found: list[Join] = []
    for child, table in enumerate(tables):
        if table is None:
            continue
        declared = {column for key in entities[child].foreign_keys for column in key.columns}
        for name in fields[child]:
            column = None if name in declared else table.can_reference(name)
            if column is None:
                continue
            for parent, parent_table in enumerate(tables):
                if parent == child or parent_table is None:
                    continue
                target = next(
                    (
                        candidate
                        for candidate in sorted(unique[parent])
                        if parent_table.columns[candidate] is not None
                        and _name_match(name, _base(entities[parent].name), candidate)
                        and column.values <= parent_table.columns[candidate].values
                    ),
                    None,
                )
                if target is not None:
                    found.append((child, (name,), parent, (target,)))
                    break
    return found


def _candidate_pair_joins(fields, tables, composite) -> list[Join]:
    """Joins on a composite key: the child holds the same two columns, and every pair of values is in the parent."""
    found: list[Join] = []
    for child, table in enumerate(tables):
        if table is None:
            continue
        for names in table.pairs:
            if names[0] not in fields[child] or names[1] not in fields[child]:
                continue
            own = table.can_reference_pair(names)
            if own is None:
                continue
            for parent, parent_table in enumerate(tables):
                if parent == child or parent_table is None or names not in composite[parent]:
                    continue
                theirs = parent_table.pairs.get(names)
                if theirs is not None and own.values <= theirs.values:
                    found.append((child, names, parent, names))
                    break
    return found
