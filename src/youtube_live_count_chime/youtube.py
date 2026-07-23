"""Fetch and parse a YouTube livestream's public viewer count."""

from __future__ import annotations

import re
from re import Pattern
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_COUNT_PATTERN: Final[Pattern[str]] = re.compile(
    r'"originalViewCount"\s*:\s*"(?P<count>[0-9]+)"'
)


class ViewerCountError(RuntimeError):
    """Raised when a valid live viewer count cannot be obtained."""


def parse_viewer_count(page_html: str) -> int:
    """Extract the exact public live viewer count from YouTube page HTML."""
    match = _COUNT_PATTERN.search(page_html)
    if match is None:
        raise ViewerCountError("live viewer count was not found in the page")
    return int(match.group("count"))


def fetch_viewer_count(url: str, timeout_seconds: float = 10.0) -> int:
    """Download a YouTube watch page and return its live viewer count."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            page_html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ViewerCountError(f"could not fetch the livestream page: {error}") from error

    return parse_viewer_count(page_html)
