import unittest

from youtube_live_count_chime.digest import describe_rise, render_roster
from youtube_live_count_chime.models import Platform, StreamTarget


TWITCH_A = StreamTarget(Platform.TWITCH, "watchmepivot")
YOUTUBE_A = StreamTarget(Platform.YOUTUBE, "srosrosr")


class DescribeRiseTests(unittest.TestCase):
    def test_counts_viewers_with_the_matching_plural(self) -> None:
        # This sentence is read aloud, so the plural has to be right in both
        # directions — "1 new viewers" is audibly wrong.
        for delta, expected in ((1, "1 new viewer"), (2, "2 new viewers")):
            with self.subTest(delta=delta):
                self.assertEqual(
                    describe_rise(TWITCH_A, delta),
                    f"{expected} on twitch watchmepivot",
                )

    def test_a_non_rise_is_a_programming_error_rather_than_a_worded_line(self) -> None:
        # Only a rise is narrated, so a fall reaching here would word nonsense
        # ("-3 new viewer on ...") for a sentence nobody should be building.
        for delta in (0, -3):
            with self.subTest(delta=delta):
                with self.assertRaises(AssertionError):
                    describe_rise(TWITCH_A, delta)


class RenderRosterTests(unittest.TestCase):
    def test_renders_every_channel_in_fixed_order_including_unchanged(self) -> None:
        self.assertEqual(
            render_roster((TWITCH_A, YOUTUBE_A), {YOUTUBE_A: 4, TWITCH_A: 2}),
            "twitch watchmepivot 2 · youtube srosrosr 4",
        )

    def test_an_unpolled_channel_is_rendered_apart_from_an_offline_one(self) -> None:
        # A fixed-shape body is the whole point: a channel never disappears.
        # But the first notification can fire before every channel's first poll
        # has come back, and a channel nobody has looked at yet — or one whose
        # poll failed, dropping its count — must not be reported offline.
        live = StreamTarget(Platform.TWITCH, "live")
        off = StreamTarget(Platform.TWITCH, "off")
        unpolled = StreamTarget(Platform.YOUTUBE, "unpolled")

        self.assertEqual(
            render_roster((live, off, unpolled), {live: 3, off: None}),
            "twitch live 3 · twitch off offline · youtube unpolled ?",
        )


if __name__ == "__main__":
    unittest.main()
