"""Fetch and parse a YouTube livestream's public viewer count."""

from __future__ import annotations

from json import JSONDecodeError, JSONDecoder
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_RENDERER_KEY: Final = '"videoViewCountRenderer"'
_JSON_DECODER: Final = JSONDecoder()


class ViewerCountError(RuntimeError):
    """Raised when a valid live viewer count cannot be obtained."""


def _decode_json_value_after_key(page_html: str, key_end: int) -> object | None:
    value_start = key_end
    while value_start < len(page_html) and page_html[value_start] in " \t\r\n":
        value_start += 1
    if value_start >= len(page_html) or page_html[value_start] != ":":
        return None

    value_start += 1
    while value_start < len(page_html) and page_html[value_start] in " \t\r\n":
        value_start += 1
    try:
        value, _ = _JSON_DECODER.raw_decode(page_html, value_start)
    except JSONDecodeError:
        return None
    return cast(object, value)


def parse_viewer_count(page_html: str) -> int:
    """Extract the exact public live viewer count from YouTube page HTML."""
    search_start = 0
    while True:
        key_start = page_html.find(_RENDERER_KEY, search_start)
        if key_start == -1:
            break

        key_end = key_start + len(_RENDERER_KEY)
        value = _decode_json_value_after_key(page_html, key_end)
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
        search_start = key_end

    raise ViewerCountError("live viewer count was not found in the page")


def fetch_viewer_count(url: str) -> int:
    """Download a YouTube watch page and return its live viewer count."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=10.0) as response:
            page_html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ViewerCountError(f"could not fetch the livestream page: {error}") from error

    return parse_viewer_count(page_html)
