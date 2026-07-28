import unittest
from unittest.mock import AsyncMock, patch

from youtube_live_count_chime.models import (
    Platform,
    SourceFetchError,
    StreamSnapshot,
    StreamTarget,
    normalize_handle,
    poll_snapshots,
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


class PollSnapshotsTests(unittest.IsolatedAsyncioTestCase):
    target = StreamTarget(Platform.YOUTUBE, "a")

    async def test_warns_once_per_outage_and_rearms_after_a_success(self) -> None:
        # Three consecutive failures are one outage, so they warn once and
        # mark unknown once; the success in between re-arms both, so the
        # second outage warns and marks again.
        live = StreamSnapshot(self.target, "vid", 7)
        outcomes: list[StreamSnapshot | SourceFetchError] = [
            SourceFetchError("first down"),
            SourceFetchError("still down"),
            SourceFetchError("still down"),
            live,
            SourceFetchError("down again"),
            live,
        ]
        script = iter(outcomes)

        def fetch() -> StreamSnapshot:
            outcome = next(script, None)
            if outcome is None:
                self.fail("poll_snapshots polled more times than the script allows")
            if isinstance(outcome, SourceFetchError):
                raise outcome
            return outcome

        with (
            patch("youtube_live_count_chime.models.asyncio.sleep", new_callable=AsyncMock),
            self.assertLogs("youtube_live_count_chime", "WARNING") as logs,
        ):
            seen: list[StreamSnapshot | None] = []
            async for snapshot in poll_snapshots("youtube:a", fetch):
                seen.append(snapshot)
                if seen.count(live) == 2:  # both outages are behind us
                    break

        # Entering an outage is reported once, not silently skipped: the
        # consumer must learn the count is unknown rather than keep the stale
        # one. Repeating it every poll would say nothing new — the consumer's
        # response is idempotent — so the marker tracks the warning exactly.
        self.assertEqual(seen, [None, live, None, live])
        warnings = [record.getMessage() for record in logs.records]
        self.assertEqual(len(warnings), 2)
        self.assertIn("could not fetch youtube:a: first down", warnings[0])
        self.assertIn("could not fetch youtube:a: down again", warnings[1])


if __name__ == "__main__":
    unittest.main()
