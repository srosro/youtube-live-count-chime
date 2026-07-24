from http.client import IncompleteRead
from pathlib import Path
import unittest
from unittest.mock import patch

from youtube_live_count_chime.youtube import (
    ViewerCountError,
    YouTubeLivePage,
    YouTubeSource,
    fetch_live_page,
    fetch_viewer_count,
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
        self.assertEqual(page.url, "https://www.youtube.com/watch?v=afTqXQQhYrY")
        self.assertEqual(page.viewers, 12)

    def test_uses_requested_watch_url_when_canonical_is_missing(self) -> None:
        page = parse_live_page(
            '{"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"12"}}',
            "https://www.youtube.com/watch?v=afTqXQQhYrY",
        )

        self.assertEqual(
            page,
            YouTubeLivePage(
                video_id="afTqXQQhYrY",
                url="https://www.youtube.com/watch?v=afTqXQQhYrY",
                viewers=12,
            ),
        )

    def test_rejects_page_without_a_live_video_id(self) -> None:
        with self.assertRaisesRegex(ViewerCountError, "live video ID was not found"):
            parse_live_page(
                '{"videoViewCountRenderer":'
                '{"isLive":true,"originalViewCount":"12"}}',
                "https://www.youtube.com/@watchmepivot/live",
            )


class FetchViewerCountTests(unittest.TestCase):
    def test_normalizes_incomplete_response_to_viewer_count_error(self) -> None:
        with (
            patch(
                "youtube_live_count_chime.youtube.urlopen",
                side_effect=IncompleteRead(b"partial", 100),
            ),
            self.assertRaisesRegex(
                ViewerCountError,
                "could not fetch the livestream page",
            ),
        ):
            fetch_viewer_count("https://example.test/live")


class YouTubeSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_for_handle_yields_a_snapshot_for_the_normalized_target(self) -> None:
        page = YouTubeLivePage(
            video_id="afTqXQQhYrY",
            url="https://www.youtube.com/watch?v=afTqXQQhYrY",
            viewers=12,
        )
        with patch(
            "youtube_live_count_chime.youtube.fetch_live_page", return_value=page
        ) as fetcher:
            source = YouTubeSource.for_handle("@watchmepivot")

            snapshot = await anext(source.snapshots())

        fetcher.assert_called_once_with("https://www.youtube.com/@watchmepivot/live")
        self.assertEqual(snapshot.target.key, "youtube:watchmepivot")
        self.assertEqual(snapshot.stream_id, "afTqXQQhYrY")
        self.assertEqual(snapshot.viewers, 12)
        self.assertEqual(snapshot.url, "https://www.youtube.com/watch?v=afTqXQQhYrY")

    def test_for_handle_normalizes_prefix_and_case(self) -> None:
        self.assertEqual(YouTubeSource.for_handle("@MKBHD").name, "youtube:mkbhd")
        self.assertEqual(YouTubeSource.for_handle("  ltt ").name, "youtube:ltt")

    def test_for_handle_rejects_unusable_handles(self) -> None:
        for bad in ("@", "   ", "a b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                YouTubeSource.for_handle(bad)

    async def test_snapshots_warns_and_retries_after_a_fetch_error(self) -> None:
        page = YouTubeLivePage("vid", "https://www.youtube.com/watch?v=vid", 7)
        with patch(
            "youtube_live_count_chime.youtube.fetch_live_page",
            side_effect=[ViewerCountError("boom"), page],
        ) as fetcher:
            source = YouTubeSource.for_handle("@x", poll_interval=0.0)

            snapshot = await anext(source.snapshots())

        self.assertEqual(fetcher.call_count, 2)  # first fetch failed, retried
        self.assertEqual(snapshot.viewers, 7)


if __name__ == "__main__":
    unittest.main()
