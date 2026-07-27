"""Fan out stream sources and chime whenever a viewer count changes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Final

from youtube_live_count_chime.digest import Roster, render_title
from youtube_live_count_chime.models import ArrivalNamer, StreamSnapshot, StreamSource
from youtube_live_count_chime.notify import NotificationError, post_notification
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
    notify: Callable[[str, str], None] = post_notification,
    namer: ArrivalNamer | None = None,
) -> None:
    """Watch every source concurrently, chiming and notifying on count changes.

    A rise also posts a macOS notification naming the arrival when the chat
    roster reveals it. The roster of current counts is shared across consumers
    so every notification carries the same fixed-shape digest. The chime
    fires first and unconditionally, ahead of both the chat-roster round
    trip and the osascript call. Naming, playback, and notification
    failures are each warned and skipped so one channel's glitch never
    stops the watcher: an unnamed notification still posts, a failed
    notification still leaves the chime played, and neither costs the
    other channels.
    """
    chime_lock = asyncio.Lock()
    roster = Roster(tuple(source.name for source in sources))

    async def consume(source: StreamSource) -> None:
        previous: StreamSnapshot | None = None
        try:
            async for snapshot in source.snapshots():
                roster.update(source.name, snapshot.viewers)
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
                        f"{source.name}: {previous.viewers} -> {snapshot.viewers} "
                        f"({direction})",
                        flush=True,
                    )
                    # Chime first: it is the pre-existing signal and owes
                    # nothing to the network. Naming costs a chat-roster
                    # round trip and the banner costs an osascript call, so
                    # doing either first would delay every chime behind I/O.
                    async with chime_lock:
                        try:
                            await asyncio.to_thread(play, sound)
                        except SoundPlaybackError as error:
                            _LOGGER.warning(
                                "could not play chime for %s: %s", source.name, error
                            )
                    if rising:
                        delta = snapshot.viewers - previous.viewers
                        names: tuple[str, ...] = ()
                        if namer is not None:
                            # ArrivalNamer is a public protocol, so treat it
                            # like play/notify: a name is a nice-to-have and
                            # must never cost the notification or the chime.
                            try:
                                names = await namer.arrivals(
                                    snapshot.target, snapshot.stream_id
                                )
                            except Exception as error:
                                _LOGGER.warning(
                                    "could not name arrivals for %s: %s",
                                    source.name,
                                    error,
                                )
                        title = render_title(snapshot.target, delta, names)
                        try:
                            await asyncio.to_thread(notify, title, roster.render())
                        except NotificationError as error:
                            _LOGGER.warning(
                                "could not notify for %s: %s", source.name, error
                            )
                previous = snapshot
        except Exception as error:
            # Name the channel in the failure that main will report.
            raise RuntimeError(f"source {source.name} failed") from error

    async with asyncio.TaskGroup() as group:
        for source in sources:
            group.create_task(consume(source))
