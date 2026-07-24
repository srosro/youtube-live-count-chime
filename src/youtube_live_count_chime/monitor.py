"""Fan out stream sources and chime whenever a viewer count changes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Final

from youtube_live_count_chime.models import CountTracker, StreamSource
from youtube_live_count_chime.sounds import SoundPlaybackError, play_sound


_LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChimeConfig:
    """The sounds played when a viewer count rises or falls."""

    up_sound: Path
    down_sound: Path


async def monitor(
    sources: Sequence[StreamSource],
    config: ChimeConfig,
    *,
    tracker: CountTracker | None = None,
    play: Callable[[Path], None] = play_sound,
) -> None:
    """Watch every source concurrently, chiming once per viewer-count change.

    Sources swallow and retry their own fetch failures, so an exception that
    escapes one is an unexpected bug: it propagates and stops the watcher
    rather than being silently absorbed (``main`` reports it and exits
    non-zero).
    """
    shared_tracker = tracker if tracker is not None else CountTracker()
    chime_lock = asyncio.Lock()

    async def consume(source: StreamSource) -> None:
        async for snapshot in source.snapshots():
            change = shared_tracker.observe(snapshot)
            if change is None:
                continue
            sound = config.up_sound if change.direction == "up" else config.down_sound
            print(
                f"{source.name}: {change.previous} -> {change.current} "
                f"({change.direction})",
                flush=True,
            )
            async with chime_lock:
                try:
                    await asyncio.to_thread(play, sound)
                except SoundPlaybackError as error:
                    _LOGGER.warning(
                        "could not play chime for %s: %s", source.name, error
                    )

    await asyncio.gather(*(consume(source) for source in sources))
