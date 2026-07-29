"""Render the deterministic digest shown in watcher notifications."""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Final

from youtube_live_count_chime.models import StreamTarget


_SEPARATOR: Final = " · "
# A target polled but not yet seen live is not the same as one observed
# offline, and must not claim to be: the first notification can fire before
# every channel's first poll has come back.
_UNPOLLED: Final = "?"


def describe_rise(target: StreamTarget, delta: int) -> str:
    """Describe one channel's rise, for both the spoken line and the banner title.

    A rise is announced twice — aloud through ``say`` and on the banner — and
    the two must be the same sentence, so this is the one place it is worded.
    ``delta`` is a rise: a fall is chimed and never narrated, so a
    non-positive delta here would word nonsense ("-3 new viewer") for a line
    nobody should be building.
    """
    assert delta > 0, f"describe_rise is rise-only, got {delta}"
    return f"{delta} new viewer{'s' if delta > 1 else ''} on {target.label}"


# Handles are run-together lowercase words, and `say` renders them as noise
# ("watchmepivot") or reads an underscore aloud. Only the voice needs the
# repair: the banner keeps the real handle, because that is what matches the
# channel and what a viewer would search for — "watch me pivot" written down
# would be wrong. So `describe_rise` stays the one wording and this is a pure
# layer over it. Explicit spellings win over the underscore rule below.
_SPOKEN_HANDLES: Final[dict[str, str]] = {
    "watchmepivot": "watch me pivot",
    "samtriestobuild": "sam tries to build",
}
# A handle is an atom: `watchmepivot` must not fire inside `watchmepivot2` or
# `watchmepivot-2`, which are other channels, so the guards are the handle
# charset from `normalize_handle` rather than `\b` (which would split on `-`).
_SPOKEN_HANDLE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![a-z0-9._-])(?:"
    + "|".join(map(re.escape, _SPOKEN_HANDLES))
    + r")(?![a-z0-9._-])"
)


def for_speech(text: str) -> str:
    """Respell a line's channel handles for ``say``, leaving it otherwise intact."""
    said = _SPOKEN_HANDLE_PATTERN.sub(
        lambda match: _SPOKEN_HANDLES[match.group()], text
    )
    return said.replace("_", " ")


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
