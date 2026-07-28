"""Post macOS notifications through osascript."""

from __future__ import annotations

import subprocess
from typing import Final

from youtube_live_count_chime.models import POLL_INTERVAL_SECONDS


_OSASCRIPT: Final = "/usr/bin/osascript"

# argv-based, so notification text is never parsed as AppleScript. Channel
# handles reach the banner from the command line and the digest carries
# them verbatim; interpolating either into -e source would be a
# code-injection seam.
_SCRIPT_LINES: Final = (
    "on run argv",
    "display notification item 1 of argv with title item 2 of argv",
    "end run",
)


class NotificationError(RuntimeError):
    """Raised when macOS cannot post a notification."""


def post_notification(title: str, body: str) -> None:
    """Post one notification, passing text as argv rather than script source."""
    command: list[str] = [_OSASCRIPT]
    for line in _SCRIPT_LINES:
        command += ["-e", line]
    command += ["--", body, title]
    try:
        subprocess.run(
            tuple(command),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            # The caller awaits this inside its poll loop, so a wedged
            # Notification Center costs that one channel a single extra poll
            # interval — and it still cannot hang the watcher.
            timeout=POLL_INTERVAL_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # These exceptions render the whole argv, and the argv carries the
        # notification text — every watched channel's current count, which the
        # LaunchAgent redirects into its log file.
        # `from None` keeps a traceback from re-rendering what the message omits.
        raise NotificationError(
            f"could not post notification ({type(error).__name__})"
        ) from None
