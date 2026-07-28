"""Speak short announcements through the macOS say(1) voice."""

from __future__ import annotations

import subprocess
from typing import Final


_SAY: Final = "/usr/bin/say"

# Comfortably longer than a spoken line, so no rise is cut off mid-sentence,
# and no longer than that: this call is awaited while holding the audio lock
# that *every* source shares, so a wedged `say` mutes and stalls the whole
# fleet — not one channel — for the full timeout.
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
