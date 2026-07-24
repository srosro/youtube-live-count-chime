"""Fetch live Twitch viewer counts through the public Helix API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http.client import HTTPException
import json
from json import JSONDecodeError
import logging
import os
import threading
from typing import Final, Self, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from youtube_live_count_chime.models import (
    Platform,
    StreamSnapshot,
    StreamTarget,
    normalize_handle,
)


TOKEN_URL: Final = "https://id.twitch.tv/oauth2/token"
STREAMS_URL: Final = "https://api.twitch.tv/helix/streams"
_CLIENT_ID_ENV: Final = "TWITCH_CLIENT_ID"
_CLIENT_SECRET_ENV: Final = "TWITCH_CLIENT_SECRET"
_LOGGER: Final = logging.getLogger(__name__)


class TwitchError(RuntimeError):
    """Raised when a Twitch credential or response cannot be used."""


def _object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _strict_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@dataclass(frozen=True, slots=True)
class TwitchCredentials:
    """A Twitch application's client id and secret."""

    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> Self:
        """Read both credentials from the environment, naming any that is unset."""
        client_id = os.environ.get(_CLIENT_ID_ENV, "")
        client_secret = os.environ.get(_CLIENT_SECRET_ENV, "")
        if not client_id:
            raise TwitchError(f"{_CLIENT_ID_ENV} is not set")
        if not client_secret:
            raise TwitchError(f"{_CLIENT_SECRET_ENV} is not set")
        return cls(client_id, client_secret)


def parse_app_token(payload: object) -> str:
    """Validate a client-credentials token response and return its access token."""
    value = _object_dict(payload)
    token = value.get("access_token") if value is not None else None
    if not isinstance(token, str) or not token:
        raise TwitchError("invalid Twitch token response")
    return token


def parse_stream(payload: object, target: StreamTarget) -> StreamSnapshot:
    """Turn one Helix streams response for a login into a snapshot."""
    value = _object_dict(payload)
    data = value.get("data") if value is not None else None
    if not isinstance(data, list):
        raise TwitchError("invalid Twitch streams response")

    url = f"https://www.twitch.tv/{quote(target.name, safe='')}"
    if not data:
        return StreamSnapshot.offline(target, url=url)

    entry = _object_dict(cast(list[object], data)[0])
    stream_id = entry.get("id") if entry is not None else None
    viewers = _strict_int(entry.get("viewer_count")) if entry is not None else None
    if not isinstance(stream_id, str) or not stream_id or viewers is None or viewers < 0:
        raise TwitchError("invalid Twitch stream entry")
    return StreamSnapshot(target, stream_id, viewers, url=url)


def _get_json(request: Request) -> object:
    try:
        with urlopen(request, timeout=10.0) as response:
            return cast(object, json.loads(cast(bytes, response.read())))
    except HTTPError:
        raise
    except (URLError, HTTPException, OSError, JSONDecodeError, UnicodeDecodeError) as error:
        raise TwitchError(
            f"Twitch request failed ({type(error).__name__})"
        ) from error


class TwitchClient:
    """Fetch Helix stream data with a cached client-credentials app token.

    One client is shared across all Twitch sources; polls run in worker
    threads, so a lock serializes token fetches — N logins share one token
    instead of each fetching its own.
    """

    __slots__ = ("credentials", "_app_token", "_token_lock")

    def __init__(self, credentials: TwitchCredentials) -> None:
        self.credentials = credentials
        self._app_token: str | None = None
        self._token_lock = threading.Lock()

    def _fetch_app_token_locked(self) -> str:
        request = Request(
            TOKEN_URL,
            data=urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                }
            ).encode("ascii"),
            method="POST",
        )
        try:
            payload = _get_json(request)
        except HTTPError as error:
            raise TwitchError(f"Twitch token request failed (HTTP {error.code})") from error
        token = parse_app_token(payload)
        self._app_token = token
        return token

    def _app_token_value(self) -> str:
        with self._token_lock:
            token = self._app_token
            return token if token is not None else self._fetch_app_token_locked()

    def _refresh_token(self) -> str:
        with self._token_lock:
            return self._fetch_app_token_locked()

    def _get_streams(self, token: str, target: StreamTarget) -> StreamSnapshot:
        request = Request(
            f"{STREAMS_URL}?{urlencode({'user_login': target.name})}",
            headers={
                "Client-Id": self.credentials.client_id,
                "Authorization": f"Bearer {token}",
            },
            method="GET",
        )
        return parse_stream(_get_json(request), target)

    @staticmethod
    def _streams_error(error: HTTPError) -> TwitchError:
        return TwitchError(f"Twitch streams request failed (HTTP {error.code})")

    def stream(self, target: StreamTarget) -> StreamSnapshot:
        """Return the target's snapshot, refreshing the token once after HTTP 401."""
        try:
            return self._get_streams(self._app_token_value(), target)
        except HTTPError as error:
            if error.code != 401:
                raise self._streams_error(error) from error
        try:
            return self._get_streams(self._refresh_token(), target)
        except HTTPError as error:
            raise self._streams_error(error) from error


@dataclass(frozen=True, slots=True)
class TwitchSource:
    """An asynchronous polling source for one Twitch login."""

    name: str
    target: StreamTarget
    client: TwitchClient
    poll_interval: float = 5.0

    @classmethod
    def for_login(
        cls,
        login: str,
        client: TwitchClient,
        *,
        poll_interval: float = 5.0,
    ) -> Self:
        """Create a source that polls one Twitch login, sharing one app token."""
        target = StreamTarget(Platform.TWITCH, normalize_handle(login))
        return cls(
            name=target.key,
            target=target,
            client=client,
            poll_interval=poll_interval,
        )

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        """Yield live/offline snapshots, retrying request failures after each poll."""
        while True:
            try:
                snapshot = await asyncio.to_thread(self.client.stream, self.target)
            except TwitchError as error:
                _LOGGER.warning("could not fetch Twitch stream for %s: %s", self.name, error)
            else:
                yield snapshot
            await asyncio.sleep(self.poll_interval)
