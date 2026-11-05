"""Other ways of finding sensitive text, used as a second pass beside the built-in detectors.

Each is a `Detector`, so what it finds is merged with everything else and any of them can be
combined. Choose them with `--backend`:

- `presidio[:LANGUAGE[:MODEL]]`: Microsoft Presidio, which adds recognisers for many national
  ids and formats. Needs `datanyx[presidio]` and a spaCy model for the language.
- `gliner[:MODEL]`: a GLiNER model for personal data, good on names the spaCy models miss and
  in languages they don't cover. Needs the `gliner` package; the model is downloaded once from
  Hugging Face, and the text itself never leaves the machine.
- `ollama[:MODEL[@URL]]`: a language model running on this computer through Ollama, asked to list
  what is sensitive. **Only a server on this computer is accepted**: sending sensitive text to
  a remote model would defeat the purpose.

Nothing here is a guarantee. A model can miss things, and a language model can also invent them,
so measure with `datanyx eval` before relying on a backend.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Iterator
from typing import Any

from ..sources.base import SourceError
from .detectors import (
    ADDRESS,
    CREDIT_CARD,
    DATE,
    EMAIL,
    GROUP,
    IBAN,
    IP_ADDRESS,
    LOCATION,
    MISC,
    ORG,
    PERSON,
    PHONE,
    URL,
    Detector,
    Span,
    _split_chunks,
)

DEFAULT_GLINER_MODEL = "urchade/gliner_multi_pii-v1"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}

# What each backend calls things, in this project's terms. Anything not listed becomes MISC.
_PRESIDIO_KINDS = {
    "PERSON": PERSON,
    "EMAIL_ADDRESS": EMAIL,
    "PHONE_NUMBER": PHONE,
    "CREDIT_CARD": CREDIT_CARD,
    "IBAN_CODE": IBAN,
    "IP_ADDRESS": IP_ADDRESS,
    "LOCATION": LOCATION,
    "DATE_TIME": DATE,
    "NRP": GROUP,
    "URL": URL,
    "ORGANIZATION": ORG,
    "US_SSN": "SSN",
    "CRYPTO": "CRYPTO_WALLET",
    "US_BANK_NUMBER": "ACCOUNT_NUMBER",
}
_GLINER_LABELS = {
    "person": PERSON,
    "organization": ORG,
    "location": LOCATION,
    "address": ADDRESS,
    "email": EMAIL,
    "phone number": PHONE,
    "date of birth": DATE,
    "credit card number": CREDIT_CARD,
    "passport number": "ID",
    "social security number": "SSN",
    "bank account number": "ACCOUNT_NUMBER",
    "ip address": IP_ADDRESS,
    "username": "ID",
}
_OLLAMA_KINDS = {
    "PERSON": PERSON, "ORG": ORG, "ORGANIZATION": ORG, "LOCATION": LOCATION, "ADDRESS": ADDRESS, "EMAIL": EMAIL,
    "PHONE": PHONE, "DATE": DATE, "ID": "ID", "URL": URL, "IP_ADDRESS": IP_ADDRESS,
}


class PresidioDetector:
    """Presidio's recognisers, run on a spaCy model for the language."""

    def __init__(self, language: str = "en", model: str | None = None, threshold: float = 0.4) -> None:
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
        except ImportError:
            raise SourceError("the Presidio backend needs an extra package; install datanyx[presidio]") from None
        from .detectors import DEFAULT_MODEL

        model = model or (DEFAULT_MODEL if language == "en" else None)
        if model is None:
            raise SourceError(f"Presidio needs a spaCy model for {language!r}: use presidio:{language}:MODEL")
        _require_model(model)
        self._language = language
        self._threshold = threshold
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            provider = NlpEngineProvider(
                nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": language, "model_name": model}]}
            )
            self._engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=[language])

    def find(self, text: str) -> Iterator[Span]:
        for offset, chunk in _split_chunks(text):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # Presidio's logging can quote what it was analysing.
                results = self._engine.analyze(text=chunk, language=self._language, score_threshold=self._threshold)
            for result in results:
                yield Span(offset + result.start, offset + result.end, _PRESIDIO_KINDS.get(result.entity_type, MISC))


