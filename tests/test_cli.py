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
from youtube_live_count_chime.twitch import TwitchError, TwitchSource


class _CliTestCase(unittest.TestCase):
    """Base case with real temporary sound files.

    argparse validates the macOS default sound paths against the filesystem,
    so every parse_config/main call routes its sounds through real temp files
    to keep the suite hermetic (and runnable off macOS).
    """

    _tmp: "TemporaryDirectory[str]"
    _up: Path
    _down: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls._up = Path(cls._tmp.name) / "up.aiff"
        cls._down = Path(cls._tmp.name) / "down.aiff"
        cls._up.write_bytes(b"u")
        cls._down.write_bytes(b"d")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def argv(self, *extra: str) -> list[str]:
        return ["--up-sound", str(self._up), "--down-sound", str(self._down), *extra]


class ParseConfigTests(_CliTestCase):
    def test_collects_multiple_handles(self) -> None:
        config = parse_config(self.argv("-y", "@mkbhd", "-y", "ltt", "-t", "shroud"))

        self.assertEqual(config.youtube, ("@mkbhd", "ltt"))
        self.assertEqual(config.twitch, ("shroud",))

    def test_defaults_have_no_handles(self) -> None:
        config = parse_config(self.argv())

        self.assertEqual(config.youtube, ())
        self.assertEqual(config.twitch, ())

    def test_default_sounds_wire_glass_up_basso_down(self) -> None:
        # Patch the filesystem check so the real default paths pass off macOS,
        # pinning that a rise plays Glass and a fall plays Basso (not swapped).
        with patch("youtube_live_count_chime.cli._sound_file", side_effect=Path):
            config = parse_config([])

        self.assertEqual(config.up_sound.name, "Glass.aiff")
        self.assertEqual(config.down_sound.name, "Basso.aiff")

    def test_rejects_invalid_sound_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.aiff"
            stderr = StringIO()

            # --down-sound is a real file so only --up-sound is under test,
            # independent of argparse's default-vs-supplied validation order.
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                parse_config(["--up-sound", str(path), "--down-sound", str(self._down)])

            self.assertIn(f"not a sound file: {path}", stderr.getvalue())


class BuildSourcesTests(_CliTestCase):
    def test_requires_at_least_one_handle(self) -> None:
        with self.assertRaises(ValueError):
            build_sources(parse_config(self.argv()))

    def test_rejects_empty_handle(self) -> None:
        with self.assertRaises(ValueError):
            build_sources(parse_config(self.argv("-y", "  ")))

    def test_youtube_only_needs_no_twitch_creds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sources = build_sources(parse_config(self.argv("-y", "@mkbhd", "-y", "ltt")))

        self.assertEqual(
            [source.name for source in sources], ["youtube:mkbhd", "youtube:ltt"]
        )

    def test_deduplicates_handles_across_prefix_and_case(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            sources = build_sources(parse_config(self.argv("-y", "@MKBHD", "-y", "mkbhd")))

        self.assertEqual([source.name for source in sources], ["youtube:mkbhd"])

    def test_twitch_without_creds_raises_named_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TwitchError):
                build_sources(parse_config(self.argv("-t", "shroud")))

    def test_twitch_with_creds_builds_a_twitch_source(self) -> None:
        env = {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"}
        with patch.dict(os.environ, env, clear=True):
            sources = build_sources(parse_config(self.argv("-y", "@mkbhd", "-t", "Shroud")))

        self.assertEqual(
            [source.name for source in sources], ["youtube:mkbhd", "twitch:shroud"]
        )

    def test_twitch_logins_share_one_client(self) -> None:
        env = {"TWITCH_CLIENT_ID": "id", "TWITCH_CLIENT_SECRET": "secret"}
        with patch.dict(os.environ, env, clear=True):
            sources = build_sources(parse_config(self.argv("-t", "a", "-t", "b")))

        twitch = [source for source in sources if isinstance(source, TwitchSource)]
        self.assertEqual(len(twitch), 2)
        self.assertEqual(len({id(source.client) for source in twitch}), 1)


class MainTests(_CliTestCase):
    def test_no_handles_exits_2(self) -> None:
        self.assertEqual(main(self.argv()), 2)

    def test_check_validates_and_exits_zero_without_watching(self) -> None:
        # Patch monitor to fail loudly if reached, so a regression that lets
        # --check fall through can't hang the suite on live network calls.
        async def fail_if_called(
            sources: Sequence[StreamSource], config: ChimeConfig
        ) -> None:
            raise AssertionError("--check must not reach the monitor")

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("youtube_live_count_chime.cli.monitor", fail_if_called),
        ):
            self.assertEqual(main(self.argv("--check", "-y", "@mkbhd")), 0)

    def test_check_fails_on_missing_twitch_creds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(self.argv("--check", "-t", "shroud")), 2)

    def test_missing_twitch_creds_exits_2(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(self.argv("-t", "shroud")), 2)

    def test_keyboard_interrupt_stops_cleanly(self) -> None:
        async def fake_monitor(
            sources: Sequence[StreamSource], config: ChimeConfig
        ) -> None:
            raise KeyboardInterrupt

        with patch("youtube_live_count_chime.cli.monitor", fake_monitor):
            self.assertEqual(main(self.argv("-y", "@mkbhd")), 0)

    def test_unexpected_error_exits_1(self) -> None:
        async def boom(sources: Sequence[StreamSource], config: ChimeConfig) -> None:
            raise RuntimeError("kaboom")

        with patch("youtube_live_count_chime.cli.monitor", boom):
            with self.assertLogs("youtube_live_count_chime.cli", "ERROR"):
                self.assertEqual(main(self.argv("-y", "@mkbhd")), 1)

    def test_passes_sources_and_ordered_sounds_to_monitor(self) -> None:
        captured: dict[str, Any] = {}

        async def fake_monitor(
            sources: Sequence[StreamSource], config: ChimeConfig
        ) -> None:
            captured["names"] = [source.name for source in sources]
            captured["config"] = config

        with patch("youtube_live_count_chime.cli.monitor", fake_monitor):
            self.assertEqual(main(self.argv("-y", "@mkbhd")), 0)

        self.assertEqual(captured["names"], ["youtube:mkbhd"])
        self.assertEqual(captured["config"].up_sound.name, "up.aiff")
        self.assertEqual(captured["config"].down_sound.name, "down.aiff")


if __name__ == "__main__":
    unittest.main()
