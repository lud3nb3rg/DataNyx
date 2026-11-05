"""Anonymize free text by replacing sensitive spans with placeholders."""

from __future__ import annotations

from .anonymizer import anonymize, anonymize_with_mapping, restore, unknown_placeholders
from .detectors import DEFAULT_MODEL, ENTITY_KINDS, Detector, PatternDetector, SpacyDetector, Span
from .secrets import SECRET, SecretDetector

__all__ = [
    "DEFAULT_MODEL",
    "ENTITY_KINDS",
    "SECRET",
    "Detector",
    "PatternDetector",
    "SecretDetector",
    "SpacyDetector",
    "Span",
    "anonymize",
    "anonymize_with_mapping",
    "restore",
    "unknown_placeholders",
]
