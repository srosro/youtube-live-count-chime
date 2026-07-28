"""Speak short announcements through the macOS say(1) voice."""

from __future__ import annotations

import subprocess
from typing import Final

from youtube_live_count_chime.models import POLL_INTERVAL_SECONDS


_SAY: Final = "/usr/bin/say"


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
            # A real announcement measures ~3.6s, so the bound has to clear
            # that comfortably or every rise would be cut off mid-sentence
            # instead of spoken. Two poll intervals is the smallest multiple
            # of the loop's own cadence that does: it leaves ample headroom
            # over a real line while still capping a wedged `say` at a couple
            # of polls for that one channel.
            timeout=2 * POLL_INTERVAL_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # These exceptions render the whole argv, and the argv is the spoken
        # line. `from None` keeps a traceback from re-rendering what the
        # message omits.
        raise SpeechError(f"could not speak ({type(error).__name__})") from None
