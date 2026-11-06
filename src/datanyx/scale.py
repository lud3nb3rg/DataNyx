"""Coarse size hints: a row count as an order of magnitude, never a number.

An agent choosing between loading a table and streaming it needs to know whether it has hundreds of rows
or hundreds of millions, not the exact count. An exact count is data (it can identify a day's activity or
a small cohort), so only the decade is given, and everything under a thousand is one bucket.
"""

from __future__ import annotations

from .privacy import KeyPolicy

SMALL = "fewer than 1,000 rows"


def scale_label(count: int) -> str:
    """The bucket for `count`: "fewer than 1,000 rows", "10^3 to 10^4 rows", "10^4 to 10^5 rows", ..."""
    if count < 1000:
        return SMALL
    exponent = len(str(count)) - 1  # floor(log10(count))
    return f"10^{exponent} to 10^{exponent + 1} rows"


def scale_for(policy: KeyPolicy, count: int | None) -> str | None:
    """The bucket to record, or None when hints are off or the source can't say."""
    if not policy.scale_hints or count is None or count < 0:
        return None
    return scale_label(count)
