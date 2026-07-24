import unittest

from youtube_live_count_chime.models import (
    CountTracker,
    Platform,
    StreamSnapshot,
    StreamTarget,
    ViewerChange,
    normalize_handle,
)


class NormalizeHandleTests(unittest.TestCase):
    def test_strips_prefix_whitespace_and_case(self) -> None:
        cases = {"@MKBHD": "mkbhd", "  ltt ": "ltt", "Shr_oud": "shr_oud"}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_handle(value), expected)

    def test_rejects_empty_or_url_unsafe_handles(self) -> None:
        for bad in ("", "@", "   ", "a b", "mkbhd/videos", "@@mkbhd", "x?y", "a" * 40):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_handle(bad)


class StreamSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = StreamTarget(Platform.YOUTUBE, "watchmepivot")

    def test_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            StreamSnapshot(self.target, "video-a", -1)

    def test_rejects_half_live_snapshot(self) -> None:
        cases = (("video-a", None), (None, 1))
        for stream_id, viewers in cases:
            with self.subTest(stream_id=stream_id, viewers=viewers), self.assertRaises(
                ValueError
            ):
                StreamSnapshot(self.target, stream_id, viewers)


class CountTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = StreamTarget(Platform.YOUTUBE, "watchmepivot")
        self.tracker = CountTracker()

    def test_baselines_unchanged_and_new_stream_observations(self) -> None:
        self.assertIsNone(
            self.tracker.observe(StreamSnapshot(self.target, "video-a", 1))
        )
        self.assertIsNone(
            self.tracker.observe(StreamSnapshot(self.target, "video-a", 1))
        )
        self.assertIsNone(
            self.tracker.observe(StreamSnapshot(self.target, "video-b", 7))
        )

    def test_reports_an_upward_change_on_the_same_stream(self) -> None:
        self.tracker.observe(StreamSnapshot(self.target, "video-a", 1))

        change = self.tracker.observe(StreamSnapshot(self.target, "video-a", 3))

        self.assertEqual(change, ViewerChange(self.target, "video-a", 1, 3))
        assert change is not None
        self.assertEqual(change.direction, "up")

    def test_reports_a_downward_change_on_the_same_stream(self) -> None:
        self.tracker.observe(StreamSnapshot(self.target, "video-a", 3))

        change = self.tracker.observe(StreamSnapshot(self.target, "video-a", 1))

        self.assertEqual(change, ViewerChange(self.target, "video-a", 3, 1))
        assert change is not None
        self.assertEqual(change.direction, "down")

    def test_offline_snapshot_clears_the_current_stream(self) -> None:
        self.tracker.observe(StreamSnapshot(self.target, "video-a", 1))

        self.assertIsNone(self.tracker.observe(StreamSnapshot.offline(self.target)))
        self.assertIsNone(self.tracker.current(self.target))


if __name__ == "__main__":
    unittest.main()
