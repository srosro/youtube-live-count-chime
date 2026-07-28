import asyncio
import io
import unittest
from collections.abc import AsyncIterator, Sequence
from contextlib import redirect_stdout
from pathlib import Path

from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget
from youtube_live_count_chime.monitor import ChimeConfig, monitor
from youtube_live_count_chime.notify import NotificationError
from youtube_live_count_chime.sounds import SoundPlaybackError


UP = Path("/System/Library/Sounds/Glass.aiff")
DOWN = Path("/System/Library/Sounds/Basso.aiff")


class FakeSource:
    """Replay scripted polls; a ``None`` entry is a poll whose fetch failed."""

    def __init__(
        self, target: StreamTarget, snaps: Sequence[StreamSnapshot | None]
    ) -> None:
        self.target = target
        self._snaps = snaps

    async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        for snap in self._snaps:
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
    that does not inject one posts an actual banner (and fails off macOS).
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
        # the genuine 500 -> 502 rise after it chimes. A sustained outage is
        # consecutive `None`s (poll_snapshots yields one per failed poll), and
        # a leading one arrives before any baseline exists.
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
                notify=silent,
            )
        # Pin *which* rise chimed, not just how many: swallowing the genuine
        # 500 -> 502 while chiming the gap-spanning 10 -> 500 is the exact
        # inversion this test forbids, and a bare count cannot tell them apart.
        self.assertEqual(played, [UP])
        self.assertIn("twitch:chan: 500 -> 502 (up)", buffer.getvalue())
        self.assertNotIn("-> 500", buffer.getvalue())


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_chime_plays_before_notifying(self) -> None:
        # The chime is the pre-existing signal and owes nothing to the network.
        # The banner costs an osascript call (bounded at 10s), so ordering it
        # before the chime delays every chime behind I/O.
        a = StreamTarget(Platform.TWITCH, "chan")
        events: list[str] = []

        await monitor(
            [FakeSource(a, [live(a, 1), live(a, 2)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: events.append("chime"),
            notify=lambda title, body: events.append("notify"),
        )

        self.assertEqual(events, ["chime", "notify"])

    async def test_a_rise_notifies_with_the_delta_and_its_own_current_count(
        self,
    ) -> None:
        a = StreamTarget(Platform.TWITCH, "watchmepivot")
        b = StreamTarget(Platform.YOUTUBE, "srosrosr")
        posted: list[tuple[str, str]] = []

        # The other source (b) is present to exercise the multi-source shape
        # of the digest, but its own rendered count depends on cross-task
        # scheduling order, which monitor() does not guarantee — that
        # full-roster rendering (fixed order, unchanged/offline entries) is
        # exhaustively covered directly against render_roster in test_digest.py.
        # Only the rising channel's own count is deterministic here: its
        # roster entry is always written before it notifies.
        await monitor(
            [
                FakeSource(a, [live(a, 1), live(a, 3)]),
                FakeSource(b, [live(b, 7)]),
            ],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(len(posted), 1)
        title, body = posted[0]
        self.assertEqual(title, "+2 watching twitch watchmepivot")
        self.assertIn("twitch watchmepivot 3", body)

    async def test_a_fall_chimes_but_posts_no_notification(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        posted: list[tuple[str, str]] = []
        played: list[Path] = []

        await monitor(
            [FakeSource(a, [live(a, 5), live(a, 2)])],
            ChimeConfig(UP, DOWN),
            play=played.append,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(posted, [])
        self.assertEqual(played, [DOWN])  # the fall is still audible

    async def test_a_notification_failure_does_not_stop_the_watcher(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        played: list[Path] = []

        def explode(title: str, body: str) -> None:
            raise NotificationError("banner refused")

        # The warning is the operator's only signal that banners are broken.
        with self.assertLogs("youtube_live_count_chime.monitor", "WARNING") as logs:
            await monitor(
                [FakeSource(a, [live(a, 1), live(a, 2), live(a, 5)])],
                ChimeConfig(UP, DOWN),
                play=played.append,
                notify=explode,
            )

        self.assertEqual(played, [UP, UP])  # both rises still chimed
        self.assertEqual(len(logs.records), 2)  # each failure warned

    async def test_a_failed_poll_leaves_no_stale_count_in_another_channels_digest(
        self,
    ) -> None:
        # A failed poll means "unknown", which is neither "offline" nor "still
        # 7": the rising channel's banner must not publish the blind channel's
        # pre-outage count as though it were current.
        rising = StreamTarget(Platform.YOUTUBE, "rising")
        blind = StreamTarget(Platform.TWITCH, "blind")
        posted: list[tuple[str, str]] = []
        polled = asyncio.Event()

        class _Blind(FakeSource):
            async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
                for snap in self._snaps:
                    yield snap
                polled.set()

        class _Rising(FakeSource):
            async def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
                yield self._snaps[0]
                await polled.wait()  # order the rise after the failed poll
                yield self._snaps[1]

        await monitor(
            [
                _Rising(rising, [live(rising, 1), live(rising, 2)]),
                _Blind(blind, [live(blind, 7), None]),
            ],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(len(posted), 1)
        self.assertIn("twitch blind ?", posted[0][1])


if __name__ == "__main__":
    unittest.main()
