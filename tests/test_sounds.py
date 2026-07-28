from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from youtube_live_count_chime.models import POLL_INTERVAL_SECONDS
from youtube_live_count_chime.sounds import (
    _TIMEOUT_SECONDS,
    SoundPlaybackError,
    play_sound,
)


class PlaySoundTests(unittest.TestCase):
    def test_invokes_afplay_with_the_path(self) -> None:
        with patch("youtube_live_count_chime.sounds.subprocess.run") as run:
            play_sound(Path("/sounds/up.aiff"))

        run.assert_called_once_with(
            ("/usr/bin/afplay", "/sounds/up.aiff"),
            check=True,
            timeout=_TIMEOUT_SECONDS,
            stdout=-3,
            stderr=-1,
            text=True,
        )
        # Pins a floor on the bound: too tight and a custom --up-sound longer
        # than the ~1.9s default is truncated on every change.
        self.assertGreaterEqual(_TIMEOUT_SECONDS, 4 * POLL_INTERVAL_SECONDS)

    def test_playback_failure_becomes_sound_playback_error(self) -> None:
        # Every arm of the catch: a non-zero afplay exit, a missing or
        # non-executable afplay (OSError), and the bound firing.
        failures: tuple[Exception, ...] = (
            subprocess.CalledProcessError(1, "afplay", stderr="boom"),
            FileNotFoundError("afplay not found"),
            subprocess.TimeoutExpired("afplay", _TIMEOUT_SECONDS),
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
