"""Play local audio files through macOS afplay."""

from __future__ import annotations

from pathlib import Path
import subprocess


class SoundPlaybackError(RuntimeError):
    """Raised when macOS cannot play a configured sound."""


def play_sound(path: Path) -> None:
    """Play one sound synchronously with the macOS system audio player."""
    try:
        subprocess.run(
            ("/usr/bin/afplay", str(path)),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SoundPlaybackError(f"could not play {path}: {error}") from error
