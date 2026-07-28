"""Play local audio files through macOS afplay."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from youtube_live_count_chime.macos import MACOS_COMMAND_FAILURES, run_macos_command


# Bounds a wedged afplay, which would otherwise hold the shared audio lock
# forever. Deliberately NOT tuned to the default chime's ~1.9s: --up-sound
# takes any file the operator points at, and a bound sized for Glass would
# truncate a working custom chime on every change.
_TIMEOUT_SECONDS: Final = 30.0


class SoundPlaybackError(RuntimeError):
    """Raised when macOS cannot play a configured sound."""


def play_sound(path: Path) -> None:
    """Play one sound synchronously with the macOS system audio player."""
    try:
        run_macos_command(("/usr/bin/afplay", str(path)), timeout=_TIMEOUT_SECONDS)
    except MACOS_COMMAND_FAILURES as error:
        raise SoundPlaybackError(f"could not play {path}: {error}") from error
