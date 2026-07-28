"""Render the deterministic digest shown in watcher notifications."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from youtube_live_count_chime.models import StreamTarget


# Names listed in full before collapsing to "and N more"; keeps the title
# readable in the macOS banner, which truncates aggressively.
MAX_NAMES: Final = 3

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


def render_title(target: StreamTarget, delta: int, names: Sequence[str]) -> str:
    """Render the arrival line, reconciling the named chatters with the rise.

    The chat roster is a lossy proxy for the viewer count, so ``names`` and
    ``delta`` routinely disagree. ``delta`` is authoritative for how many
    people arrived, so the title never names more than that many, and any
    arrival the roster could not name is carried by "and N more".
    """
    where = target.label
    listed = tuple(names[: min(MAX_NAMES, delta)])
    if not listed:
        return f"+{delta} watching {where}"
    unnamed = delta - len(listed)
    if unnamed > 0:
        return f"{', '.join(listed)} and {unnamed} more are now watching {where}"
    if len(listed) == 1:
        return f"{listed[0]} is now watching {where}"
    return f"{', '.join(listed)} are now watching {where}"
