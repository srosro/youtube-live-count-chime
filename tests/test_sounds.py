from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from youtube_live_count_chime.sounds import SoundConfigurationError, require_sound_file


class SoundValidationTests(unittest.TestCase):
    def test_accepts_existing_regular_file(self) -> None:
        with TemporaryDirectory() as directory:
            sound = Path(directory) / "tone.aiff"
            sound.write_bytes(b"test audio fixture")

            self.assertEqual(require_sound_file(sound), sound)

    def test_rejects_missing_file(self) -> None:
        missing = Path("/definitely/missing/youtube-live-count-chime.aiff")

        with self.assertRaisesRegex(SoundConfigurationError, "does not exist"):
            require_sound_file(missing)

    def test_rejects_directory(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                SoundConfigurationError, "is not a regular file"
            ):
                require_sound_file(Path(directory))


if __name__ == "__main__":
    unittest.main()
