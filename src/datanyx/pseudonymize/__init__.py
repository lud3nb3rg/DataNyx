"""Replace real values in tables with consistent fake ones, so data can be shared and joined."""

from __future__ import annotations

from .fakes import KINDS, Pseudonymizer
from .keys import load_key
from .plan import Treatment, build_plan, classify, parse_overrides, render_plan
from .tables import Options, Report, detect_kind, pseudonymize_file

__all__ = [
    "KINDS",
    "Options",
    "Pseudonymizer",
    "Report",
    "Treatment",
    "build_plan",
    "classify",
    "detect_kind",
    "load_key",
    "parse_overrides",
    "pseudonymize_file",
    "render_plan",
]
