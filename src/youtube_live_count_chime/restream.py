"""Restream OAuth, channel discovery, and live-status WebSocket support."""

from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
import getpass
from http.client import HTTPResponse
import json
from json import JSONDecodeError
import logging
import subprocess
from time import monotonic
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect

from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget


KEYCHAIN_PREFIX: Final = "youtube-live-count-chime-restream"
CLIENT_ID_SERVICE: Final = f"{KEYCHAIN_PREFIX}-client-id"
CLIENT_SECRET_SERVICE: Final = f"{KEYCHAIN_PREFIX}-client-secret"
ACCESS_TOKEN_SERVICE: Final = f"{KEYCHAIN_PREFIX}-access-token"
REFRESH_TOKEN_SERVICE: Final = f"{KEYCHAIN_PREFIX}-refresh-token"
TOKEN_URL: Final = "https://api.restream.io/oauth/token"
CHANNELS_URL: Final = "https://api.restream.io/v2/user/channel/all"
WEBSOCKET_URL: Final = "wss://streaming.api.restream.io/ws"
TWITCH_PLATFORM_ID: Final = 1
TOKEN_REFRESH_SECONDS: Final = 50.0 * 60.0
RETRY_SECONDS: Final = 5.0
_KEYCHAIN_SERVICES: Final = frozenset(
    {
        CLIENT_ID_SERVICE,
        CLIENT_SECRET_SERVICE,
        ACCESS_TOKEN_SERVICE,
        REFRESH_TOKEN_SERVICE,
    }
)
_CHANNEL_SCOPES: Final = frozenset(
    {"channels.default.read", "channels.read"}
)
_STREAM_SCOPES: Final = frozenset({"stream.default.read", "stream.read"})
_LOGGER: Final = logging.getLogger(__name__)
_WEBSOCKET_LOGGER: Final = logging.Logger(
    f"{__name__}.websocket",
    level=logging.CRITICAL + 1,
)
_WEBSOCKET_LOGGER.propagate = False
_WEBSOCKET_LOGGER.addHandler(logging.NullHandler())


class RestreamError(RuntimeError):
    """Raised when Restream credentials or responses cannot be used."""


class KeychainStore:
    """Read and update the four Restream credentials in macOS Keychain."""

    __slots__ = ("account",)

    def __init__(self, account: str | None = None) -> None:
        self.account = account if account is not None else getpass.getuser()

    @staticmethod
    def _validate_service(service: str) -> None:
        if service not in _KEYCHAIN_SERVICES:
            raise RestreamError("unknown Restream Keychain service")

    def get(self, service: str) -> str:
        """Return one credential without exposing its value on failure."""
        self._validate_service(service)
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-a",
                    self.account,
                    "-s",
                    service,
                    "-w",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RestreamError(
                f"could not read Restream credential ({type(error).__name__})"
            ) from None
        value = result.stdout.rstrip("\r\n")
        if not value:
            raise RestreamError("Restream credential is empty")
        return value

    def set(self, service: str, value: str) -> None:
        """Replace one credential without exposing its value on failure."""
        self._validate_service(service)
        if not value:
            raise RestreamError("cannot store an empty Restream credential")
        try:
            subprocess.run(
                [
                    "/usr/bin/security",
                    "add-generic-password",
                    "-U",
                    "-a",
                    self.account,
                    "-s",
                    service,
                    "-w",
                ],
                check=True,
                capture_output=True,
                input=f"{value}\n",
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RestreamError(
                f"could not store Restream credential ({type(error).__name__})"
            ) from None


@dataclass(frozen=True, slots=True)
class TokenPair:
    """A validated Restream access/refresh token response."""

    access_token: str
    refresh_token: str
    expires_in: int
    scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class RestreamChannel:
    """One channel connected to a Restream account."""

    channel_id: int
    streaming_platform_id: int
    identifier: str
    display_name: str
    url: str
    twitch_handle: str | None

    @property
    def id(self) -> int:
        """Return the Restream channel ID using the API field's name."""
        return self.channel_id


def _object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _strict_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_token_payload(payload: object) -> TokenPair:
    """Validate a Restream refresh response and its required capabilities."""
    value = _object_dict(payload)
    if value is None:
        raise RestreamError("invalid Restream token response")

    access_token = value.get("access_token")
    refresh_token = value.get("refresh_token")
    expires_in = _strict_int(value.get("expires_in"))
    raw_scopes = value.get("scopeJson")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
        or expires_in is None
        or expires_in <= 0
        or not isinstance(raw_scopes, list)
        or any(not isinstance(scope, str) for scope in raw_scopes)
    ):
        raise RestreamError("invalid Restream token response")

    scopes = frozenset(cast(list[str], raw_scopes))
    if scopes.isdisjoint(_CHANNEL_SCOPES) or scopes.isdisjoint(_STREAM_SCOPES):
        raise RestreamError("Restream token lacks required capabilities")
    return TokenPair(access_token, refresh_token, expires_in, scopes)


