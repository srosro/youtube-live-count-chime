"""Render the deterministic digest shown in watcher notifications."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from youtube_live_count_chime.models import StreamTarget


# Names listed in full before collapsing to "and N more"; keeps the title
# readable in the macOS banner, which truncates aggressively.
MAX_NAMES: Final = 3

_SEPARATOR: Final = " · "


def _display(key: str) -> str:
    """Turn a ``platform:handle`` key into ``platform handle`` for display."""
    return key.replace(":", " ", 1)


@dataclass(slots=True)
class Roster:
    """Current viewer counts for every monitored target, in a fixed order.

    ``order`` is the source order from ``build_sources`` and never changes at
    runtime, so the rendered body has the same shape on every notification.
    """

    order: tuple[str, ...]
    counts: dict[str, int | None] = field(default_factory=dict)

    def update(self, key: str, viewers: int | None) -> None:
        """Record a target's latest count (``None`` when offline)."""
        self.counts[key] = viewers

    def render(self) -> str:
        """Render every target, including unchanged, offline, and unseen ones."""
        parts = []
        for key in self.order:
            viewers = self.counts.get(key)
            shown = "offline" if viewers is None else str(viewers)
            parts.append(f"{_display(key)} {shown}")
        return _SEPARATOR.join(parts)


def render_title(target: StreamTarget, delta: int, names: Sequence[str]) -> str:
    """Render the arrival line, degrading to a bare count when no name is known."""
    where = _display(target.key)
    if not names:
        return f"+{delta} watching {where}"
    if len(names) == 1:
        return f"{names[0]} is now watching {where}"
    if len(names) <= MAX_NAMES:
        return f"{', '.join(names)} are now watching {where}"
    listed = ", ".join(names[:MAX_NAMES])
    return f"{listed} and {len(names) - MAX_NAMES} more are now watching {where}"
