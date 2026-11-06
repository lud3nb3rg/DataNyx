"""MongoDB, described from a sample of each collection's documents.

Unlike a SQL database, MongoDB has no catalog of fields: the only way to learn a collection's shape is
to look at documents. So this source reads up to `SAMPLE_SIZE` documents per collection into memory,
records their types, and discards them. Nothing but the structure leaves this module, the same as for a
JSON file. Because it does read documents, connect with a role that can read only what you are willing
to have sampled, and keep the URL (it holds the password) in the environment: `env:NAME`.

A field that appears in only a few documents may be missed, which the schema's notes say.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from ..inference import infer
from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import DataSchema, Entity
from .base import Source, SourceError
from .files import EMPTY_TABLE, missing_extra, records_observation
from .sql_source import _resolve_env, is_mongodb_url

SAMPLE_SIZE = 500
MAX_COLLECTIONS = 100
_SCHEMES = ("mongodb://", "mongodb+srv://")
_TIMEOUT_MS = 10_000


class MongoDbSource(Source):
    kind = "mongodb"

    def matches(self, target: str) -> bool:
        return is_mongodb_url(target)

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        url = _resolve_env(target)
        if not url.startswith(_SCHEMES):
            raise SourceError("a MongoDB URL starts with mongodb:// or mongodb+srv://")
        try:
            import pymongo.errors as errors
        except ImportError:
            raise missing_extra("MongoDB", "mongodb") from None
        # Only fixed messages and exception type names leave this method. The driver's messages can
        # quote the URL, which holds the password, or the data.
        try:
            client = _client(url)
        except errors.ConfigurationError:
            raise SourceError("could not parse the MongoDB URL") from None
        except Exception as error:
            raise SourceError(f"could not connect to MongoDB ({type(error).__name__})") from None
        try:
            return _describe(client, namespace, policy, self.kind)
        except SourceError:
            raise
        except errors.ServerSelectionTimeoutError:
            raise SourceError("could not reach the MongoDB server") from None
        except errors.OperationFailure:
            raise SourceError("MongoDB refused the request; check the credentials and what the role may read") from None
        except errors.ConfigurationError:
            raise SourceError("the URL names no database; add one to it or pass --schema") from None
        except Exception as error:
            raise SourceError(f"could not read the database ({type(error).__name__})") from None
        finally:
            client.close()


def _client(url: str):
    """The driver's client. A function of its own so tests can stand in for a server."""
    import pymongo

    return pymongo.MongoClient(
        url,
        serverSelectionTimeoutMS=_TIMEOUT_MS,
        connectTimeoutMS=_TIMEOUT_MS,
        appname="datanyx",
    )


def _describe(client, namespace: str | None, policy: KeyPolicy, kind: str) -> DataSchema:
    database = client[namespace] if namespace else client.get_default_database()
    names = sorted(name for name in database.list_collection_names() if not name.startswith("system."))
    notes: list[str] = []
    if not names:
        notes.append("no collections found")
    visible = [name for name in names if policy.shows_name(name)]
    if len(visible) != len(names):
        notes.append("hid data-like collection names (key shape)")
    if len(visible) > MAX_COLLECTIONS:
        notes.append(f"stopped after {MAX_COLLECTIONS} collections; name a smaller database")
        visible = visible[:MAX_COLLECTIONS]
    entities = []
    for name in visible:
        scale = scale_for(policy, _estimated_count(database[name]) if policy.scale_hints else None)
        root = records_observation()
        sampled = 0
        for document in database[name].find({}, limit=SAMPLE_SIZE):
            root.observe_item(_plain(document))
            sampled += 1
        if not sampled:
            entities.append(Entity(name, EMPTY_TABLE, scale=scale))
            continue
        node, inferred = infer(root, policy, f"$.{name}")
        notes.extend(inferred)
        entities.append(Entity(name, node, scale=scale))
    if entities:
        notes.append(f"inferred from a sample of up to {SAMPLE_SIZE} documents per collection; rare fields may be missing")
    return DataSchema(kind, tuple(entities), tuple(notes))


def _estimated_count(collection) -> int | None:
    """The collection's size from its metadata, which doesn't scan it."""
    try:
        return int(collection.estimated_document_count())
    except Exception:
        return None


def _plain(value):
    """A BSON value as the plain Python value the inference understands: a type, never its content."""
    if value is None or isinstance(value, (bool, int, float, str, datetime, date, time, bytes, bytearray, Decimal)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    name = type(value).__name__
    if name == "Decimal128":
        return 0.0
    if name == "ObjectId":
        return "0" * 24  # An id: text, whose content stays out of the format check.
    return ""  # Regex, Timestamp, Code, DBRef and the like: text of unknown shape.