def _twitch_handle(url: str) -> str | None:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname is None
        or parts.hostname.lower() not in {"twitch.tv", "www.twitch.tv"}
        or parts.query
        or parts.fragment
    ):
        return None
    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) != 1:
        return None
    return path_parts[0]


def parse_channel(payload: object) -> RestreamChannel:
    """Validate one Restream channel and derive its Twitch handle."""
    value = _object_dict(payload)
    if value is None:
        raise RestreamError("invalid Restream channel response")

    channel_id = _strict_int(value.get("id"))
    platform_id = _strict_int(value.get("streamingPlatformId"))
    identifier = value.get("identifier")
    display_name = value.get("displayName")
    url = value.get("url")
    if (
        channel_id is None
        or channel_id < 0
        or platform_id is None
        or platform_id < 0
        or not isinstance(identifier, str)
        or not isinstance(display_name, str)
        or not display_name
        or not isinstance(url, str)
    ):
        raise RestreamError("invalid Restream channel response")

    twitch_handle: str | None = None
    if platform_id == TWITCH_PLATFORM_ID:
        twitch_handle = _twitch_handle(url)
        if not identifier or twitch_handle is None:
            raise RestreamError("invalid Restream Twitch channel")
    return RestreamChannel(
        channel_id,
        platform_id,
        identifier,
        display_name,
        url,
        twitch_handle,
    )


def parse_status_message(
    message: object,
    targets_by_channel: Mapping[int, StreamTarget],
) -> StreamSnapshot | None:
    """Parse one mapped Twitch status event, ignoring every other message."""
    if isinstance(message, (str, bytes, bytearray)):
        try:
            message = cast(object, json.loads(message))
        except (JSONDecodeError, UnicodeDecodeError):
            return None
    value = _object_dict(message)
    if value is None or value.get("action") != "updateStatuses":
        return None

    channel_id = _strict_int(value.get("channelId"))
    platform_id = _strict_int(value.get("platformId"))
    if channel_id is None or platform_id != TWITCH_PLATFORM_ID:
        return None
    target = targets_by_channel.get(channel_id)
    if target is None or target.platform is not Platform.TWITCH:
        return None

    online = value.get("online")
    if not isinstance(online, bool):
        return None

    raw_viewers = value.get("viewers")
    viewers = None if raw_viewers is None else _strict_int(raw_viewers)
    if raw_viewers is not None and (viewers is None or viewers < 0):
        return None

    raw_followers = value.get("followers")
    followers = None if raw_followers is None else _strict_int(raw_followers)
    if raw_followers is not None and (followers is None or followers < 0):
        return None

    url = f"https://www.twitch.tv/{quote(target.name, safe='')}"
    if not online:
        return StreamSnapshot.offline(target, url=url)
    if viewers is None:
        return None

    event_id = value.get("eventId")
    event_identifier = value.get("eventIdentifier")
    stream_id = (
        event_id
        if isinstance(event_id, str) and event_id
        else event_identifier
        if isinstance(event_identifier, str) and event_identifier
        else None
    )
    if stream_id is None:
        return None
    return StreamSnapshot(target, stream_id, viewers, followers, url)


