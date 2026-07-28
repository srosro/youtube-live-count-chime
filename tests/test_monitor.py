import asyncio
import io
import time
import unittest
from collections.abc import AsyncIterator, Sequence
from contextlib import redirect_stdout
from pathlib import Path

from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget
from youtube_live_count_chime.monitor import ChimeConfig, monitor
from youtube_live_count_chime.notify import NotificationError
from youtube_live_count_chime.sounds import SoundPlaybackError
from youtube_live_count_chime.speech import SpeechError


UP = Path("/System/Library/Sounds/Glass.aiff")
DOWN = Path("/System/Library/Sounds/Basso.aiff")


class FakeSource:
    """Replay scripted polls; a ``None`` entry marks entering an outage.

    Consecutive ``None``s are not what the real producer emits — it marks
    once per outage — but driving them here is what pins the consumer's
    idempotence, which that suppression depends on.
    """

    def __init__(
        self, target: StreamTarget, snaps: Sequence[StreamSnapshot | None]
    ) -> None:
        self.target = target
        self._snaps = snaps

    async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        for snap in self._snaps:
            yield snap


class LeadingSource(FakeSource):
    """Replay every scripted poll, then release ``done`` for a follower.

    Cross-task interleaving is otherwise unspecified, so a test that asserts
    one channel's entry in another channel's notification has to order the
    two polls explicitly.
    """

    def __init__(
        self,
        target: StreamTarget,
        snaps: Sequence[StreamSnapshot | None],
        done: asyncio.Event,
    ) -> None:
        super().__init__(target, snaps)
        self._done = done

    async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        for snap in self._snaps:
            yield snap
        self._done.set()


class FollowingSource(FakeSource):
    """Baseline first, then hold the remaining polls until ``after`` is set."""

    def __init__(
        self,
        target: StreamTarget,
        snaps: Sequence[StreamSnapshot | None],
        after: asyncio.Event,
    ) -> None:
        super().__init__(target, snaps)
        self._after = after

    async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        yield self._snaps[0]
        await self._after.wait()
        for snap in self._snaps[1:]:
            yield snap


class ExplodingSource:
    target = StreamTarget(Platform.TWITCH, "channel-x")

    async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        raise RuntimeError("upstream failure")
        yield  # pragma: no cover - marks this an async generator


def live(target: StreamTarget, viewers: int) -> StreamSnapshot:
    return StreamSnapshot(target, "s1", viewers)


def silent(title: str, body: str) -> None:
    """Swallow notifications in tests that assert only on chime behavior.

    monitor() defaults to the real osascript notifier, so a rise in a test
    that does not inject one posts an actual banner on macOS. Off macOS it
    is worse than a failure: the OSError becomes a NotificationError, which
    monitor downgrades to a warning, so the only symptom is a stray WARNING
    record breaking some other test's log-count assertion.
    """


