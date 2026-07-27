import io
import unittest
from collections.abc import AsyncIterator
from contextlib import redirect_stdout
from pathlib import Path

from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget
from youtube_live_count_chime.monitor import ChimeConfig, monitor
from youtube_live_count_chime.notify import NotificationError
from youtube_live_count_chime.sounds import SoundPlaybackError


UP = Path("/System/Library/Sounds/Glass.aiff")
DOWN = Path("/System/Library/Sounds/Basso.aiff")


class FakeSource:
    def __init__(self, name: str, snaps: list[StreamSnapshot]) -> None:
        self.name = name
        self._snaps = snaps

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        for snap in self._snaps:
            yield snap


class ExplodingSource:
    name = "channel-x"

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        raise RuntimeError("upstream failure")
        yield  # pragma: no cover - marks this an async generator


def live(target: StreamTarget, viewers: int) -> StreamSnapshot:
    return StreamSnapshot(target, "s1", viewers)


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_chimes_up_and_down_but_not_on_baseline_or_no_change(self) -> None:
        target = StreamTarget(Platform.YOUTUBE, "a")
        snaps = [live(target, 5), live(target, 5), live(target, 9), live(target, 2)]
        played: list[Path] = []
        await monitor(
            [FakeSource("youtube:a", snaps)],
            ChimeConfig(UP, DOWN),
            play=played.append,
        )
        self.assertEqual(played, [UP, DOWN])  # 5->5 silent, 5->9 up, 9->2 down

    async def test_tracks_each_target_independently(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")
        b = StreamTarget(Platform.TWITCH, "b")
        played: list[Path] = []
        await monitor(
            [
                FakeSource("youtube:a", [live(a, 1), live(a, 2)]),
                FakeSource("twitch:b", [live(b, 100), live(b, 100)]),
            ],
            ChimeConfig(UP, DOWN),
            play=played.append,
        )
        self.assertEqual(played, [UP])  # a 1->2 up; b unchanged; baselines silent

    async def test_prints_the_transition_in_order(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "chan")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            await monitor(
                [FakeSource("youtube:chan", [live(a, 5), live(a, 9)])],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
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
                    "youtube:a",
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
                [FakeSource("youtube:a", [live(a, 1), live(a, 2), live(a, 3)])],
                ChimeConfig(UP, DOWN),
                play=boom,
            )
        self.assertEqual(len(logs.records), 2)

    async def test_unexpected_source_error_stops_the_watcher(self) -> None:
        healthy = StreamTarget(Platform.YOUTUBE, "a")
        with self.assertRaises(ExceptionGroup) as ctx:
            await monitor(
                [
                    ExplodingSource(),
                    FakeSource("youtube:a", [live(healthy, 1), live(healthy, 4)]),
                ],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
            )
        # Pin the full wrapper message and the preserved cause: this fails if
        # the naming wrapper or its `from error` chaining is dropped.
        messages = [str(error) for error in ctx.exception.exceptions]
        self.assertIn("source channel-x failed", messages)
        causes = [type(error.__cause__) for error in ctx.exception.exceptions]
        self.assertIn(RuntimeError, causes)


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rise_notifies_with_named_arrival_and_full_roster(self) -> None:
        a = StreamTarget(Platform.TWITCH, "watchmepivot")
        b = StreamTarget(Platform.YOUTUBE, "srosrosr")
        posted: list[tuple[str, str]] = []

        class Namer:
            async def arrivals(self, target: StreamTarget, stream_id: str) -> tuple[str, ...]:
                return ("joe_doe",)

        await monitor(
            [
                FakeSource("twitch:watchmepivot", [live(a, 1), live(a, 2)]),
                FakeSource("youtube:srosrosr", [live(b, 7)]),
            ],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
            namer=Namer(),
        )

        self.assertEqual(len(posted), 1)
        title, body = posted[0]
        self.assertEqual(title, "joe_doe is now watching twitch watchmepivot")
        self.assertIn("twitch watchmepivot 2", body)
        self.assertIn("youtube srosrosr 7", body)

    async def test_fall_chimes_but_never_notifies(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        posted: list[tuple[str, str]] = []
        await monitor(
            [FakeSource("twitch:chan", [live(a, 5), live(a, 2)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(posted, [])

    async def test_rise_without_a_namer_falls_back_to_a_bare_count(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "chan")
        posted: list[tuple[str, str]] = []
        await monitor(
            [FakeSource("youtube:chan", [live(a, 1), live(a, 3)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(posted[0][0], "+2 watching youtube chan")

    async def test_a_notification_failure_does_not_stop_the_watcher(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        played: list[Path] = []

        def explode(title: str, body: str) -> None:
            raise NotificationError("banner refused")

        await monitor(
            [FakeSource("twitch:chan", [live(a, 1), live(a, 2), live(a, 5)])],
            ChimeConfig(UP, DOWN),
            play=played.append,
            notify=explode,
        )

        self.assertEqual(played, [UP, UP])  # both rises still chimed


if __name__ == "__main__":
    unittest.main()
