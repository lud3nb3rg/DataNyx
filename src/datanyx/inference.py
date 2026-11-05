"""Infer a schema from sample values.

This runs in two steps so that values can't reach the output:

1. `Observation.observe` records what each value looks like and discards it.
   Scalars are reduced to a type and format label. Raw keys are kept in memory
   because telling structure from data requires seeing all of them together.
2. `Observation.to_node` applies the `KeyPolicy` and emits schema nodes. Only
   keys the policy accepts make it into the result.

Any source whose data arrives as values (documents, API responses, CSV rows)
can reuse this. Sources with a catalog, such as databases, can build schema
nodes directly from it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .formats import detect_format
from .privacy import KeyPolicy, looks_like_data
from .schema import (
    Array,
    Boolean,
    Field,
    Integer,
    Node,
    Null,
    Number,
    Object,
    String,
    Union,
    Unknown,
)

_MAP_VALUE = ".{*}"
_ARRAY_ITEM = "[]"


class Observation:
    """What has been seen at one position in the data. Holds no values."""

    __slots__ = (
        "arrays",
        "booleans",
        "fields",
        "floats",
        "integers",
        "items",
        "nulls",
        "objects",
        "seen",
        "string_formats",
        "strings",
    )

    def __init__(self) -> None:
        self.seen = 0
        self.nulls = 0
        self.booleans = 0
        self.integers = 0
        self.floats = 0
        self.strings = 0
        self.string_formats: set[str | None] = set()
        self.arrays = 0
        self.items: Observation | None = None
        self.objects = 0
        self.fields: dict[str, Observation] = {}

    def observe(self, value: object) -> None:
        self.seen += 1
        if value is None:
            self.nulls += 1
        elif isinstance(value, bool):  # Must precede int: bool subclasses int.
            self.booleans += 1
        elif isinstance(value, int):
            self.integers += 1
        elif isinstance(value, float):
            self.floats += 1
        elif isinstance(value, str):
            self.strings += 1
            self.string_formats.add(detect_format(value))
        elif isinstance(value, (list, tuple, set, frozenset)):
            self.arrays += 1
            for item in value:
                self.observe_item(item)
        elif isinstance(value, dict):
            self.objects += 1
            for key, child in value.items():
                # YAML allows keys that aren't strings; they are named by their text.
                self.fields.setdefault(key if isinstance(key, str) else str(key), Observation()).observe(child)
        elif isinstance(value, (datetime, date, time)):  # datetime subclasses date.
            self.strings += 1
            label = "date-time" if isinstance(value, datetime) else "date" if isinstance(value, date) else "time"
            self.string_formats.add(label)
        elif isinstance(value, (Decimal, timedelta)):
            self.floats += 1
        elif isinstance(value, (bytes, bytearray)):
            self.strings += 1
            self.string_formats.add("binary")
        else:
            raise TypeError(f"unsupported value type: {type(value).__name__}")

    def observe_item(self, value: object) -> None:
        if self.items is None:
            self.items = Observation()
        self.items.observe(value)

    def merge(self, other: Observation) -> None:
        self.seen += other.seen
        self.nulls += other.nulls
        self.booleans += other.booleans
        self.integers += other.integers
        self.floats += other.floats
        self.strings += other.strings
        self.string_formats |= other.string_formats
        self.arrays += other.arrays
        if other.items is not None:
            if self.items is None:
                self.items = Observation()
            self.items.merge(other.items)
        self.objects += other.objects
        for key, child in other.fields.items():
            self.fields.setdefault(key, Observation()).merge(child)

    def kinds(self) -> frozenset[str]:
        """The non-null JSON kinds seen here."""
        counts = {
            "boolean": self.booleans,
            "number": self.integers + self.floats,
            "string": self.strings,
            "array": self.arrays,
            "object": self.objects,
        }
        return frozenset(kind for kind, count in counts.items() if count)

    def to_node(self, policy: KeyPolicy, path: str, notes: list[str]) -> Node:
        options: list[Node] = []
        if self.objects:
            options.append(self._object_node(policy, path, notes))
        if self.arrays:
            items = (
                self.items.to_node(policy, path + _ARRAY_ITEM, notes)
                if self.items is not None
                else Unknown()
            )
            options.append(Array(items))
        if self.strings:
            only_format = (
                next(iter(self.string_formats))
                if len(self.string_formats) == 1
                else None
            )
            options.append(String(only_format))
        if self.floats:
            options.append(Number())  # Number covers integers seen at this path too.
        elif self.integers:
            options.append(Integer())
        if self.booleans:
            options.append(Boolean())
        if self.nulls:
            options.append(Null())

        if not options:
            return Unknown()
        return options[0] if len(options) == 1 else Union(tuple(options))

    def _object_node(self, policy: KeyPolicy, path: str, notes: list[str]) -> Object:
        hidden, reason = _select_hidden_keys(self, policy, path)
        if hidden:
            notes.append(f"hid data-like keys at {path} ({reason})")

        fields = {}
        for key, child in self.fields.items():
            if key not in hidden:
                required = child.seen == self.objects
                child_path = f"{path}.{key}"
                fields[key] = Field(child.to_node(policy, child_path, notes), required)

        if not hidden:
            return Object(fields)

        merged = Observation()
        for key in hidden:
            merged.merge(self.fields[key])
        extra = merged.to_node(policy, path + _MAP_VALUE, notes)
        key_formats = {detect_format(key) for key in hidden}
        key_format = key_formats.pop() if len(key_formats) == 1 else None
        return Object(fields, extra, key_format)


def infer(observation: Observation, policy: KeyPolicy, path: str = "$") -> tuple[Node, tuple[str, ...]]:
    """Convert an observation into a schema node plus notes that are safe to show."""
    notes: list[str] = []
    node = observation.to_node(policy, path, notes)
    return node, tuple(notes)


def _select_hidden_keys(obs: Observation, policy: KeyPolicy, path: str) -> tuple[set[str], str]:
    """Return the keys to hide at this object and a key-free reason for doing so."""
    if path in policy.map_paths:
        return set(obs.fields), "configured map path"

    hidden: set[str] = set()
    reasons: list[str] = []

    data_like = _data_like_keys(obs)
    if data_like:
        hidden |= data_like
        reasons.append("key shape")

    rare = _rare_keys(obs, policy, hidden)
    if rare:
        hidden |= rare
        reasons.append("rarely repeated")

    remaining = set(obs.fields) - hidden
    if _is_high_cardinality_map(obs, remaining, policy):
        hidden |= remaining
        reasons.append("many same-shaped entries")

    return hidden - policy.keep_names, ", ".join(reasons)


def _data_like_keys(obs: Observation) -> set[str]:
    data_like = {key for key in obs.fields if looks_like_data(key)}
    if len(data_like) * 2 >= len(obs.fields) and data_like:
        # Mostly data keys: this is a map, so a plain-looking key holding the
        # same kind of value is probably data too ("bob" beside "al@example.com").
        kinds = {obs.fields[key].kinds() for key in data_like}
        data_like |= {key for key, child in obs.fields.items() if child.kinds() in kinds}
    return data_like


def _rare_keys(obs: Observation, policy: KeyPolicy, already_hidden: set[str]) -> set[str]:
    if obs.objects < policy.min_instances_for_support:
        return set()
    candidates = set(obs.fields) - already_hidden
    rare = {key for key in candidates if obs.fields[key].seen < policy.min_key_support}
    if rare and len(rare) * 2 >= len(candidates):
        # Most keys don't repeat, so these objects have per-record keys. A key
        # that happened to repeat a few times is more likely data than structure.
        rare |= {key for key in candidates if obs.fields[key].seen * 2 < obs.objects}
    return rare


def _is_high_cardinality_map(obs: Observation, keys: set[str], policy: KeyPolicy) -> bool:
    if not keys:
        return False
    # Keys recurring across many objects are structure, however numerous they are.
    if obs.objects >= 2:
        recurring = sum(1 for key in keys if obs.fields[key].seen * 2 >= obs.objects)
        if recurring * 2 >= len(keys):
            return False

    children = [obs.fields[key] for key in keys]
    kinds = {child.kinds() for child in children}
    if len(kinds) != 1:
        return False
    kind = next(iter(kinds))
    if kind == {"object"}:
        return len(keys) >= policy.map_threshold_objects and _share_shape(children)
    if len(kind) == 1 and not kind & {"array", "object"}:
        return len(keys) >= policy.map_threshold_scalars
    return False


def _share_shape(children: list[Observation]) -> bool:
    """True when the objects have at least half of their keys in common."""
    key_sets = [set(child.fields) for child in children]
    union = set().union(*key_sets)
    if not union:
        return True
    return len(set.intersection(*key_sets)) * 2 >= len(union)
