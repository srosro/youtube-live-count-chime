from http.client import BadStatusLine, IncompleteRead
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from youtube_live_count_chime.youtube import (
    ViewerCountError,
    YouTubeLivePage,
    YouTubeSource,
    fetch_live_page,
    parse_live_page,
    parse_viewer_count,
)


FIXTURE = Path(__file__).parent / "fixtures" / "live_page.html"


class ParseViewerCountTests(unittest.TestCase):
    def test_extracts_live_counts(self) -> None:
        key = '"videoViewCountRenderer":'
        live = '{"isLive":true,"originalViewCount":"123"}'
        cases = (
            ("fixture", FIXTURE.read_text(encoding="utf-8"), 1),
            (
                "multiple-digits",
                key + '{"isLive":true,"originalViewCount":"12345"}',
                12_345,
            ),
            (
                "unrelated-count-first",
                '"originalViewCount":"999",' + key + live,
                123,
            ),
            (
                "unparseable-renderer-first",
                key + "not-json," + key + live,
                123,
            ),
        )
        for name, page_html, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(parse_viewer_count(page_html), expected)

    def test_rejects_invalid_renderers(self) -> None:
        cases = (
            (
                "non-live",
                '{"videoViewCountRenderer":'
                '{"isLive":false,"originalViewCount":"123"}}',
            ),
            ("missing", "<html>not a livestream count</html>"),
            (
                "non-numeric",
                '{"videoViewCountRenderer":'
                '{"isLive":true,"originalViewCount":"many"}}',
            ),
        )
        for name, page_html in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                ViewerCountError,
                "live viewer count was not found",
            ):
                parse_viewer_count(page_html)


class ParseLivePageTests(unittest.TestCase):
    def test_extracts_canonical_live_video_and_viewers(self) -> None:
        page_html = """
        <link rel="canonical"
              href="https://www.youtube.com/watch?v=afTqXQQhYrY">
        <script>
        {"videoViewCountRenderer":
         {"isLive":true,"originalViewCount":"12"}}
        </script>
        """

        page = parse_live_page(
            page_html, "https://www.youtube.com/@watchmepivot/live"
        )

        self.assertEqual(page.video_id, "afTqXQQhYrY")
        self.assertEqual(page.viewers, 12)

    def test_uses_requested_watch_url_when_canonical_is_missing(self) -> None:
        page = parse_live_page(
            '{"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"12"}}',
            "https://www.youtube.com/watch?v=afTqXQQhYrY",
        )

        self.assertEqual(page, YouTubeLivePage(video_id="afTqXQQhYrY", viewers=12))

    def test_accepts_video_id_ending_in_dash(self) -> None:
        # An 11-char ID whose final character is '-' must still match — the
        # trailing boundary in the pattern can't rely on a word boundary there.
        page = parse_live_page(
            '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghij-">'
            '{"videoViewCountRenderer":{"isLive":true,"originalViewCount":"5"}}',
            "https://www.youtube.com/@x/live",
        )

        self.assertEqual(page.video_id, "abcdefghij-")

    def test_accepts_video_id_followed_by_query_params(self) -> None:
        # The old pattern's `&` alternative existed for this; the lookahead
        # passes it (`&` is outside the ID character class).
        page = parse_live_page(
            '<link rel="canonical" href="https://www.youtube.com/watch?v=afTqXQQhYrY&t=42">'
            '{"videoViewCountRenderer":{"isLive":true,"originalViewCount":"5"}}',
            "https://www.youtube.com/@x/live",
        )

        self.assertEqual(page.video_id, "afTqXQQhYrY")

    def test_rejects_truncatable_over_length_video_id(self) -> None:
        # A 12th char of '-' must not truncate to a bogus 11-char ID: the old
        # `\b` pattern matched a boundary before the '-' and returned
        # "abcdefghijk" ('_' is a word char, so the old pattern already
        # rejected that variant); the lookahead rejects '-' too.
        with self.assertRaisesRegex(ViewerCountError, "live video ID was not found"):
            parse_live_page(
                '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk-x">'
                '{"videoViewCountRenderer":{"isLive":true,"originalViewCount":"5"}}',
                "https://www.youtube.com/@x/live",
            )

    def test_rejects_page_without_a_live_video_id(self) -> None:
        with self.assertRaisesRegex(ViewerCountError, "live video ID was not found"):
            parse_live_page(
                '{"videoViewCountRenderer":'
                '{"isLive":true,"originalViewCount":"12"}}',
                "https://www.youtube.com/@watchmepivot/live",
            )


class FetchLivePageTests(unittest.TestCase):
    def test_normalizes_http_client_failures_to_viewer_count_error(self) -> None:
        # IncompleteRead and BadStatusLine are both http.client.HTTPException
        # subtypes; the latter is not an OSError, so it needs the widened catch.
        for error in (IncompleteRead(b"partial", 100), BadStatusLine("garbage")):
            with self.subTest(error=type(error).__name__):
                with (
                    patch(
                        "youtube_live_count_chime.youtube.urlopen",
                        side_effect=error,
                    ),
                    self.assertRaisesRegex(
                        ViewerCountError,
                        "could not fetch the livestream page",
                    ),
                ):
                    fetch_live_page("https://example.test/live")


class YouTubeSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_for_handle_yields_a_snapshot_for_the_normalized_target(self) -> None:
        page = YouTubeLivePage(video_id="afTqXQQhYrY", viewers=12)
        with patch(
            "youtube_live_count_chime.youtube.fetch_live_page", return_value=page
        ) as fetcher:
            source = YouTubeSource.for_handle("@watchmepivot")

            snapshot = await anext(source.snapshots())

        fetcher.assert_called_once_with("https://www.youtube.com/@watchmepivot/live")
        self.assertEqual(snapshot.target.key, "youtube:watchmepivot")
        self.assertEqual(snapshot.stream_id, "afTqXQQhYrY")
        self.assertEqual(snapshot.viewers, 12)

    def test_for_handle_normalizes_prefix_and_case(self) -> None:
        self.assertEqual(YouTubeSource.for_handle("@MKBHD").name, "youtube:mkbhd")
        self.assertEqual(YouTubeSource.for_handle("  ltt ").name, "youtube:ltt")

    def test_for_handle_rejects_unusable_handle(self) -> None:
        # Full accept/reject matrix lives in test_models.NormalizeHandleTests;
        # here we only confirm for_handle delegates to it.
        with self.assertRaises(ValueError):
            YouTubeSource.for_handle("mkbhd/videos")

    async def test_snapshots_warns_and_retries_after_a_fetch_error(self) -> None:
        page = YouTubeLivePage("vid", 7)
        with (
            patch(
                "youtube_live_count_chime.youtube.fetch_live_page",
                side_effect=[ViewerCountError("boom"), page],
            ) as fetcher,
            patch(
                "youtube_live_count_chime.models.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            source = YouTubeSource.for_handle("@x")

            with self.assertLogs("youtube_live_count_chime", "WARNING"):
                snapshot = await anext(source.snapshots())

        self.assertEqual(fetcher.call_count, 2)
        self.assertEqual(snapshot.viewers, 7)


if __name__ == "__main__":
    unittest.main()
