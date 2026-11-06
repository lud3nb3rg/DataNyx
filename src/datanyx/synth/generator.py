"""Generate fake data that matches a schema.

The only input is a `DataSchema`, which has no values in it, and every value comes
from a random generator or a fixed word list. Nothing real can reach the output, and
names the schema hid stay hidden: hidden keys are replaced by invented ones.

Rows are reproducible for a given seed. Primary keys are unique and foreign keys point
at rows that exist, so a synthetic database holds together.
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from ..schema import (
    Array,
    Boolean,
    DataSchema,
    Entity,
    Integer,
    Node,
    Null,
    Number,
    Object,
    String,
    Union,
    Unknown,
)

MAX_ROWS = 1_000_000
_OPTIONAL_PRESENT = 0.7
_NULL_CHANCE = 0.2

_FIRST = ["Ana", "Omar", "Li", "Sofia", "Noah", "Amara", "Jonas", "Mei", "Lucas", "Priya", "Elena", "Tomas"]
_LAST = ["Silva", "Haddad", "Chen", "Rossi", "Okafor", "Novak", "Larsen", "Tanaka", "Moreau", "Singh", "Duarte"]
_CITIES = ["Lisbon", "Osaka", "Leeds", "Austin", "Porto", "Lyon", "Turin", "Quito", "Bergen", "Kyoto", "Cork"]
_COUNTRIES = ["Portugal", "Japan", "Canada", "Brazil", "Norway", "Kenya", "Chile", "Poland", "Vietnam"]
_COMPANIES = ["Acme Labs", "Northwind Trading", "Globex", "Initech Works", "Umbra Foods", "Blue Heron Co"]
_STREETS = ["Maple Street", "Harbor Road", "Station Avenue", "Orchard Lane", "Mill Court", "Cedar Way"]
_STATUSES = ["active", "inactive", "pending", "archived"]
_CURRENCIES = ["EUR", "USD", "GBP", "JPY"]
_WORDS = ["amber", "basil", "cobalt", "delta", "ember", "fjord", "garnet", "harbor", "indigo", "juniper", "kestrel", "lantern", "meadow", "nectar", "onyx", "pebble", "quartz", "ripple", "saffron", "timber", "umber", "velvet", "willow", "yarrow", "zephyr"]

_SPLIT_WORDS = re.compile(r"[a-z]+")
_CAMEL = re.compile(r"([a-z])([A-Z])")


@dataclass
class Table:
    """The generated rows for one entity."""

    name: str | None
    entity: Entity
    rows: list[Any]
    #: True when the entity is a single document rather than a list of rows.
    document: bool = False


def synthesize(schema: DataSchema, rows: int = 100, seed: int | None = 0) -> list[Table]:
    """Fake data for every entity in `schema`: `rows` rows each, or one document.

    `seed` makes the result repeatable; pass None for a different result every time.
    """
    if not 0 <= rows <= MAX_ROWS:
        raise ValueError(f"rows must be between 0 and {MAX_ROWS}")
    generator = _Generator(random.Random(seed), rows)
    by_name = {entity.name: entity for entity in schema.entities}
    tables: dict[str | None, Table] = {}
    for entity in _in_dependency_order(schema.entities, by_name):
        tables[entity.name] = generator.table(entity, tables)
    return [tables[entity.name] for entity in schema.entities]


def _in_dependency_order(entities: tuple[Entity, ...], by_name: dict[str | None, Entity]) -> list[Entity]:
    """Referenced entities first, so foreign keys have rows to point at."""
    ordered: list[Entity] = []
    state: dict[str | None, int] = {}  # 1 while being visited, 2 once placed

    def visit(entity: Entity) -> None:
        if state.get(entity.name):
            return  # Done, or part of a cycle: its keys fall back to plain values.
        state[entity.name] = 1
        for key in entity.foreign_keys:
            target = by_name.get(key.referenced_entity)
            if target is not None:
                visit(target)
        state[entity.name] = 2
        ordered.append(entity)

    for entity in entities:
        visit(entity)
    return ordered


class _Generator:
    def __init__(self, rng: random.Random, rows: int) -> None:
        self.rng = rng
        self.rows = rows
        # A lone document can't show both an empty and a filled list, so it gets items.
        self.min_items = 0

    # --- entities ----------------------------------------------------------

    def table(self, entity: Entity, done: dict[str | None, Table]) -> Table:
        node = entity.node
        if isinstance(node, Array):
            item, document = node.items, False
        elif entity.is_table:
            item, document = node, False
        else:
            item, document = node, True
        count = 1 if document else self.rows
        self.min_items = 1 if document else 0

        if not isinstance(item, Object) or document:
            return Table(entity.name, entity, [self.value(item, entity.name or "value") for _ in range(count)], document)

        fixed = self._key_columns(entity, item, count, done)
        rows = [self.object(item, {name: values[i] for name, values in fixed.items()}) for i in range(count)]
        return Table(entity.name, entity, _unique_by_key(rows, entity, item), False)

    def _key_columns(
        self, entity: Entity, item: Object, count: int, done: dict[str | None, Table]
    ) -> dict[str, list[Any]]:
        """Values for primary key and foreign key columns, one per row."""
        columns: dict[str, list[Any]] = {}
        primary = [name for name in entity.primary_key if name in item.fields]
        if not primary and isinstance(item.fields.get("id") and item.fields["id"].node, Integer):
            primary = ["id"]  # A plain `id` column is nearly always a counter.
        for position, name in enumerate(primary):
            last = position == len(primary) - 1
            node = item.fields[name].node
            if last:
                columns[name] = self._unique(node, name, count)
            else:
                columns[name] = [self.value(node, name) for _ in range(count)]

        for name, field in item.fields.items():
            if field.unique and name not in columns:  # Found unique by `--infer-keys`: other tables may point at it.
                columns[name] = self._unique(field.node, name, count)

        for key in entity.foreign_keys:
            target = done.get(key.referenced_entity)
            if target is None or not target.rows or not all(c in item.fields for c in key.columns):
                continue  # Not generated yet (a cycle), or nothing to point at.
            picks = [self.rng.choice(target.rows) for _ in range(count)]
            if not all(isinstance(pick, dict) and all(c in pick for c in key.referenced_columns) for pick in picks):
                continue
            for source, referenced in zip(key.columns, key.referenced_columns):
                columns[source] = [pick[referenced] for pick in picks]
        return columns

    def _unique(self, node: Node, name: str, count: int) -> list[Any]:
        options = [o for o in (node.options if isinstance(node, Union) else (node,)) if not isinstance(o, Null)]
        base = options[0] if options else Unknown()
        if isinstance(base, (Integer, Number)):
            return list(range(1, count + 1))
        if isinstance(base, String) and base.format == "uuid":
            return [str(uuid.UUID(int=self.rng.getrandbits(128), version=4)) for _ in range(count)]
        prefix = (re.sub(r"\W", "", name)[:3] or "id").upper()
        return [f"{prefix}{i:05d}" for i in range(1, count + 1)]

    # --- values ------------------------------------------------------------

    def object(self, node: Object, fixed: dict[str, Any] | None = None) -> dict[str, Any]:
        fixed = fixed or {}
        result: dict[str, Any] = {}
        for name, field in node.fields.items():
            if name in fixed:
                result[name] = fixed[name]
            elif field.required or self.rng.random() < _OPTIONAL_PRESENT:
                result[name] = self.value(field.node, name)
        if node.extra is not None:
            for _ in range(self.rng.randint(1, 3)):
                result[self._extra_key(node.key_format)] = self.value(node.extra, "value")
        return result

    def value(self, node: Node, hint: str) -> Any:
        rng = self.rng
        if isinstance(node, (Unknown, Null)):
            return None
        if isinstance(node, Boolean):
            return rng.random() < 0.5
        if isinstance(node, Integer):
            return self._integer(hint)
        if isinstance(node, Number):
            return self._number(hint)
        if isinstance(node, String):
            return self._string(node.format, hint)
        if isinstance(node, Array):
            if isinstance(node.items, Unknown):
                return []
            return [self.value(node.items, hint) for _ in range(rng.randint(self.min_items, 3))]
        if isinstance(node, Object):
            return self.object(node)
        if isinstance(node, Union):
            nulls = [o for o in node.options if isinstance(o, Null)]
            others = [o for o in node.options if not isinstance(o, Null)]
            if nulls and (not others or rng.random() < _NULL_CHANCE):
                return None
            return self.value(rng.choice(others), hint)
        raise TypeError(f"unhandled node: {type(node).__name__}")

    def _integer(self, hint: str) -> int:
        words = _words(hint)
        low, high = 0, 1000
        if "age" in words:
            low, high = 18, 90
        elif "year" in words:
            low, high = 1990, 2025
        elif "month" in words:
            low, high = 1, 12
        elif "day" in words:
            low, high = 1, 28
        elif words & {"count", "qty", "quantity", "num", "number", "items"}:
            low, high = 0, 20
        elif words & {"percent", "pct"}:
            low, high = 0, 100
        elif "id" in words:
            low, high = 1, 10000
        return self.rng.randint(low, high)

    def _number(self, hint: str) -> float:
        words = _words(hint)
        low, high, places = 0.0, 1000.0, 2
        if words & {"price", "amount", "total", "balance", "cost", "salary", "revenue", "fee"}:
            low, high = 1.0, 1000.0
        elif words & {"lat", "latitude"}:
            low, high = -90.0, 90.0
        elif words & {"lon", "lng", "longitude"}:
            low, high = -180.0, 180.0
        elif words & {"rate", "ratio", "score", "probability"}:
            low, high, places = 0.0, 1.0, 3
        # Always a fraction, so a column of these is read back as numbers and not integers.
        value = round(self.rng.uniform(low, high), places)
        return value if value != int(value) else value + 10 ** -places

    def _string(self, format: str | None, hint: str) -> str:
        rng = self.rng
        if format == "email":
            return f"{rng.choice(_FIRST).lower()}.{rng.choice(_LAST).lower()}{rng.randint(1, 99)}@example.com"
        if format == "uuid":
            return str(uuid.UUID(int=rng.getrandbits(128), version=4))
        if format == "uri":
            return f"https://example.com/{rng.choice(_WORDS)}/{rng.randint(1, 999)}"
        if format == "date":
            return self._date().isoformat()
        if format == "date-time":
            moment = datetime.combine(self._date(), time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)))
            return moment.isoformat() + "Z"
        if format == "time":
            return time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)).isoformat()
        if format == "ipv4":
            return f"192.0.2.{rng.randint(1, 254)}"  # A range reserved for documentation.
        if format == "ipv6":
            return f"2001:db8::{rng.randint(1, 0xFFFF):x}"
        if format == "binary":
            return "".join(rng.choice("0123456789abcdef") for _ in range(16))
        return self._text(hint)

    def _text(self, hint: str) -> str:
        rng = self.rng
        words = _words(hint)
        if words & {"first", "given", "forename"}:
            return rng.choice(_FIRST)
        if words & {"last", "surname", "family"}:
            return rng.choice(_LAST)
        if "name" in words and not words & {"file", "table", "column", "host", "user", "key"}:
            return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        if "city" in words or "town" in words:
            return rng.choice(_CITIES)
        if "country" in words:
            return rng.choice(_COUNTRIES)
        if words & {"company", "organization", "organisation", "employer", "org"}:
            return rng.choice(_COMPANIES)
        if words & {"street", "address"}:
            return f"{rng.randint(1, 250)} {rng.choice(_STREETS)}"
        if words & {"phone", "tel", "mobile", "telephone"}:
            return f"+1-555-01{rng.randint(0, 99):02d}"  # 555-01xx is reserved for fiction.
        if words & {"zip", "postal", "postcode"}:
            return f"0{rng.randint(1000, 9999)}"  # A leading zero keeps it from reading as a number.
        if "status" in words or "state" in words:
            return rng.choice(_STATUSES)
        if words & {"currency"}:
            return rng.choice(_CURRENCIES)
        if words & {"description", "notes", "note", "comment", "text", "title", "message", "summary", "body"}:
            return " ".join(rng.choice(_WORDS) for _ in range(rng.randint(3, 8))).capitalize() + "."
        return " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 2)))

    def _date(self) -> date:
        return date(2020, 1, 1) + timedelta(days=self.rng.randint(0, 2190))

    def _extra_key(self, key_format: str | None) -> str:
        rng = self.rng
        if key_format == "uuid":
            return str(uuid.UUID(int=rng.getrandbits(128), version=4))
        if key_format == "email":
            return f"{rng.choice(_FIRST).lower()}{rng.randint(100, 999)}@example.com"
        if key_format == "date":
            return self._date().isoformat()
        if key_format == "uri":
            return f"https://example.com/{rng.choice(_WORDS)}{rng.randint(1, 999)}"
        return f"key {rng.choice(_WORDS)}{rng.randint(1, 9999)}"


def _unique_by_key(rows: list[dict[str, Any]], entity: Entity, item: Object) -> list[dict[str, Any]]:
    """Drop rows that repeat a primary key. Foreign key columns inside the key can collide."""
    key = [name for name in entity.primary_key if name in item.fields]
    if not key or len(key) != len(entity.primary_key):
        return rows
    seen: set[tuple[Any, ...]] = set()
    unique = []
    for row in rows:
        marker = tuple(row.get(name) for name in key)
        if marker not in seen:
            seen.add(marker)
            unique.append(row)
    return unique


def _words(name: str) -> set[str]:
    """The lowercase words in a field name: `shipCity` and `ship_city` both give {ship, city}."""
    return set(_SPLIT_WORDS.findall(_CAMEL.sub(r"\1 \2", name).lower()))