class GlinerDetector:
    """A GLiNER model asked for each kind of personal data."""

    def __init__(self, model: str = DEFAULT_GLINER_MODEL, threshold: float = 0.5, window: int = 1200) -> None:
        try:
            from gliner import GLiNER
        except ImportError:
            raise SourceError("the GLiNER backend needs the gliner package; install it with: pip install gliner") from None
        try:
            self._model: Any = GLiNER.from_pretrained(model)
        except Exception as error:
            raise SourceError(f"the GLiNER model could not be loaded ({type(error).__name__})") from None
        self._threshold = threshold
        self._window = window
        self._labels = list(_GLINER_LABELS)

    def find(self, text: str) -> Iterator[Span]:
        for offset, window in _windows(text, self._window):
            for entity in self._model.predict_entities(window, self._labels, threshold=self._threshold):
                kind = _GLINER_LABELS.get(str(entity["label"]).lower(), MISC)
                yield Span(offset + int(entity["start"]), offset + int(entity["end"]), kind)


class OllamaDetector:
    """A language model on this computer, asked to quote what is sensitive in each passage."""

    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL, url: str = DEFAULT_OLLAMA_URL, timeout: float = 180.0) -> None:
        host = urllib.parse.urlparse(url).hostname or ""
        if host.lower() not in _LOOPBACK:
            raise SourceError("only an Ollama server on this computer can be used: the text would otherwise leave it")
        self._model = model
        self._url = url.rstrip("/") + "/api/generate"
        self._timeout = timeout

    def find(self, text: str) -> Iterator[Span]:
        for offset, chunk in _windows(text, 3000):
            for item in self._ask(chunk):
                quoted, kind = item["text"], _OLLAMA_KINDS.get(str(item.get("kind", "")).upper(), MISC)
                if not quoted.strip():
                    continue
                start = chunk.find(quoted)
                while start != -1:
                    yield Span(offset + start, offset + start + len(quoted), kind)
                    start = chunk.find(quoted, start + len(quoted))

    def _ask(self, chunk: str) -> list[dict[str, Any]]:
        prompt = (
            "List every piece of personal or identifying information in the text below: names of people, "
            "organisations, places, addresses, email addresses, phone numbers, dates of birth, and ids or "
            "account numbers. Quote each exactly as it is written. Reply only with JSON.\n\nTEXT:\n" + chunk
        )
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": sorted(_OLLAMA_KINDS)}},
                "required": ["text", "kind"],
            }}},
            "required": ["items"],
        }
        body = json.dumps({"model": self._model, "prompt": prompt, "stream": False, "format": schema, "options": {"temperature": 0}})
        request = urllib.request.Request(self._url, body.encode("utf-8"), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                answer = json.loads(response.read().decode("utf-8"))
            items = json.loads(answer["response"])["items"]
            return [item for item in items if isinstance(item, dict) and isinstance(item.get("text"), str)]
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SourceError("the local Ollama server could not be reached; is it running?") from None
        except (ValueError, KeyError, TypeError):
            raise SourceError("the local model's answer wasn't usable; try another model") from None


def _require_model(model: str) -> None:
    try:
        import spacy.util
    except ImportError:
        raise SourceError('this backend needs spaCy; run: pip install "datanyx[text]"') from None
    if not spacy.util.is_package(model):
        raise SourceError(f"NER model not found; run: python -m spacy download {model}")


def _windows(text: str, size: int) -> Iterator[tuple[int, str]]:
    """Pieces of at most `size` characters, cut at whitespace, with where each starts."""
    position = 0
    while position < len(text):
        end = min(position + size, len(text))
        if end < len(text):
            cut = max(text.rfind(chr(10), position, end), text.rfind(" ", position, end))
            end = cut + 1 if cut > position else end
        yield position, text[position:end]
        position = end


# --- choosing one ---------------------------------------------------------------------------------------

BACKENDS = ("presidio", "gliner", "ollama")


def build_backend(spec: str) -> Detector:
    """A detector from a `--backend` value such as `presidio`, `presidio:de:de_core_news_lg`,
    `gliner:urchade/gliner_multi_pii-v1` or `ollama:llama3.1@http://localhost:11434`."""
    name, _, argument = spec.partition(":")
    name = name.strip().lower()
    if name == "presidio":
        language, _, model = argument.partition(":")
        return PresidioDetector(language or "en", model or None)
    if name == "gliner":
        return GlinerDetector(argument or DEFAULT_GLINER_MODEL)
    if name == "ollama":
        model, _, url = argument.partition("@")
        return OllamaDetector(model or DEFAULT_OLLAMA_MODEL, url or DEFAULT_OLLAMA_URL)
    raise SourceError(f"unknown backend {name!r}; the choices are {', '.join(BACKENDS)}")


def availability() -> dict[str, bool]:
    """Which backends' packages are installed, for `datanyx doctor`."""
    return {
        "presidio": _installed("presidio_analyzer"),
        "gliner": _installed("gliner"),
        "ollama": True,  # Needs a server rather than a package.
    }


def _installed(module: str) -> bool:
    import importlib.util
    import sys

    if module in sys.modules:
        return sys.modules[module] is not None
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False

