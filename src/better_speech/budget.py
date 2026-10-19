"""One monotonic deadline shared across all phases."""

import math
import time


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if not math.isfinite(seconds) or seconds <= 0:
        raise TimeoutError("deadline expired")
    return seconds


def phase_deadline(deadline: float, reserve: float) -> float:
    return deadline - min(max(0, reserve), max(0, deadline - time.monotonic()) * 0.5)
