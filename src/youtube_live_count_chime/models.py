"""Shared typed models for livestream viewer-count sources."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Final, Literal, Protocol, Self


# YouTube handles allow letters, digits, ., _, - ; Twitch logins are a subset.
# Both platforms resolve handles case-insensitively, so a lowercased handle is
# safe to interpolate straight into the fetch URL.
_HANDLE_PATTERN: Final = re.compile(r"[a-z0-9._-]+")


class Platform(StrEnum):
    """A livestream platform supported by the watcher."""

    YOUTUBE = "youtube"
    TWITCH = "twitch"


def normalize_handle(value: str) -> str:
    """Normalize a channel handle/login to a dedup key, rejecting unusable input.

    Strips surrounding whitespace and a leading ``@`` and lowercases, so
    ``@MKBHD`` and ``mkbhd`` resolve to one target. Raises ``ValueError`` for
    an empty handle or one carrying URL-significant characters (``/``, ``?``,
    spaces, a second ``@``), which would otherwise be interpolated straight
    into a fetch URL and fail every poll.
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


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """One observed live or offline state for a stream target."""

    target: StreamTarget
    stream_id: str | None
    viewers: int | None
    url: str | None = None

    def __post_init__(self) -> None:
        if (self.stream_id is None) != (self.viewers is None):
            raise ValueError("live snapshots require both stream_id and viewers")
        if self.viewers is not None and self.viewers < 0:
            raise ValueError("viewers cannot be negative")

    @classmethod
    def offline(cls, target: StreamTarget, *, url: str | None = None) -> Self:
        """Create an offline snapshot for a stream target."""
        return cls(target, None, None, url=url)


@dataclass(frozen=True, slots=True)
class ViewerChange:
    """A viewer-count change observed during one stream."""

    target: StreamTarget
    stream_id: str
    previous: int
    current: int

    @property
    def direction(self) -> Literal["up", "down"]:
        """Return the direction of the change."""
        return "up" if self.current > self.previous else "down"


class CountTracker:
    """Track the latest live snapshot for each target."""

    def __init__(self) -> None:
        self._current: dict[StreamTarget, StreamSnapshot] = {}

    def current(self, target: StreamTarget) -> StreamSnapshot | None:
        """Return the target's latest live snapshot, if any."""
        return self._current.get(target)

    def observe(self, snapshot: StreamSnapshot) -> ViewerChange | None:
        """Record a snapshot and return a viewer-count change when it occurs."""
        if snapshot.stream_id is None:
            self._current.pop(snapshot.target, None)
            return None

        previous = self._current.get(snapshot.target)
        self._current[snapshot.target] = snapshot
        if (
            previous is None
            or previous.stream_id != snapshot.stream_id
            or previous.viewers == snapshot.viewers
        ):
            return None

        assert previous.viewers is not None
        assert snapshot.viewers is not None
        return ViewerChange(
            snapshot.target,
            snapshot.stream_id,
            previous.viewers,
            snapshot.viewers,
        )


class StreamSource(Protocol):
    """An asynchronous source of stream snapshots."""

    @property
    def name(self) -> str:
        """Return a stable display name for this source."""

    def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        """Yield successive stream snapshots."""
