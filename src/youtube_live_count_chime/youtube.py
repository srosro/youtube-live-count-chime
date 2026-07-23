"""Fetch and parse a YouTube livestream's public viewer count."""

from __future__ import annotations

from http.client import IncompleteRead
from json import JSONDecodeError, JSONDecoder
import re
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_RENDERER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'"videoViewCountRenderer"\s*:\s*'
)
_JSON_DECODER: Final = JSONDecoder()


class ViewerCountError(RuntimeError):
    """Raised when a valid live viewer count cannot be obtained."""


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


def fetch_viewer_count(url: str) -> int:
    """Download a YouTube watch page and return its live viewer count."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=10.0) as response:
            page_html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, IncompleteRead, TimeoutError, OSError) as error:
        raise ViewerCountError(f"could not fetch the livestream page: {error}") from error

    return parse_viewer_count(page_html)
