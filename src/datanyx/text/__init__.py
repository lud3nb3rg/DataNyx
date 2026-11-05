"""Anonymize free text by replacing sensitive spans with placeholders."""

from __future__ import annotations

from .anonymizer import Anonymizer, anonymize, anonymize_with_mapping, restore, unknown_placeholders
from .custom import DictionaryDetector, RegexDetector, build_detectors, kinds_of, load_detector_file
from .detectors import DEFAULT_MODEL, ENTITY_KINDS, Detector, PatternDetector, SpacyDetector, Span
from .secrets import SECRET, SecretDetector

__all__ = [
    "DEFAULT_MODEL",
    "ENTITY_KINDS",
    "SECRET",
    "Anonymizer",
    "Detector",
    "DictionaryDetector",
    "PatternDetector",
    "RegexDetector",
    "SecretDetector",
    "SpacyDetector",
    "Span",
    "anonymize",
    "anonymize_with_mapping",
    "build_detectors",
    "kinds_of",
    "load_detector_file",
    "restore",
    "unknown_placeholders",
]