def mute(text: str) -> None:
    """Swallow announcements in tests that assert only on chime behavior.

    Same hazard as `silent`: monitor() defaults to the real `say`, so a rise
    in a test that does not inject one talks out loud on macOS and warns
    everywhere else.
    """


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_chimes_up_and_down_but_not_on_baseline_or_no_change(self) -> None:
        target = StreamTarget(Platform.YOUTUBE, "a")
        snaps = [live(target, 5), live(target, 5), live(target, 9), live(target, 2)]
        played: list[Path] = []
        await monitor(
            [FakeSource(target, snaps)],
            ChimeConfig(UP, DOWN),
            play=played.append,
            speak=mute,
            notify=silent,
        )
        self.assertEqual(played, [UP, DOWN])  # 5->5 silent, 5->9 up, 9->2 down

    async def test_tracks_each_target_independently(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")
        b = StreamTarget(Platform.TWITCH, "b")
        played: list[Path] = []
        await monitor(
            [
                FakeSource(a, [live(a, 1), live(a, 2)]),
                FakeSource(b, [live(b, 100), live(b, 100)]),
            ],
            ChimeConfig(UP, DOWN),
            play=played.append,
            speak=mute,
            notify=silent,
        )
        self.assertEqual(played, [UP])  # a 1->2 up; b unchanged; baselines silent

    async def test_prints_the_transition_in_order(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "chan")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            await monitor(
                [FakeSource(a, [live(a, 5), live(a, 9)])],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
                speak=mute,
                notify=silent,
            )
        # Pins previous->current order and the up/down word (a swap would show
        # "9 -> 5" or "(down)").
        self.assertIn("youtube:chan: 5 -> 9 (up)", buffer.getvalue())

    async def test_reset_then_resume_chiming(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")
        played: list[Path] = []
        await monitor(
            [
                FakeSource(
                    a,
                    [
                        live(a, 5),  # baseline
                        StreamSnapshot.offline(a),  # offline clears the baseline
                        live(a, 9),  # fresh baseline after the reset (silent)
                        StreamSnapshot(a, "s2", 20),  # different stream re-baselines
                        StreamSnapshot(a, "s2", 25),  # same stream 20->25 -> chimes
                    ],
                )
            ],
            ChimeConfig(UP, DOWN),
            play=played.append,
            speak=mute,
            notify=silent,
        )
        # Every reset step is silent, and chiming resumes on the next real delta.
        self.assertEqual(played, [UP])

    async def test_playback_failure_warns_and_keeps_watching(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")

        def boom(path: Path) -> None:
            raise SoundPlaybackError("audio device gone")

        # A transient audio failure must not tear the watcher down: both deltas
        # (1->2 and 2->3) are attempted, each warned and skipped, and monitor
        # completes normally. Two warnings pins that it kept going after the first.
        with self.assertLogs("youtube_live_count_chime.monitor", "WARNING") as logs:
            await monitor(
                [FakeSource(a, [live(a, 1), live(a, 2), live(a, 3)])],
                ChimeConfig(UP, DOWN),
                play=boom,
                speak=mute,
                notify=silent,
            )
        self.assertEqual(len(logs.records), 2)

    async def test_unexpected_source_error_stops_the_watcher(self) -> None:
        healthy = StreamTarget(Platform.YOUTUBE, "a")
        with self.assertRaises(ExceptionGroup) as ctx:
            await monitor(
                [
                    ExplodingSource(),
                    FakeSource(healthy, [live(healthy, 1), live(healthy, 4)]),
                ],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
                speak=mute,
                notify=silent,
            )
        # Pin the full wrapper message and the preserved cause: this fails if
        # the channel-naming wrapper or its `from error` chaining is dropped.
        messages = [str(error) for error in ctx.exception.exceptions]
        self.assertIn("source twitch:channel-x failed", messages)
        causes = [type(error.__cause__) for error in ctx.exception.exceptions]
        self.assertIn(RuntimeError, causes)

    async def test_recovery_from_a_failed_poll_re_baselines_rather_than_spanning_it(
        self,
    ) -> None:
        # While a poll is failing the watcher is blind, so the count it last saw
        # is a pre-outage sample. Keeping it would chime "10 -> 500" on the first
        # read back — a delta measured across an unbounded gap. Clearing the
        # baseline makes the recovery poll re-baseline silently instead; only
        # the genuine 500 -> 502 rise after it chimes. The consecutive
        # `None`s here exceed what poll_snapshots emits (it marks once per
        # outage) precisely to pin that repeats stay a no-op, and a leading
        # one arrives before any baseline exists.
        a = StreamTarget(Platform.TWITCH, "chan")
        played: list[Path] = []
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            await monitor(
                [
                    FakeSource(
                        a,
                        [None, None, live(a, 10), None, None, live(a, 500), live(a, 502)],
                    )
                ],
                ChimeConfig(UP, DOWN),
                play=played.append,
                speak=mute,
                notify=silent,
            )
        # Pin *which* rise chimed, not just how many: swallowing the genuine
        # 500 -> 502 while chiming the gap-spanning 10 -> 500 is the exact
        # inversion this test forbids, and a bare count cannot tell them apart.
        self.assertEqual(played, [UP])
        self.assertIn("twitch:chan: 500 -> 502 (up)", buffer.getvalue())


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_rise_chimes_then_speaks_then_posts_before_the_next_poll(
        self,
    ) -> None:
        # The chime is the pre-existing signal and owes nothing to the
        # network, so it precedes both the announcement and the banner's real
        # (~0.13s) osascript call; the announcement precedes the banner
        # because it is the signal that survives OBS suppressing banners.
        # And each is awaited inline, so one channel's rises are announced and
        # posted in order rather than collapsing or overlapping. A single-rise
        # version of this passes even with the work detached, because the
        # TaskGroup joins it before monitor() returns; two rises and steps
        # that take time are what separate the two designs. (The exact
        # title and body are pinned by the digest tests, not here.)
        a = StreamTarget(Platform.TWITCH, "chan")
        events: list[str] = []

        def slow_speak(text: str) -> None:
            # Real speech takes ~3.6s.
            time.sleep(0.05)
            events.append("speak")

        def slow_notify(title: str, body: str) -> None:
            # Real osascript takes ~0.13s.
            time.sleep(0.05)
            events.append("notify")

        await monitor(
            [FakeSource(a, [live(a, 1), live(a, 2), live(a, 3)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: events.append("chime"),
            speak=slow_speak,
            notify=slow_notify,
        )

        self.assertEqual(
            events, ["chime", "speak", "notify", "chime", "speak", "notify"]
        )

    async def test_two_channels_rising_at_once_never_talk_over_each_other(self) -> None:
        # The point of speaking *inside* chime_lock: a channel's chime and the
        # announcement it introduces are one uninterruptible unit of audio, so
        # a second channel rising at the same moment queues behind the whole
        # pair rather than chiming into the middle of someone's sentence.
        # Hoisting `speak` out of the lock (or taking the lock twice) reorders
        # this to chime, chime, speak, speak — which is the failure this pins.
        # Speech is slow, so the window for the other channel to cut in is
        # real; only the lock closes it.
        a = StreamTarget(Platform.TWITCH, "a")
        b = StreamTarget(Platform.YOUTUBE, "b")
        events: list[str] = []

        def slow_speak(text: str) -> None:
            time.sleep(0.05)  # real speech takes ~3.6s
            events.append(f"speak:{text}")

        await monitor(
            [
                FakeSource(a, [live(a, 1), live(a, 2)]),
                FakeSource(b, [live(b, 10), live(b, 12)]),
            ],
            ChimeConfig(UP, DOWN),
            play=lambda path: events.append("chime"),
            speak=slow_speak,
            notify=silent,
        )

        # Which channel wins the lock first is scheduling, but the pairing is
        # not: every chime is immediately followed by its own announcement.
        self.assertEqual(
            [event.split(":")[0] for event in events],
            ["chime", "speak", "chime", "speak"],
        )
        self.assertEqual(
            sorted(event for event in events if event.startswith("speak:")),
            ["speak:1 new viewer on twitch a", "speak:2 new viewers on youtube b"],
        )

    async def test_the_spoken_line_is_the_banner_title(self) -> None:
        # The DRY contract: one wording reaches both consumers, so a streamer
        # hears exactly what the banner would have said. Two format strings
        # would drift the moment either is reworded.
        a = StreamTarget(Platform.TWITCH, "chan")
        spoken: list[str] = []
        posted: list[tuple[str, str]] = []

        await monitor(
            [FakeSource(a, [live(a, 1), live(a, 3)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            speak=spoken.append,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(spoken, ["2 new viewers on twitch chan"])
        self.assertEqual([title for title, _ in posted], spoken)

    async def test_a_fall_chimes_but_neither_speaks_nor_posts(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        posted: list[tuple[str, str]] = []
        spoken: list[str] = []
        played: list[Path] = []

        await monitor(
            [FakeSource(a, [live(a, 5), live(a, 2)])],
            ChimeConfig(UP, DOWN),
            play=played.append,
            speak=spoken.append,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(posted, [])
        self.assertEqual(spoken, [])  # a departure is not worth narrating
        self.assertEqual(played, [DOWN])  # the fall is still audible

    async def test_a_speech_failure_warns_and_still_posts_the_notification(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        posted: list[tuple[str, str]] = []
        played: list[Path] = []

        def explode(text: str) -> None:
            raise SpeechError("no voice installed")

        # A SpeechError escaping into the TaskGroup would cancel every
        # channel, and swallowing it silently would leave the operator with no
        # signal — so it is warned, and the banner it precedes still posts.
        with self.assertLogs("youtube_live_count_chime.monitor", "WARNING") as logs:
            await monitor(
                [FakeSource(a, [live(a, 1), live(a, 2), live(a, 5)])],
                ChimeConfig(UP, DOWN),
                play=played.append,
                speak=explode,
                notify=lambda title, body: posted.append((title, body)),
            )

        self.assertEqual(len(logs.records), 2)  # kept watching past the first
        self.assertEqual(played, [UP, UP])
        self.assertEqual(
            [title for title, _ in posted],
            ["1 new viewer on twitch chan", "3 new viewers on twitch chan"],
        )

    async def test_a_notification_failure_does_not_stop_the_watcher(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        b = StreamTarget(Platform.YOUTUBE, "other")
        played: list[Path] = []

        def explode(title: str, body: str) -> None:
            raise NotificationError("banner refused")

        # The warning is the operator's only signal that banners are broken,
        # and a NotificationError escaping into the TaskGroup would cancel the
        # siblings — so the second channel pins that a refused banner takes
        # down neither its own channel nor any other.
        with self.assertLogs("youtube_live_count_chime.monitor", "WARNING"):
            await monitor(
                [
                    FakeSource(a, [live(a, 1), live(a, 2), live(a, 5)]),
                    FakeSource(b, [live(b, 1), live(b, 4)]),
                ],
                ChimeConfig(UP, DOWN),
                play=played.append,
                speak=mute,
                notify=explode,
            )

        self.assertEqual(played, [UP, UP, UP])  # all three rises still chimed

    async def test_the_digest_tells_a_failed_poll_apart_from_a_confirmed_offline(
        self,
    ) -> None:
        # The banner is the digest of *every* watched channel in source order,
        # not just the one that moved, and all three roster states must
        # survive the trip through monitor. A failed poll means "unknown",
        # which is neither "offline" nor "still 7", so the rising channel's
        # banner must not publish the blind channel's pre-outage count as
        # though it were current. And `offline` is only reachable because the
        # roster is written before the offline branch returns: moving that
        # write below it degrades every observed offline channel to `?`.
        # Holding the rise until the other three have reported removes the
        # cross-task scheduling nondeterminism, so title and body can both be
        # pinned exactly — a substring check would also pass against a body
        # that dropped the steady channel entirely.
        rising = StreamTarget(Platform.YOUTUBE, "rising")
        steady = StreamTarget(Platform.YOUTUBE, "steady")
        blind = StreamTarget(Platform.TWITCH, "blind")
        ended = StreamTarget(Platform.TWITCH, "ended")
        posted: list[tuple[str, str]] = []
        steady_polled = asyncio.Event()
        blind_polled = asyncio.Event()
        ended_polled = asyncio.Event()
        all_polled = asyncio.Event()

        async def release_the_rise() -> None:
            """Hold the rise until every other channel has reported."""
            for polled in (steady_polled, blind_polled, ended_polled):
                await polled.wait()
            all_polled.set()

        await asyncio.gather(
            monitor(
                [
                    FollowingSource(
                        rising, [live(rising, 1), live(rising, 2)], all_polled
                    ),
                    LeadingSource(steady, [live(steady, 4)], steady_polled),
                    LeadingSource(blind, [live(blind, 7), None], blind_polled),
                    LeadingSource(ended, [StreamSnapshot.offline(ended)], ended_polled),
                ],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
                speak=mute,
                notify=lambda title, body: posted.append((title, body)),
            ),
            release_the_rise(),
        )

        self.assertEqual(len(posted), 1)
        title, body = posted[0]
        self.assertEqual(title, "1 new viewer on youtube rising")
        self.assertEqual(
            body,
            "youtube rising 2 · youtube steady 4 · twitch blind ? · twitch ended offline",
        )


if __name__ == "__main__":
    unittest.main()
