from pathlib import Path
import unittest

from youtube_live_count_chime.youtube import ViewerCountError, parse_viewer_count


FIXTURE = Path(__file__).parent / "fixtures" / "live_page.html"


class ParseViewerCountTests(unittest.TestCase):
    def test_extracts_original_live_viewer_count(self) -> None:
        page_html = FIXTURE.read_text(encoding="utf-8")

        self.assertEqual(parse_viewer_count(page_html), 1)

    def test_accepts_viewer_counts_with_multiple_digits(self) -> None:
        page_html = (
            '{"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"12345"}}'
        )

        self.assertEqual(parse_viewer_count(page_html), 12_345)

    def test_rejects_non_live_renderer(self) -> None:
        page_html = (
            '{"videoViewCountRenderer":'
            '{"isLive":false,"originalViewCount":"123"}}'
        )

        with self.assertRaisesRegex(
            ViewerCountError, "live viewer count was not found"
        ):
            parse_viewer_count(page_html)

    def test_ignores_unrelated_count_before_live_renderer(self) -> None:
        page_html = (
            '{"metadata":{"originalViewCount":"999"},'
            '"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"123"}}'
        )

        self.assertEqual(parse_viewer_count(page_html), 123)

    def test_rejects_page_without_live_viewer_count(self) -> None:
        with self.assertRaisesRegex(
            ViewerCountError, "live viewer count was not found"
        ):
            parse_viewer_count("<html>not a livestream count</html>")

    def test_rejects_non_numeric_live_viewer_count(self) -> None:
        page_html = (
            '{"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"many"}}'
        )

        with self.assertRaisesRegex(
            ViewerCountError, "live viewer count was not found"
        ):
            parse_viewer_count(page_html)


if __name__ == "__main__":
    unittest.main()
