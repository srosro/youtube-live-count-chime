import unittest

from youtube_live_count_chime.digest import render_roster
from youtube_live_count_chime.models import Platform, StreamTarget


TWITCH_A = StreamTarget(Platform.TWITCH, "watchmepivot")
YOUTUBE_A = StreamTarget(Platform.YOUTUBE, "srosrosr")


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
