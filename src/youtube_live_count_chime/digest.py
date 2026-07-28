"""Render the deterministic digest shown in watcher notifications."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from youtube_live_count_chime.models import StreamTarget


_SEPARATOR: Final = " · "
# A target polled but not yet seen live is not the same as one observed
# offline, and must not claim to be: the first notification can fire before
# every channel's first poll has come back.
_UNPOLLED: Final = "?"


def render_roster(
    order: Sequence[StreamTarget],
    counts: dict[StreamTarget, int | None],
) -> str:
    """Render every target, including unchanged, offline, and unpolled ones.

    ``order`` is the source order from ``build_sources`` and never changes at
    runtime, so the rendered body has the same shape on every notification. A
    target missing from ``counts`` has not been polled yet, which is not the
    same as one observed offline (``None``).
    """
    parts = []
    for target in order:
        viewers = counts.get(target, _UNPOLLED)
        shown = "offline" if viewers is None else str(viewers)
        parts.append(f"{target.label} {shown}")
    return _SEPARATOR.join(parts)
