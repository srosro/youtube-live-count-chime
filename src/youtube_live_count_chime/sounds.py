"""Play local audio files through macOS afplay."""

from __future__ import annotations

import math
from pathlib import Path
import subprocess


class SoundPlaybackError(RuntimeError):
    """Raised when macOS cannot play a configured sound."""


def play_sound(path: Path, *, volume: float = 1.0) -> None:
    """Play one sound synchronously with the macOS system audio player."""
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("volume must be finite and positive")

    try:
        subprocess.run(
            ("/usr/bin/afplay", "-v", str(volume), str(path)),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SoundPlaybackError(f"could not play {path}: {error}") from error
