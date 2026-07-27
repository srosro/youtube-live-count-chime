import unittest

from youtube_live_count_chime.digest import Roster, render_title
from youtube_live_count_chime.models import Platform, StreamTarget


TWITCH_A = StreamTarget(Platform.TWITCH, "watchmepivot")
YOUTUBE_A = StreamTarget(Platform.YOUTUBE, "srosrosr")


class RosterTests(unittest.TestCase):
    def test_renders_every_channel_in_fixed_order_including_unchanged(self) -> None:
        roster = Roster(("twitch:watchmepivot", "youtube:srosrosr"))
        roster.update("youtube:srosrosr", 4)
        roster.update("twitch:watchmepivot", 2)

        self.assertEqual(
            roster.render(), "twitch watchmepivot 2 · youtube srosrosr 4"
        )

    def test_renders_offline_and_never_seen_channels_rather_than_omitting_them(
        self,
    ) -> None:
        # A fixed-shape body is the whole point: a channel never disappears.
        roster = Roster(("twitch:a", "twitch:b"))
        roster.update("twitch:a", None)

        self.assertEqual(roster.render(), "twitch a offline · twitch b offline")


class RenderTitleTests(unittest.TestCase):
    def test_title_for_each_naming_case(self) -> None:
        cases: tuple[tuple[list[str], int, str], ...] = (
            ([], 1, "+1 watching twitch watchmepivot"),
            ([], 3, "+3 watching twitch watchmepivot"),
            (["joe_doe"], 1, "joe_doe is now watching twitch watchmepivot"),
            (
                ["joe_doe", "pixel"],
                2,
                "joe_doe, pixel are now watching twitch watchmepivot",
            ),
            (
                ["a", "b", "c"],
                3,
                "a, b, c are now watching twitch watchmepivot",
            ),
            (
                ["a", "b", "c", "d"],
                4,
                "a, b, c and 1 more are now watching twitch watchmepivot",
            ),
            (
                ["a", "b", "c", "d", "e"],
                5,
                "a, b, c and 2 more are now watching twitch watchmepivot",
            ),
            # The roster is a lossy proxy, so names and delta routinely
            # disagree; delta is authoritative for how many arrived.
            # More names than the rise: never claim more arrivals than that.
            (["joe_doe", "pixel"], 1, "joe_doe is now watching twitch watchmepivot"),
            (
                ["a", "b", "c", "d"],
                2,
                "a, b are now watching twitch watchmepivot",
            ),
            # Fewer names than the rise: the remainder is carried, not implied away.
            (
                ["joe_doe"],
                5,
                "joe_doe and 4 more are now watching twitch watchmepivot",
            ),
            (
                ["a", "b"],
                3,
                "a, b and 1 more are now watching twitch watchmepivot",
            ),
            (
                ["a", "b", "c"],
                9,
                "a, b, c and 6 more are now watching twitch watchmepivot",
            ),
        )
        for names, delta, expected in cases:
            with self.subTest(names=names, delta=delta):
                self.assertEqual(render_title(TWITCH_A, delta, names), expected)

    def test_youtube_titles_are_always_unnamed(self) -> None:
        self.assertEqual(
            render_title(YOUTUBE_A, 1, []), "+1 watching youtube srosrosr"
        )


if __name__ == "__main__":
    unittest.main()
