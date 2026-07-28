"""Fan out stream sources and chime whenever a viewer count changes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Final

from youtube_live_count_chime.models import StreamSnapshot, StreamSource
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
    play: Callable[[Path], None] = play_sound,
) -> None:
    """Watch every source concurrently, chiming once per viewer-count change.

    Each source has one consumer that keeps the previous live snapshot and
    chimes when the same stream's count moves. A playback failure (e.g. the
    output device switching mid-chime) is warned and skipped so one channel's
    audio glitch never stops the watcher. Any other exception escaping a
    source is an unexpected bug: the TaskGroup cancels the siblings and
    ``main`` reports it (named with the channel) and exits non-zero.
    """
    chime_lock = asyncio.Lock()

    async def consume(source: StreamSource) -> None:
        previous: StreamSnapshot | None = None
        try:
            async for snapshot in source.snapshots():
                if snapshot is None:
                    # A failed poll: unknown, not offline. The chime baseline is
                    # a pre-outage sample, so clear it — that is what keeps the
                    # first poll back from chiming a gap-wide delta. Recovery
                    # re-baselines silently, at one lost chime per failed poll.
                    previous = None
                    continue
                if snapshot.stream_id is None:
                    previous = None
                    continue
                assert snapshot.viewers is not None  # live snapshot invariant
                if (
                    previous is not None
                    and previous.stream_id == snapshot.stream_id
                    and previous.viewers != snapshot.viewers
                ):
                    assert previous.viewers is not None
                    rising = snapshot.viewers > previous.viewers
                    direction = "up" if rising else "down"
                    sound = config.up_sound if rising else config.down_sound
                    print(
                        f"{source.target.key}: {previous.viewers} -> {snapshot.viewers} "
                        f"({direction})",
                        flush=True,
                    )
                    async with chime_lock:
                        try:
                            await asyncio.to_thread(play, sound)
                        except SoundPlaybackError as error:
                            _LOGGER.warning(
                                "could not play chime for %s: %s", source.target.key, error
                            )
                previous = snapshot
        except Exception as error:
            # Name the channel in the failure that main will report.
            raise RuntimeError(f"source {source.target.key} failed") from error

    async with asyncio.TaskGroup() as group:
        for source in sources:
            group.create_task(consume(source))
