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
        # The bound is what stops a wedged afplay holding the fleet-wide audio
        # lock forever. It must clear a real ~1.9s chime and still cap a wedge
        # inside a poll interval.
        self.assertGreater(_TIMEOUT_SECONDS, 2.0)
        self.assertLessEqual(_TIMEOUT_SECONDS, POLL_INTERVAL_SECONDS)

    def test_playback_failure_becomes_sound_playback_error(self) -> None:
        # Every arm of the catch: a non-zero afplay exit, afplay being
        # missing/not executable (OSError), and a wedged afplay hitting the
        # bound — the last is the one that would otherwise hold the shared
        # audio lock forever with nothing logged.
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
