"""Speak short announcements through the macOS say(1) voice."""

from __future__ import annotations

import subprocess
from typing import Final


_SAY: Final = "/usr/bin/say"

# Bounds a wedged `say`, which holds the shared audio lock for the full
# timeout; comfortably longer than a spoken line so no rise is cut off.
_TIMEOUT_SECONDS: Final = 6.0


class SpeechError(RuntimeError):
    """Raised when macOS cannot speak an announcement."""


def speak_text(text: str) -> None:
    """Speak one line synchronously, passing the text as argv rather than a shell."""
    try:
        subprocess.run(
            # argv, never a shell: the text carries a channel handle and is
            # not ours to trust.
            (_SAY, "--", text),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # These exceptions render the whole argv, and the argv is the spoken
        # line. `from None` keeps a traceback from re-rendering what the
        # message omits.
        raise SpeechError(f"could not speak ({type(error).__name__})") from None
