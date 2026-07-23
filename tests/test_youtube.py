from contextlib import nullcontext
from http.client import IncompleteRead
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from youtube_live_count_chime.youtube import (
    ViewerCountError,
    fetch_viewer_count,
    parse_viewer_count,
)


FIXTURE = Path(__file__).parent / "fixtures" / "live_page.html"


class ParseViewerCountTests(unittest.TestCase):
    def test_extracts_original_live_viewer_count(self) -> None:
        page_html = FIXTURE.read_text(encoding="utf-8")

        self.assertEqual(parse_viewer_count(page_html), 1)

    def test_extracts_synthetic_live_counts(self) -> None:
        cases = (
            (
                "multiple-digits",
                '{"videoViewCountRenderer":'
                '{"isLive":true,"originalViewCount":"12345"}}',
                12_345,
            ),
            (
                "unrelated-count-first",
                '{"metadata":{"originalViewCount":"999"},'
                '"videoViewCountRenderer":'
                '{"isLive":true,"originalViewCount":"123"}}',
                123,
            ),
            (
                "unparseable-renderer-first",
                '"videoViewCountRenderer":not-json,'
                '"videoViewCountRenderer":'
                '{"isLive":true,"originalViewCount":"123"}',
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


class FetchViewerCountTests(unittest.TestCase):
    def test_normalizes_incomplete_response_to_viewer_count_error(self) -> None:
        response = Mock()
        response.read.side_effect = IncompleteRead(b"partial", 100)

        with (
            patch(
                "youtube_live_count_chime.youtube.urlopen",
                return_value=nullcontext(response),
            ),
            self.assertRaisesRegex(
                ViewerCountError,
                "could not fetch the livestream page",
            ),
        ):
            fetch_viewer_count("https://example.test/live")


if __name__ == "__main__":
    unittest.main()
