from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from youtube_live_count_chime.sounds import SoundPlaybackError, play_sound


class PlaySoundTests(unittest.TestCase):
    def test_invokes_afplay_with_the_path(self) -> None:
        with patch("youtube_live_count_chime.sounds.subprocess.run") as run:
            play_sound(Path("/sounds/up.aiff"))

        run.assert_called_once_with(
            ("/usr/bin/afplay", "/sounds/up.aiff"),
            check=True,
            stdout=-3,
            stderr=-1,
            text=True,
        )

    def test_playback_failure_becomes_sound_playback_error(self) -> None:
        # Both arms of the catch: a non-zero afplay exit and afplay being
        # missing/not executable (OSError).
        failures: tuple[Exception, ...] = (
            subprocess.CalledProcessError(1, "afplay", stderr="boom"),
            FileNotFoundError("afplay not found"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch(
                    "youtube_live_count_chime.sounds.subprocess.run", side_effect=failure
                ):
                    with self.assertRaises(SoundPlaybackError):
                        play_sound(Path("/sounds/up.aiff"))


if __name__ == "__main__":
    unittest.main()
