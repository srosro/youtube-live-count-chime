from collections.abc import Sequence
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from youtube_live_count_chime.cli import Config, parse_config, run
from youtube_live_count_chime.sounds import SoundPlaybackError
from youtube_live_count_chime.youtube import ViewerCountError


class RecordingPlayer:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.paths.append(path)


class SequenceFetcher:
    def __init__(self, values: Sequence[int | Exception]) -> None:
        self._values = list(values)

    def __call__(self, url: str) -> int:
        value = self._values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FailingOncePlayer:
    def __init__(self) -> None:
        self.paths: list[Path] = []
        self._failed = False

    def __call__(self, path: Path) -> None:
        self.paths.append(path)
        if not self._failed:
            self._failed = True
            raise SoundPlaybackError("temporary audio failure")


class StopAfterSleeps:
    def __init__(self, count: int) -> None:
        self._remaining = count

    def __call__(self, seconds: float) -> None:
        self._remaining -= 1
        if self._remaining == 0:
            raise KeyboardInterrupt


def run_for_values(
    config: Config,
    values: Sequence[int | Exception],
    player: RecordingPlayer | FailingOncePlayer,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with (
        patch(
            "youtube_live_count_chime.cli.fetch_viewer_count",
            SequenceFetcher(values),
        ),
        patch("youtube_live_count_chime.cli.play_sound", player),
        patch(
            "youtube_live_count_chime.cli.time.sleep",
            StopAfterSleeps(len(values)),
        ),
        patch("youtube_live_count_chime.cli.sys.stdout", stdout),
        patch("youtube_live_count_chime.cli.sys.stderr", stderr),
    ):
        result = run(config)
    return result, stdout.getvalue(), stderr.getvalue()


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


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config(
            url="https://example.test/live",
            interval_seconds=5.0,
            up_sound=Path("/sounds/up.aiff"),
            down_sound=Path("/sounds/down.aiff"),
        )

    def test_first_valid_count_is_a_silent_baseline(self) -> None:
        player = RecordingPlayer()

        result, stdout, stderr = run_for_values(self.config, [3], player)

        self.assertEqual(result, 0)
        self.assertEqual(player.paths, [])
        self.assertIn("Baseline: 3 watching now", stdout)
        self.assertIn("Stopped.", stdout)
        self.assertEqual(stderr, "")

    def test_changed_count_plays_direction_sound_once(self) -> None:
        cases = (
            ([1, 4], self.config.up_sound, "1 -> 4 (up)"),
            ([5, 2], self.config.down_sound, "5 -> 2 (down)"),
        )
        for values, expected_sound, expected_transition in cases:
            with self.subTest(values=values):
                player = RecordingPlayer()

                _, stdout, _ = run_for_values(self.config, values, player)

                self.assertEqual(player.paths, [expected_sound])
                self.assertIn(expected_transition, stdout)

    def test_unchanged_count_is_silent(self) -> None:
        player = RecordingPlayer()

        _, stdout, _ = run_for_values(self.config, [3, 3], player)

        self.assertEqual(player.paths, [])
        self.assertNotIn("3 -> 3", stdout)

    def test_fetch_failure_preserves_last_valid_count(self) -> None:
        player = RecordingPlayer()

        _, stdout, stderr = run_for_values(
            self.config,
            [1, ViewerCountError("temporary fetch failure"), 2],
            player,
        )

        self.assertEqual(player.paths, [self.config.up_sound])
        self.assertIn("1 -> 2 (up)", stdout)
        self.assertIn("Warning: temporary fetch failure", stderr)

    def test_failed_chime_is_retried_for_unchanged_count(self) -> None:
        player = FailingOncePlayer()

        _, stdout, stderr = run_for_values(self.config, [1, 2, 2], player)

        self.assertEqual(
            player.paths,
            [self.config.up_sound, self.config.up_sound],
        )
        self.assertEqual(stdout.count("1 -> 2 (up)"), 1)
        self.assertIn("Warning: temporary audio failure", stderr)


if __name__ == "__main__":
    unittest.main()
