"""Play local audio files through macOS afplay."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Final


# This call is awaited while holding the audio lock every source shares, so a
# wedged afplay (output device removed, audio rerouted mid-play) would
# otherwise hold that lock forever — silencing every channel's chime and
# announcement, and stalling the banners behind them, with nothing logged.
#
# The bound catches that hang; it is deliberately NOT tuned to the default
# chime's ~1.9s. --up-sound/--down-sound take any file the operator points at,
# so a bound sized for Glass would SIGKILL a longer custom chime mid-play on
# every change and log a warning each time — breaking a supported setup to
# guard a pathological one. Generous enough that no plausible alert sound
# reaches it, short enough that a true wedge ends.
_TIMEOUT_SECONDS: Final = 30.0


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
