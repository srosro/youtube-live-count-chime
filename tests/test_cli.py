from collections.abc import Callable, Sequence
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
import unittest
from unittest.mock import Mock, patch

from youtube_live_count_chime.cli import Config, parse_config, run
from youtube_live_count_chime.sounds import SoundPlaybackError
from youtube_live_count_chime.youtube import ViewerCountError


def run_for_values(
    config: Config,
    values: Sequence[int | Exception],
    sound_effects: Sequence[Exception | None] | None = None,
) -> tuple[int, str, str, list[Path]]:
    stdout = StringIO()
    stderr = StringIO()
    fetcher = Mock(side_effect=values)
    player = Mock(side_effect=sound_effects)
    sleeper = Mock(
        side_effect=[None] * (len(values) - 1) + [KeyboardInterrupt()]
    )
    with (
        patch(
            "youtube_live_count_chime.cli.fetch_viewer_count",
            cast(Callable[[str], int], fetcher),
        ),
        patch(
            "youtube_live_count_chime.cli.play_sound",
            cast(Callable[[Path], None], player),
        ),
        patch(
            "youtube_live_count_chime.cli.time.sleep",
            cast(Callable[[float], None], sleeper),
        ),
        patch("youtube_live_count_chime.cli.sys.stdout", stdout),
        patch("youtube_live_count_chime.cli.sys.stderr", stderr),
    ):
        result = run(config)
    paths = [cast(Path, call.args[0]) for call in player.call_args_list]
    return result, stdout.getvalue(), stderr.getvalue(), paths


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

    def test_rejects_invalid_interval(self) -> None:
        cases = (
            ("non-number", "many", "interval must be a number"),
            ("non-positive", "0", "interval must be greater than zero"),
            ("non-finite", "nan", "interval must be finite"),
        )
        for name, value, message in cases:
            with self.subTest(name=name):
                stderr = StringIO()

                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    parse_config([f"--interval={value}"])

                self.assertIn(message, stderr.getvalue())

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
        result, stdout, stderr, paths = run_for_values(self.config, [3])

        self.assertEqual(result, 0)
        self.assertEqual(paths, [])
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
                _, stdout, _, paths = run_for_values(self.config, values)

                self.assertEqual(paths, [expected_sound])
                self.assertIn(expected_transition, stdout)

    def test_unchanged_count_is_silent(self) -> None:
        _, stdout, _, paths = run_for_values(self.config, [3, 3])

        self.assertEqual(paths, [])
        self.assertNotIn("3 -> 3", stdout)

    def test_fetch_failure_preserves_last_valid_count(self) -> None:
        _, stdout, stderr, paths = run_for_values(
            self.config,
            [1, ViewerCountError("temporary fetch failure"), 2],
        )

        self.assertEqual(paths, [self.config.up_sound])
        self.assertIn("1 -> 2 (up)", stdout)
        self.assertIn("Warning: temporary fetch failure", stderr)

    def test_failed_chime_is_retried_for_unchanged_count(self) -> None:
        _, stdout, stderr, paths = run_for_values(
            self.config,
            [1, 2, 2],
            [SoundPlaybackError("temporary audio failure"), None],
        )

        self.assertEqual(
            paths,
            [self.config.up_sound, self.config.up_sound],
        )
        self.assertEqual(stdout.count("1 -> 2 (up)"), 1)
        self.assertIn("Warning: temporary audio failure", stderr)


if __name__ == "__main__":
    unittest.main()
