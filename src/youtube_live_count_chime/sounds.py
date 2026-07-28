"""Play local audio files through macOS afplay."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Final


# Bounded on the same basis as speech.py: this call is awaited while holding
# the audio lock every source shares, so a wedged afplay (output device
# removed, audio rerouted mid-play) would otherwise hold that lock forever —
# silencing every channel's chime and announcement, and stalling the banners
# behind them, with nothing logged. Glass measures ~1.9s, so this clears a
# real chime with room while capping a wedge at under one poll interval.
_TIMEOUT_SECONDS: Final = 4.0


class SoundPlaybackError(RuntimeError):
    """Raised when macOS cannot play a configured sound."""


def play_sound(path: Path) -> None:
    """Play one sound synchronously with the macOS system audio player."""
    try:
        subprocess.run(
            ("/usr/bin/afplay", str(path)),
            check=True,
            timeout=_TIMEOUT_SECONDS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise SoundPlaybackError(f"could not play {path}: {error}") from error
