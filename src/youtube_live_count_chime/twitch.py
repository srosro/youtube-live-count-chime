"""Fetch live Twitch viewer counts through the public Helix API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from http.client import HTTPException
import json
from json import JSONDecodeError
import os
import threading
from typing import Final, Self, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from youtube_live_count_chime.models import (
    Platform,
    SourceFetchError,
    StreamSnapshot,
    StreamTarget,
    normalize_handle,
    poll_snapshots,
)


TOKEN_URL: Final = "https://id.twitch.tv/oauth2/token"
STREAMS_URL: Final = "https://api.twitch.tv/helix/streams"
_CLIENT_ID_ENV: Final = "TWITCH_CLIENT_ID"
_CLIENT_SECRET_ENV: Final = "TWITCH_CLIENT_SECRET"
# Token-endpoint statuses that mean the credentials themselves are refused.
# Everything else it returns (429 rate limits, 5xx degradation) is transient.
_CREDENTIAL_REJECTED_STATUSES: Final = frozenset({400, 401, 403})


class TwitchError(RuntimeError):
    """Base for every Twitch failure. Never raised directly — pick a subclass.

    The two subclasses split on one question: can a later poll survive this?
    Only ``TwitchRequestError`` is a ``SourceFetchError``, so only it is
    swallowed and retried by the shared poll loop.
    """


class TwitchRequestError(TwitchError, SourceFetchError):
    """Raised when one Twitch request fails in a way a later poll may survive."""


class TwitchAuthError(TwitchError):
    """Raised when the credentials are missing or Twitch refuses them.

    Either way no poll can ever succeed, so this is deliberately *not* a
    ``SourceFetchError``. Missing credentials are raised during
    ``cli.build_sources`` and caught by ``cli.main``, which exits 2 with a
    clean message before any poll loop exists. Credentials Twitch rejects
    surface later, from the poll loop, where being swallowed would hammer the
    token endpoint behind a log that looks healthy — so they tear the watcher
    down with a traceback instead. Under the launch agent that is a throttled
    respawn loop rather than a true stop, but each attempt says why in the log.
    """


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
            raise TwitchAuthError(f"{_CLIENT_ID_ENV} is not set")
        if not client_secret:
            raise TwitchAuthError(f"{_CLIENT_SECRET_ENV} is not set")
        return cls(client_id, client_secret)


def parse_app_token(payload: object) -> str:
    """Validate a client-credentials token response and return its access token."""
    value = _object_dict(payload)
    token = value.get("access_token") if value is not None else None
    if not isinstance(token, str) or not token:
        # A 200 with an unexpected body is transport-shaped (proxy, captive
        # portal, partial degradation), not a credential rejection.
        raise TwitchRequestError("invalid Twitch token response")
    return token


def parse_stream(payload: object, target: StreamTarget) -> StreamSnapshot:
    """Turn one Helix streams response for a login into a snapshot."""
    value = _object_dict(payload)
    data = value.get("data") if value is not None else None
    if not isinstance(data, list):
        raise TwitchRequestError("invalid Twitch streams response")

    if not data:
        return StreamSnapshot.offline(target)

    entry = _object_dict(cast(list[object], data)[0])
    stream_id = entry.get("id") if entry is not None else None
    viewers = _strict_int(entry.get("viewer_count")) if entry is not None else None
    if not isinstance(stream_id, str) or not stream_id or viewers is None or viewers < 0:
        raise TwitchRequestError("invalid Twitch stream entry")
    return StreamSnapshot(target, stream_id, viewers)


def _get_json(request: Request) -> object:
    try:
        with urlopen(request, timeout=10.0) as response:
            return cast(object, json.loads(cast(bytes, response.read())))
    except HTTPError:
        raise
    except (URLError, HTTPException, OSError, JSONDecodeError, UnicodeDecodeError) as error:
        raise TwitchRequestError(
            f"Twitch request failed ({type(error).__name__})"
        ) from error


class TwitchClient:
    """Fetch Helix stream data with a cached client-credentials app token.

    One client is shared across all Twitch sources; polls run in worker
    threads, so a lock serializes token fetches — N logins share one token
    instead of each fetching its own.
    """

    __slots__ = ("credentials", "_app_token", "_generation", "_token_lock")

    def __init__(self, credentials: TwitchCredentials) -> None:
        self.credentials = credentials
        self._app_token: str | None = None
        self._generation = 0
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
            message = f"Twitch token request failed (HTTP {error.code})"
            if error.code in _CREDENTIAL_REJECTED_STATUSES:
                raise TwitchAuthError(message) from error
            raise TwitchRequestError(message) from error
        token = parse_app_token(payload)
        self._app_token = token
        self._generation += 1
        return token

    def _token(self) -> tuple[str, int]:
        """Return the current token and its generation, fetching once if unset."""
        with self._token_lock:
            token = self._app_token
            if token is None:
                token = self._fetch_app_token_locked()
            return token, self._generation

    def _refresh_token(self, seen_generation: int) -> str:
        """Fetch a new token unless another thread already rotated it."""
        with self._token_lock:
            if self._generation != seen_generation:
                assert self._app_token is not None  # a bumped generation stored one
                return self._app_token
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
    def _streams_error(error: HTTPError) -> TwitchRequestError:
        return TwitchRequestError(f"Twitch streams request failed (HTTP {error.code})")

    def stream(self, target: StreamTarget) -> StreamSnapshot:
        """Return the target's snapshot, refreshing the token once after HTTP 401."""
        token, generation = self._token()
        try:
            return self._get_streams(token, target)
        except HTTPError as error:
            if error.code != 401:
                raise self._streams_error(error) from error
        try:
            return self._get_streams(self._refresh_token(generation), target)
        except HTTPError as error:
            if error.code == 401:
                # _refresh_token returns a token newer than the one that just
                # 401'd (minted here or by a concurrent refresh), so a second
                # 401 is a Client-Id/token mismatch, not expiry — retrying
                # never helps.
                raise TwitchAuthError(
                    "Twitch streams rejected a refreshed token"
                ) from error
            raise self._streams_error(error) from error


@dataclass(frozen=True, slots=True)
class TwitchSource:
    """An asynchronous polling source for one Twitch login."""

    name: str
    target: StreamTarget
    client: TwitchClient

    @classmethod
    def for_login(cls, login: str, client: TwitchClient) -> Self:
        """Create a source that polls one Twitch login, sharing one app token."""
        target = StreamTarget(Platform.TWITCH, normalize_handle(login))
        return cls(name=target.key, target=target, client=client)

    def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        """Yield live/offline snapshots, retrying request failures after each poll."""
        return poll_snapshots(self.name, lambda: self.client.stream(self.target))
