"""Tiny helper for "N of M (~ETA)" progress labels."""

from __future__ import annotations

import time


def format_eta(elapsed_s: float, done: int, total: int) -> str:
    """
    'N/total (~Xs left)' — a plain linear estimate (rate = done/elapsed).
    Returns just 'N/total' for done<=0 or elapsed<=0 (nothing to rate yet).
    """
    if total <= 0:
        return ""
    if done <= 0 or elapsed_s <= 0:
        return f"{done}/{total}"
    rate = done / elapsed_s
    remaining = max(0.0, (total - done) / rate) if rate > 0 else 0.0
    if remaining < 1.0:
        eta = "<1s left"
    elif remaining < 60.0:
        eta = f"~{remaining:.0f}s left"
    else:
        eta = f"~{remaining / 60.0:.1f}m left"
    return f"{done}/{total} ({eta})"


class ElapsedTimer:
    """Minimal stand-in for a monotonic stopwatch, used by progress callbacks
    that only get (done, total) and need elapsed time for format_eta."""

    def __init__(self):
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._start