def _decode_response(response: HTTPResponse) -> object:
    try:
        return cast(object, json.loads(response.read()))
    except (JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise RestreamError("invalid JSON from Restream") from error


@dataclass(slots=True)
class RestreamClient:
    """Use Keychain-backed OAuth credentials with the Restream HTTP API."""

    store: KeychainStore = field(default_factory=KeychainStore)

    def access_token(self) -> str:
        """Return the current Keychain access token."""
        return self.store.get(ACCESS_TOKEN_SERVICE)

    def refresh(self) -> TokenPair:
        """Refresh OAuth tokens and persist both rotated values."""
        client_id = self.store.get(CLIENT_ID_SERVICE)
        client_secret = self.store.get(CLIENT_SECRET_SERVICE)
        refresh_token = self.store.get(REFRESH_TOKEN_SERVICE)
        authorization = b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            TOKEN_URL,
            data=urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                }
            ).encode("ascii"),
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                pair = parse_token_payload(_decode_response(response))
        except RestreamError:
            raise
        except (HTTPError, URLError, OSError) as error:
            raise RestreamError(
                f"Restream token refresh failed ({type(error).__name__})"
            ) from None

        self.store.set(REFRESH_TOKEN_SERVICE, pair.refresh_token)
        self.store.set(ACCESS_TOKEN_SERVICE, pair.access_token)
        return pair

    @staticmethod
    def _get_channels(access_token: str) -> list[RestreamChannel]:
        request = Request(
            CHANNELS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                payload = _decode_response(response)
        except HTTPError:
            raise
        except RestreamError:
            raise
        except (URLError, OSError) as error:
            raise RestreamError(
                f"Restream channel request failed ({type(error).__name__})"
            ) from None

        if isinstance(payload, dict):
            payload = cast(dict[str, object], payload).get("data")
        if not isinstance(payload, list):
            raise RestreamError("invalid Restream channel list")
        return [parse_channel(item) for item in cast(list[object], payload)]

    def list_channels(self) -> list[RestreamChannel]:
        """List channels, refreshing and retrying once after HTTP 401."""
        access_token = self.access_token()
        try:
            return self._get_channels(access_token)
        except HTTPError as error:
            if error.code != 401:
                raise RestreamError(
                    f"Restream channel request failed ({type(error).__name__})"
                ) from None

        pair = self.refresh()
        try:
            return self._get_channels(pair.access_token)
        except HTTPError as error:
            raise RestreamError(
                f"Restream channel request failed ({type(error).__name__})"
            ) from None


def _websocket_status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


@dataclass(frozen=True, slots=True)
class RestreamSource:
    """Stream mapped Restream Twitch statuses over a resilient WebSocket."""

    client: RestreamClient
    targets_by_channel: Mapping[int, StreamTarget]
    refresh_interval: float = TOKEN_REFRESH_SECONDS
    retry_interval: float = RETRY_SECONDS
    name: str = field(default="restream", init=False)

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        """Yield mapped statuses, refreshing credentials and reconnecting."""
        refreshed_after_unauthorized = False
        access_token: str | None = None
        refresh_at = 0.0
        while True:
            try:
                if access_token is None:
                    pair = await asyncio.to_thread(self.client.refresh)
                    access_token = pair.access_token
                    refresh_at = monotonic() + self.refresh_interval
                url = (
                    f"{WEBSOCKET_URL}?accessToken="
                    f"{quote(access_token, safe='')}"
                )
                async with connect(url, logger=_WEBSOCKET_LOGGER) as websocket:
                    refreshed_after_unauthorized = False
                    while True:
                        remaining = refresh_at - monotonic()
                        if remaining <= 0:
                            pair = await asyncio.to_thread(self.client.refresh)
                            access_token = pair.access_token
                            refresh_at = monotonic() + self.refresh_interval
                            break
                        try:
                            async with asyncio.timeout(remaining):
                                message = await websocket.recv()
                        except TimeoutError:
                            pair = await asyncio.to_thread(self.client.refresh)
                            access_token = pair.access_token
                            refresh_at = monotonic() + self.refresh_interval
                            break
                        snapshot = parse_status_message(
                            message,
                            self.targets_by_channel,
                        )
                        if snapshot is not None:
                            yield snapshot
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if (
                    _websocket_status_code(error) == 401
                    and not refreshed_after_unauthorized
                ):
                    try:
                        pair = await asyncio.to_thread(self.client.refresh)
                    except Exception as refresh_error:
                        _LOGGER.warning(
                            "Restream token refresh failed (%s)",
                            type(refresh_error).__name__,
                        )
                        await asyncio.sleep(self.retry_interval)
                    else:
                        access_token = pair.access_token
                        refreshed_after_unauthorized = True
                        refresh_at = monotonic() + self.refresh_interval
                    continue
                _LOGGER.warning(
                    "Restream WebSocket disconnected (%s)",
                    type(error).__name__,
                )
                await asyncio.sleep(self.retry_interval)
