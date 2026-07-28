"""Shared typed models for livestream viewer-count sources, and the polling
loop every source runs on top of them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
import logging
import re
from typing import Final, Protocol, Self


# YouTube handles allow letters, digits, ., _, - ; Twitch logins are a subset.
# Both platforms resolve handles case-insensitively, so a lowercased handle is
# safe to interpolate straight into the fetch URL. The 30-char bound (YouTube's
# max; Twitch's is 25) rejects long charset-valid junk that would 404 every poll.
_HANDLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9._-]{1,30}")

# Seconds between polls of each channel — the single source of truth for both
# sources' cadence.
POLL_INTERVAL_SECONDS: Final = 5.0

_LOGGER: Final = logging.getLogger(__name__)


class SourceFetchError(RuntimeError):
    """Raised when a source cannot produce a snapshot for one poll."""


class Platform(StrEnum):
    """A livestream platform supported by the watcher."""

    YOUTUBE = "youtube"
    TWITCH = "twitch"


def normalize_handle(value: str) -> str:
    """Normalize a channel handle/login to a dedup key, rejecting unusable input.

    Strips surrounding whitespace and a leading ``@`` and lowercases, so
    ``@MKBHD`` and ``mkbhd`` resolve to one target. Raises ``ValueError`` for
    an empty handle, one longer than 30 characters, or one carrying
    URL-significant characters (``/``, ``?``, spaces, a second ``@``), which
    would otherwise be interpolated straight into a fetch URL and fail every
    poll.
    """
    normalized = value.strip().removeprefix("@").strip().lower()
    if not _HANDLE_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid channel handle: {value!r}")
    return normalized


@dataclass(frozen=True, slots=True)
class StreamTarget:
    """A named channel or account on one platform."""

    platform: Platform
    name: str

    @property
    def key(self) -> str:
        """Return a stable identifier for this target."""
        return f"{self.platform}:{self.name}"

    @property
    def label(self) -> str:
        """Return this target as displayed to a human, e.g. ``twitch mkbhd``."""
        return f"{self.platform} {self.name}"


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """One observed live or offline state for a stream target."""

    target: StreamTarget
    stream_id: str | None
    viewers: int | None

    def __post_init__(self) -> None:
        if (self.stream_id is None) != (self.viewers is None):
            raise ValueError("live snapshots require both stream_id and viewers")
        if self.viewers is not None and self.viewers < 0:
            raise ValueError("viewers cannot be negative")

    @classmethod
    def offline(cls, target: StreamTarget) -> Self:
        """Create an offline snapshot for a stream target."""
        return cls(target, None, None)


class StreamSource(Protocol):
    """An asynchronous source of stream snapshots."""

    @property
    def target(self) -> StreamTarget:
        """Return the channel this source polls."""

    def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        """Yield successive stream snapshots, or ``None`` for a failed poll."""


async def poll_snapshots(
    name: str, fetch: Callable[[], StreamSnapshot]
) -> AsyncIterator[StreamSnapshot | None]:
    """Poll ``fetch`` forever, yielding snapshots and surviving fetch failures.

    Entering an outage yields ``None`` once: the channel is *unknown*, which
    is not ``StreamSnapshot.offline()`` ("confirmed not streaming"). Saying
    nothing at all would leave the consumer publishing pre-outage state as
    current, but repeating it every poll would say nothing new — the
    consumer's response is idempotent, so only the transition carries
    information. A successful poll re-arms both the warning and the marker
    for the next distinct outage.
    """
    warned = False
    while True:
        try:
            snapshot = await asyncio.to_thread(fetch)
        except SourceFetchError as error:
            if not warned:
                # `name` already carries the platform, e.g. "youtube:mkbhd".
                _LOGGER.warning("could not fetch %s: %s", name, error)
                warned = True
                yield None
        else:
            warned = False
            yield snapshot
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
