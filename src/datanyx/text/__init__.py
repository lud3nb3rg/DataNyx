"""Anonymize free text by replacing sensitive spans with placeholders."""

from __future__ import annotations

from .anonymizer import anonymize
from .detectors import DEFAULT_MODEL, ENTITY_KINDS, Detector, PatternDetector, Span, SpacyDetector

__all__ = [
    "DEFAULT_MODEL",
    "ENTITY_KINDS",
    "Detector",
    "PatternDetector",
    "Span",
    "SpacyDetector",
    "anonymize",
]
