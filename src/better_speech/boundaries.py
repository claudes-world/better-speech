"""Shared source-only placement boundaries for preparation and speech planning."""

from __future__ import annotations

import re


def protected_fences(source: str) -> tuple[tuple[int, int], ...]:
    """Half-open fenced-code ranges; unclosed fences protect the final offset."""
    opened = None
    ranges = []
    for match in re.finditer(r"(?m)^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)", source):
        marker = match.group(1)
        if opened is None:
            opened = (match.start(), marker[0], len(marker))
        elif (
            marker[0] == opened[1]
            and len(marker) >= opened[2]
            and not source[match.start() : match.end()].strip().strip(marker[0])
        ):
            ranges.append((opened[0], match.end()))
            opened = None
    if opened is not None:
        ranges.append((opened[0], len(source) + 1))
    return tuple(ranges)


def paragraph_boundaries(source: str) -> tuple[frozenset[int], frozenset[int]]:
    """Safe start/end offsets, including either side of whitespace blank lines."""
    protected = protected_fences(source)
    separators = tuple(re.finditer(r"\n[ \t]*\n", source))
    starts = {0, *(m.end() for m in separators)}
    ends = {len(source), *(m.start() for m in separators)}

    def safe(offset):
        return not any(a <= offset < b for a, b in protected)

    return (
        frozenset(offset for offset in starts if safe(offset)),
        frozenset(offset for offset in ends if safe(offset)),
    )
