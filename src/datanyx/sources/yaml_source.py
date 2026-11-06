"""YAML files, read with the safe loader so that no tag can run code."""

from __future__ import annotations

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..scale import scale_for
from ..schema import DataSchema
from .base import Source, SourceError
from .files import extension, items_seen, missing_extra, open_text, records_observation, reject_namespace, unnamed

_UNSET = object()
# Aliases let a few hundred bytes describe more values than could ever be walked.
MAX_EXPANDED_VALUES = 10_000_000


def _check_expansion(document: object) -> object:
    """Count what the document expands to, visiting each shared value once, and refuse a bomb."""
    sizes: dict[int, int] = {}
    in_progress: set[int] = set()

    def size(value: object) -> int:
        if not isinstance(value, (list, tuple, dict)):
            return 1
        key = id(value)
        if key in sizes:
            return sizes[key]
        if key in in_progress:
            raise SourceError("input is too deeply nested")
        in_progress.add(key)
        children = value.values() if isinstance(value, dict) else value
        total = 1
        for child in children:
            total += size(child)
            if total > MAX_EXPANDED_VALUES:
                raise SourceError("the YAML's aliases expand to too many values")
        in_progress.discard(key)
        sizes[key] = total
        return total

    size(document)
    return document


class YamlSource(Source):
    kind = "yaml"

    def matches(self, target: str) -> bool:
        return extension(target) in (".yaml", ".yml")

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        try:
            import yaml
        except ImportError:
            raise missing_extra("YAML", "yaml") from None
        # The C loader is much faster when libyaml is present, and equally safe.
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        with open_text(target) as stream:
            try:
                documents = yaml.load_all(stream, Loader=loader)
                first = _check_expansion(next(documents, _UNSET))
                second = _check_expansion(next(documents, _UNSET)) if first is not _UNSET else _UNSET
                if second is _UNSET:
                    observation = Observation()
                    if first is not _UNSET:
                        observation.observe(first)
                else:
                    # Several documents are modelled as an array, like JSON Lines.
                    observation = records_observation()
                    observation.observe_item(first)
                    observation.observe_item(second)
                    for document in documents:
                        observation.observe_item(_check_expansion(document))
            except yaml.YAMLError as error:
                mark = getattr(error, "problem_mark", None)
                where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
                # Built by hand: PyYAML's message quotes the text it choked on.
                raise SourceError(f"invalid YAML{where}") from None
            except UnicodeDecodeError:
                raise SourceError("input is not valid UTF-8") from None
            except ValueError:  # A date that doesn't exist, for one. Its own text can quote the file.
                raise SourceError("invalid YAML: a value in it can't be read") from None
        node, notes = infer(observation, policy)
        return unnamed(self.kind, node, notes, scale_for(policy, items_seen(observation)))
