"""Fetch and parse a YouTube livestream's public viewer count."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from html import unescape
from http.client import HTTPException
from json import JSONDecodeError, JSONDecoder
import re
from typing import Final, Self, cast
from urllib.request import Request, urlopen

from youtube_live_count_chime.models import (
    Platform,
    SourceFetchError,
    StreamSnapshot,
    StreamTarget,
    normalize_handle,
    poll_snapshots,
)


USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_RENDERER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'"videoViewCountRenderer"\s*:\s*'
)
_CANONICAL_LINK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<link\b"
    r"(?=[^>]*\brel\s*=\s*['\"][^'\"]*\bcanonical\b[^'\"]*['\"])"
    r"(?=[^>]*\bhref\s*=\s*['\"](?P<url>[^'\"]+)['\"])[^>]*>",
    re.IGNORECASE,
)
_VIDEO_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:[?&]v=)(?P<video_id>[A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])"
)
_JSON_DECODER: Final = JSONDecoder()


class ViewerCountError(SourceFetchError):
    """Raised when a valid live viewer count cannot be obtained."""


@dataclass(frozen=True, slots=True)
class YouTubeLivePage:
    """The live video ID and viewer count from a YouTube page."""

    video_id: str
    viewers: int


def parse_viewer_count(page_html: str) -> int:
    """Extract the exact public live viewer count from YouTube page HTML."""
    for match in _RENDERER_PATTERN.finditer(page_html):
        try:
            value = cast(object, _JSON_DECODER.raw_decode(page_html, match.end())[0])
        except JSONDecodeError:
            continue
        if isinstance(value, dict):
            renderer = cast(dict[str, object], value)
            count = renderer.get("originalViewCount")
            if (
                renderer.get("isLive") is True
                and isinstance(count, str)
                and count.isascii()
                and count.isdigit()
            ):
                return int(count)

    raise ViewerCountError("live viewer count was not found in the page")


def _video_id_from_url(url: str) -> str | None:
    """Return the video ID from a YouTube watch URL, if present."""
    match = _VIDEO_ID_PATTERN.search(url)
    return match.group("video_id") if match is not None else None


def parse_live_page(page_html: str, requested_url: str) -> YouTubeLivePage:
    """Extract the live video ID and viewer count from a YouTube page."""
    canonical_match = _CANONICAL_LINK_PATTERN.search(page_html)
    canonical_url = (
        unescape(canonical_match.group("url")) if canonical_match is not None else None
    )
    video_id = _video_id_from_url(canonical_url or requested_url)
    if video_id is None and canonical_url is not None:
        video_id = _video_id_from_url(requested_url)
    if video_id is None:
        raise ViewerCountError("live video ID was not found in the page")

    return YouTubeLivePage(video_id, parse_viewer_count(page_html))


def _download_page(url: str) -> str:
    """Download a YouTube page using the standard library HTTP client."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=10.0) as response:
            return cast(bytes, response.read()).decode("utf-8", errors="replace")
    except (HTTPException, OSError) as error:
        raise ViewerCountError(f"could not fetch the livestream page: {error}") from error


def fetch_live_page(url: str) -> YouTubeLivePage:
    """Download a YouTube page and return its live-page details."""
    return parse_live_page(_download_page(url), url)


@dataclass(frozen=True, slots=True)
class YouTubeSource:
    """An asynchronous polling source for one YouTube channel handle."""

    name: str
    target: StreamTarget
    url: str

    @classmethod
    def for_handle(cls, handle: str) -> Self:
        """Create a source that discovers the current stream for a handle."""
        normalized_handle = normalize_handle(handle)
        target = StreamTarget(Platform.YOUTUBE, normalized_handle)
        return cls(
            name=target.key,
            target=target,
            url=f"https://www.youtube.com/@{normalized_handle}/live",
        )

    def _snapshot(self) -> StreamSnapshot:
        """Fetch the channel's live page as a snapshot."""
        page = fetch_live_page(self.url)
        return StreamSnapshot(
            target=self.target,
            stream_id=page.video_id,
            viewers=page.viewers,
        )

    def snapshots(self) -> AsyncIterator[StreamSnapshot | None]:
        """Yield live snapshots, retrying page-fetch failures after each poll."""
        return poll_snapshots(self.name, self._snapshot)
