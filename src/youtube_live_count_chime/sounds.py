"""Validate and play local audio files through macOS afplay."""

from __future__ import annotations

from pathlib import Path
import subprocess


class SoundConfigurationError(ValueError):
    """Raised when a configured sound path is unusable."""


class SoundPlaybackError(RuntimeError):
    """Raised when macOS cannot play a configured sound."""


def require_sound_file(path: Path) -> Path:
    """Return an existing regular sound file or raise a configuration error."""
    if not path.exists():
        raise SoundConfigurationError(f"sound file does not exist: {path}")
    if not path.is_file():
        raise SoundConfigurationError(f"sound path is not a regular file: {path}")
    return path


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
