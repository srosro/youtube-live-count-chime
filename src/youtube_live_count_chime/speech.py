"""Speak short announcements through the macOS say(1) voice."""

from __future__ import annotations

from typing import Final

from youtube_live_count_chime.macos import MACOS_COMMAND_FAILURES, run_macos_command


_SAY: Final = "/usr/bin/say"

# Bounds a wedged `say`, which holds the shared audio lock for the full
# timeout; comfortably longer than a spoken line so no rise is cut off.
_TIMEOUT_SECONDS: Final = 6.0


class SpeechError(RuntimeError):
    """Raised when macOS cannot speak an announcement."""


def speak_text(text: str) -> None:
    """Speak one line synchronously, passing the text as argv rather than a shell."""
    try:
        # The text carries a channel handle, so it is not ours to trust: argv.
        run_macos_command((_SAY, "--", text), timeout=_TIMEOUT_SECONDS)
    except MACOS_COMMAND_FAILURES as error:
        # These exceptions render the whole argv, and the argv is the spoken
        # line. `from None` keeps a traceback from re-rendering what the
        # message omits.
        raise SpeechError(f"could not speak ({type(error).__name__})") from None
