from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence
import unittest
from unittest.mock import patch

from youtube_live_count_chime.cli import build_sources, main, parse_config
from youtube_live_count_chime.monitor import ChimeConfig
from youtube_live_count_chime.models import StreamSource
from youtube_live_count_chime.twitch import TwitchError


class ParseConfigTests(unittest.TestCase):
    def test_collects_multiple_handles(self) -> None:
        config = parse_config(["-y", "@mkbhd", "-y", "ltt", "-t", "shroud"])

        self.assertEqual(config.youtube, ("@mkbhd", "ltt"))
        self.assertEqual(config.twitch, ("shroud",))

    def test_defaults_have_no_handles(self) -> None:
        config = parse_config([])

        self.assertEqual(config.youtube, ())
        self.assertEqual(config.twitch, ())
        self.assertEqual(config.poll_interval, 5.0)

    def test_rejects_invalid_sound_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.aiff"
            stderr = StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                parse_config(["--up-sound", str(path)])

            self.assertIn(f"not a sound file: {path}", stderr.getvalue())

    def test_rejects_non_positive_poll_interval(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--poll-interval", "0"])


class BuildSourcesTests(unittest.TestCase):
    def test_requires_at_least_one_handle(self) -> None:
        with self.assertRaises(ValueError):
            build_sources(parse_config([]))

    def test_rejects_empty_handle(self) -> None:
        with self.assertRaises(ValueError):
            build_sources(parse_config(["-y", "  "]))

    def test_youtube_only_needs_no_twitch_creds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sources = build_sources(parse_config(["-y", "@mkbhd", "-y", "ltt"]))

        self.assertEqual([source.name for source in sources], ["youtube:mkbhd", "youtube:ltt"])

    def test_deduplicates_repeated_handles(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sources = build_sources(parse_config(["-y", "@mkbhd", "-y", "mkbhd"]))

        self.assertEqual([source.name for source in sources], ["youtube:mkbhd"])

    def test_twitch_without_creds_raises_named_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TwitchError):
                build_sources(parse_config(["-t", "shroud"]))

    def test_twitch_with_creds_builds_a_twitch_source(self) -> None:
        env = {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"}
        with patch.dict(os.environ, env, clear=True):
            sources = build_sources(parse_config(["-y", "@mkbhd", "-t", "Shroud"]))

        self.assertEqual(
            [source.name for source in sources], ["youtube:mkbhd", "twitch:shroud"]
        )


class MainTests(unittest.TestCase):
    def test_no_handles_exits_2(self) -> None:
        self.assertEqual(main([]), 2)

    def test_missing_twitch_creds_exits_2(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(["-t", "shroud"]), 2)

    def test_keyboard_interrupt_stops_cleanly(self) -> None:
        async def fake_monitor(
            sources: Sequence[StreamSource], config: ChimeConfig
        ) -> None:
            raise KeyboardInterrupt

        with patch("youtube_live_count_chime.cli.monitor", fake_monitor):
            self.assertEqual(main(["-y", "@mkbhd"]), 0)

    def test_passes_sources_and_ordered_sounds_to_monitor(self) -> None:
        captured: dict[str, Any] = {}

        async def fake_monitor(
            sources: Sequence[StreamSource], config: ChimeConfig
        ) -> None:
            captured["names"] = [source.name for source in sources]
            captured["config"] = config

        with patch("youtube_live_count_chime.cli.monitor", fake_monitor):
            self.assertEqual(main(["-y", "@mkbhd"]), 0)

        self.assertEqual(captured["names"], ["youtube:mkbhd"])
        self.assertEqual(captured["config"].up_sound.name, "Glass.aiff")
        self.assertEqual(captured["config"].down_sound.name, "Basso.aiff")


if __name__ == "__main__":
    unittest.main()
