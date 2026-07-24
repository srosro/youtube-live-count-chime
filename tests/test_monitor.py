import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget
from youtube_live_count_chime.monitor import ChimeConfig, monitor


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
    name = "boom"

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        raise RuntimeError("kaboom")
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

    async def test_unexpected_source_error_propagates(self) -> None:
        healthy = StreamTarget(Platform.YOUTUBE, "a")
        with self.assertRaises(RuntimeError):
            await monitor(
                [
                    ExplodingSource(),
                    FakeSource("youtube:a", [live(healthy, 1), live(healthy, 4)]),
                ],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
            )


if __name__ == "__main__":
    unittest.main()
