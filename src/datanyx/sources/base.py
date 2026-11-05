"""The interface every data source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..privacy import KeyPolicy
from ..schema import DataSchema


class SourceError(Exception):
    """A source couldn't be read. The message is shown to whoever runs the tool,
    which may be the agent, so it must never quote the data."""


class Source(ABC):
    """Reads something and describes its structure without exposing its contents."""

    #: Name used with `--source`, and recorded on the resulting schema.
    kind: str

    @abstractmethod
    def matches(self, target: str) -> bool:
        """Whether this source handles `target` when none was named explicitly.

        `target` is whatever the user passed: a file path, and in future a
        connection string or similar.
        """

    @abstractmethod
    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        """Build the schema for `target`, raising SourceError if it can't be read.

        `namespace` selects a part of the source, such as a database schema. Sources
        with no such concept reject it, rather than silently describing everything.
        """
