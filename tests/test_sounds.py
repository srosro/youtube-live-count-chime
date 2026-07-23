from pathlib import Path
import unittest
from unittest.mock import patch

from youtube_live_count_chime.sounds import play_sound


class PlaySoundTests(unittest.TestCase):
    def test_passes_requested_volume_to_afplay(self) -> None:
        with patch("youtube_live_count_chime.sounds.subprocess.run") as run:
            play_sound(Path("/sounds/up.aiff"), volume=1.5)

        run.assert_called_once_with(
            ("/usr/bin/afplay", "-v", "1.5", "/sounds/up.aiff"),
            check=True,
            stdout=-3,
            stderr=-1,
            text=True,
        )

    def test_rejects_non_positive_or_non_finite_volume(self) -> None:
        for volume in (0.0, -1.0, float("inf"), float("-inf"), float("nan")):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                play_sound(Path("/sounds/up.aiff"), volume=volume)


if __name__ == "__main__":
    unittest.main()
