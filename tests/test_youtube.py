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

    def test_ignores_unrelated_count_before_live_renderer(self) -> None:
        page_html = (
            '{"metadata":{"originalViewCount":"999"},'
            '"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"123"}}'
        )

        self.assertEqual(parse_viewer_count(page_html), 123)

    def test_skips_unparseable_renderer_before_valid_live_renderer(self) -> None:
        page_html = (
            '"videoViewCountRenderer":not-json,'
            '"videoViewCountRenderer":'
            '{"isLive":true,"originalViewCount":"123"}'
        )

        self.assertEqual(parse_viewer_count(page_html), 123)

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


if __name__ == "__main__":
    unittest.main()
