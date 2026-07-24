from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from youtube_live_count_chime.cli import build_sources, parse_config
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
        with self.assertRaises(SystemExit):
            build_sources(parse_config([]))

    def test_youtube_only_needs_no_twitch_creds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sources = build_sources(parse_config(["-y", "@mkbhd", "-y", "ltt"]))

        self.assertEqual([source.name for source in sources], ["youtube:mkbhd", "youtube:ltt"])

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


if __name__ == "__main__":
    unittest.main()
