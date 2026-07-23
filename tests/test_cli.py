from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from youtube_live_count_chime.cli import ChimeNotifier, Config, parse_config
from youtube_live_count_chime.monitor import Direction, Transition


class RecordingPlayer:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.paths.append(path)


class CliConfigurationTests(unittest.TestCase):
    def test_parses_url_interval_and_existing_sound_files(self) -> None:
        with TemporaryDirectory() as directory:
            up_sound = Path(directory) / "up.aiff"
            down_sound = Path(directory) / "down.aiff"
            up_sound.write_bytes(b"up")
            down_sound.write_bytes(b"down")

            config = parse_config(
                [
                    "https://example.test/live",
                    "--interval",
                    "2.5",
                    "--up-sound",
                    str(up_sound),
                    "--down-sound",
                    str(down_sound),
                ]
            )

        self.assertEqual(config.url, "https://example.test/live")
        self.assertEqual(config.interval_seconds, 2.5)
        self.assertEqual(config.up_sound, up_sound)
        self.assertEqual(config.down_sound, down_sound)

    def test_rejects_non_positive_interval(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_config(["--interval", "0"])

        self.assertIn("interval must be greater than zero", stderr.getvalue())

    def test_rejects_non_finite_interval(self) -> None:
        for value in ("nan", "inf", "+inf", "-inf"):
            with self.subTest(value=value):
                stderr = StringIO()

                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    parse_config([f"--interval={value}"])

                self.assertIn("interval must be finite", stderr.getvalue())

    def test_rejects_missing_sound_file(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_config(
                [
                    "--up-sound",
                    "/definitely/missing/up.aiff",
                    "--down-sound",
                    "/definitely/missing/down.aiff",
                ]
            )

        self.assertIn("sound file does not exist", stderr.getvalue())


class ChimeNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.player = RecordingPlayer()
        self.stdout = StringIO()
        self.stderr = StringIO()
        self.config = Config(
            url="https://example.test/live",
            interval_seconds=5.0,
            up_sound=Path("/sounds/up.aiff"),
            down_sound=Path("/sounds/down.aiff"),
        )
        self.notifier = ChimeNotifier(
            config=self.config,
            player=self.player,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def test_increase_uses_up_sound_once(self) -> None:
        self.notifier(
            Transition(previous=1, current=4, direction=Direction.UP)
        )

        self.assertEqual(self.player.paths, [self.config.up_sound])
        self.assertIn("1 -> 4 (up)", self.stdout.getvalue())

    def test_decrease_uses_down_sound_once(self) -> None:
        self.notifier(
            Transition(previous=5, current=2, direction=Direction.DOWN)
        )

        self.assertEqual(self.player.paths, [self.config.down_sound])
        self.assertIn("5 -> 2 (down)", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
