import unittest

from youtube_live_count_chime.models import (
    Platform,
    StreamSnapshot,
    StreamTarget,
    normalize_handle,
)


class NormalizeHandleTests(unittest.TestCase):
    def test_strips_and_lowercases_and_keeps_allowed_punctuation(self) -> None:
        cases = {
            "@MKBHD": "mkbhd",
            "  ltt ": "ltt",
            "  @mkbhd": "mkbhd",  # outer strip, before dropping @
            "@ mkbhd ": "mkbhd",  # inner strip, after dropping @
            "Shr_oud": "shr_oud",
            "mk.bhd-1": "mk.bhd-1",  # ., -, digits are all allowed
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_handle(value), expected)

    def test_rejects_empty_or_url_unsafe_handles(self) -> None:
        for bad in ("", "@", "   ", "a b", "mkbhd/videos", "@@mkbhd", "x?y"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_handle(bad)

    def test_bounds_handle_length(self) -> None:
        self.assertEqual(normalize_handle("a" * 30), "a" * 30)
        with self.assertRaises(ValueError):
            normalize_handle("a" * 31)


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


if __name__ == "__main__":
    unittest.main()
