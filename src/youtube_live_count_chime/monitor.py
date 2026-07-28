"""Fan out stream sources and chime whenever a viewer count changes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Final

from youtube_live_count_chime.digest import describe_rise, render_roster
from youtube_live_count_chime.models import StreamSnapshot, StreamSource, StreamTarget
from youtube_live_count_chime.notify import NotificationError, post_notification
from youtube_live_count_chime.sounds import SoundPlaybackError, play_sound
from youtube_live_count_chime.speech import SpeechError, speak_text


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
    speak: Callable[[str], None] = speak_text,
    notify: Callable[[str, str], None] = post_notification,
) -> None:
    """Watch every source concurrently, chiming and notifying on count changes.

    A rise is also spoken aloud and posts a macOS notification. Speech is the
    signal that survives streaming: OBS suppresses banners while capturing, so
    the announcement is what the streamer actually gets. It is worded once, by
    ``describe_rise``, and used verbatim as both the spoken line and the
    banner title. The roster of current counts is
    shared across consumers, so every notification carries the same
    fixed-shape digest of every watched channel. The chime fires first,
    unconditionally, and awaited; the banner is then posted inline, so this
    channel's banners stay in order. Title and body are *not* one instant: the
    delta is measured before the audio, and ``render_roster`` reads the shared
    counts after it, so another channel's count in the body can be newer than
    the title's rise — and the rising channel's own body count is its latest,
    which is what the digest is for. Playback, speech, and notification failures
    are each warned and skipped so one channel's glitch never stops the
    watcher: a failed announcement still leaves the chime played and the
    banner posted. They are not free to the other channels, though — the
    chime and the announcement are awaited under the audio lock every source
    shares, so a wedged one blocks every channel's audio until its bound
    expires. Any other exception escaping a source is an unexpected bug: the
    TaskGroup cancels the siblings and ``main`` reports it (named with the
    channel) and exits non-zero.
    """
    chime_lock = asyncio.Lock()
    order = tuple(source.target for source in sources)
    counts: dict[StreamTarget, int | None] = {}

    async def consume(source: StreamSource) -> None:
        previous: StreamSnapshot | None = None

        async def chime(sound: Path) -> None:
            try:
                await asyncio.to_thread(play, sound)
            except SoundPlaybackError as error:
                _LOGGER.warning(
                    "could not play chime for %s: %s", source.target.key, error
                )

        try:
            async for snapshot in source.snapshots():
                if snapshot is None:
                    # A failed poll: unknown, not offline. Both the digest count
                    # and the chime baseline are pre-outage samples. Drop the
                    # count so the digest publishes no stale number, and clear
                    # the baseline so the first poll back cannot be a rise —
                    # which is what keeps recovery from chiming a gap-wide
                    # delta. Recovery re-baselines silently, at one lost chime
                    # per outage.
                    #
                    # This branch must stay idempotent: a sustained outage
                    # marks `None` once, not once per poll, so anything
                    # stateful added here (an outage counter, a gap-length
                    # rule) would silently stop being fed.
                    counts.pop(source.target, None)
                    previous = None
                    continue
                counts[source.target] = snapshot.viewers
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
                    delta = snapshot.viewers - previous.viewers
                    rising = delta > 0
                    direction = "up" if rising else "down"
                    print(
                        f"{source.target.key}: {previous.viewers} -> {snapshot.viewers} "
                        f"({direction})",
                        flush=True,
                    )
                    # Chime first: it is the pre-existing signal and owes
                    # nothing to the network. The banner costs an osascript
                    # call, so posting it first would delay every chime
                    # behind I/O.
                    #
                    # Speech is audio too, so it shares the chime's lock, and
                    # both are taken under a *single* acquisition: two
                    # channels rising at once must never talk over each other
                    # or over a chime, and a chime must never be separated
                    # from the announcement it introduces. The cost is
                    # accepted and fleet-wide: the lock is shared by every
                    # source, so a rise serializes *any* channel with a
                    # change, not just this one. Only a rise pays it — an
                    # unchanged poll costs nothing, and a
                    # fall pays only the chime it already paid. That is the
                    # chosen shape: a bare chime carries no information while
                    # streaming.
                    if rising:
                        announcement = describe_rise(source.target, delta)
                        async with chime_lock:
                            await chime(config.up_sound)
                            try:
                                await asyncio.to_thread(speak, announcement)
                            except SpeechError as error:
                                _LOGGER.warning(
                                    "could not speak for %s: %s", source.target.key, error
                                )
                        try:
                            await asyncio.to_thread(
                                notify,
                                announcement,
                                render_roster(order, counts),
                            )
                        except NotificationError as error:
                            _LOGGER.warning(
                                "could not notify for %s: %s", source.target.key, error
                            )
                    else:
                        async with chime_lock:
                            await chime(config.down_sound)
                previous = snapshot
        except Exception as error:
            # Name the channel in the failure that main will report.
            raise RuntimeError(f"source {source.target.key} failed") from error

    async with asyncio.TaskGroup() as group:
        for source in sources:
            group.create_task(consume(source))
