"""Name Twitch arrivals by diffing the channel's chat roster.

Twitch exposes no viewer identities, so a rise in ``viewer_count`` is correlated
with a new entry in the chat roster. That is a proxy: it misses embed and some
mobile viewers, and includes people idling in chat. Every failure path here
degrades to ``()`` so a missing name never costs the notification.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Final, Protocol, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from youtube_live_count_chime.models import Platform, StreamTarget
from youtube_live_count_chime.tokens import StoredToken, TokenStoreError
from youtube_live_count_chime.twitch import (
    TOKEN_URL,
    TwitchCredentials,
    TwitchError,
    get_json,
    object_dict,
)


CHATTERS_URL: Final = "https://api.twitch.tv/helix/chat/chatters"
# The Helix scope this endpoint requires; auth.py requests exactly this one.
CHATTERS_SCOPE: Final = "moderator:read:chatters"
# Helix caps a chatters page at 1000 and this code does not paginate, so a
# channel with more than 1000 chatters would see roster churn (arrivals
# missed or falsely reported) as the returned page shifts between polls.
_PAGE_SIZE: Final = 1000
_LOGGER: Final = logging.getLogger(__name__)


class _Chatters(Protocol):
    def chatters(self, token: StoredToken) -> frozenset[str]: ...


class _TokenLookup(Protocol):
    def get(self, login: str) -> StoredToken | None: ...


class _TokenSaver(Protocol):
    def save(self, token: StoredToken) -> None: ...


def parse_chatters(payload: object) -> frozenset[str]:
    """Extract chatter logins from a Helix chatters response."""
    value = object_dict(payload)
    data = value.get("data") if value is not None else None
    if not isinstance(data, list):
        raise TwitchError("invalid Twitch chatters response")

    logins: set[str] = set()
    for item in cast("list[object]", data):
        entry = object_dict(item)
        if entry is None:
            raise TwitchError("invalid Twitch chatter entry")
        login = entry.get("user_login")
        if not isinstance(login, str) or not login:
            raise TwitchError("invalid Twitch chatter entry")
        logins.add(login)
    return frozenset(logins)


@dataclass(frozen=True, slots=True)
class ChatterClient:
    """Read a channel's chat roster with its broadcaster's user token."""

    credentials: TwitchCredentials
    store: _TokenSaver
    # Logins whose token was still rejected after a refresh. Helix answers 401
    # for permanent conditions too (the moderator:read:chatters scope was never
    # granted, or the authorized user is neither the broadcaster nor a
    # moderator), and retrying those would burn a refresh rotation on every
    # rise — Twitch rate-limits refreshes and invalidates a redeemed refresh
    # token, so the loop could eventually destroy the stored grant.
    _unauthorized: set[str] = field(default_factory=set)

    def _request(self, token: StoredToken) -> frozenset[str]:
        # The broadcaster reading their own channel: moderator_id == broadcaster_id.
        query = urlencode(
            {
                "broadcaster_id": token.user_id,
                "moderator_id": token.user_id,
                "first": _PAGE_SIZE,
            }
        )
        request = Request(
            f"{CHATTERS_URL}?{query}",
            headers={
                "Client-Id": self.credentials.client_id,
                "Authorization": f"Bearer {token.access_token}",
            },
            method="GET",
        )
        return parse_chatters(get_json(request))

    def _refresh(self, token: StoredToken) -> StoredToken:
        request = Request(
            TOKEN_URL,
            data=urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                }
            ).encode("ascii"),
            method="POST",
        )
        try:
            payload = get_json(request)
        except HTTPError as error:
            raise TwitchError(f"token refresh failed (HTTP {error.code})") from error
        fields = object_dict(payload)
        if fields is None:
            raise TwitchError("invalid Twitch refresh response")
        access = fields.get("access_token")
        refresh = fields.get("refresh_token")
        if not isinstance(access, str) or not access:
            raise TwitchError("invalid Twitch refresh response")
        rotated = StoredToken(
            token.login,
            token.user_id,
            access,
            refresh if isinstance(refresh, str) and refresh else token.refresh_token,
        )
        self.store.save(rotated)
        return rotated

    def _unauthorized_error(self, login: str) -> TwitchError:
        return TwitchError(f"{login} is not authorized to read its own chat roster")

    def chatters(self, token: StoredToken) -> frozenset[str]:
        """Return the chat roster, refreshing the user token once after HTTP 401."""
        if token.login in self._unauthorized:
            raise self._unauthorized_error(token.login)
        try:
            return self._request(token)
        except HTTPError as error:
            if error.code != 401:
                raise TwitchError(
                    f"Twitch chatters request failed (HTTP {error.code})"
                ) from error
        try:
            return self._request(self._refresh(token))
        except HTTPError as error:
            if error.code != 401:
                raise TwitchError(
                    f"Twitch chatters request failed (HTTP {error.code})"
                ) from error
            # A freshly refreshed token rejected again is a permanent 401, not
            # an expiry. Remember it so no further rise retries: logged once.
            self._unauthorized.add(token.login)
            _LOGGER.error(
                "Twitch refuses the chat roster for %s. Run --auth %s again, "
                "signed in as an account that is the broadcaster or one of its "
                "moderators, to grant the %s scope. Arrivals on that channel "
                "will not be named until then.",
                token.login,
                token.login,
                CHATTERS_SCOPE,
            )
            raise self._unauthorized_error(token.login) from error


class TwitchChatterNamer:
    """Name arrivals by diffing successive chat rosters per broadcast."""

    __slots__ = ("client", "store", "_state")

    def __init__(self, client: _Chatters, store: _TokenLookup) -> None:
        self.client = client
        self.store = store
        # key -> (stream_id, roster) captured at the previous query
        self._state: dict[str, tuple[str, frozenset[str]]] = {}

    async def arrivals(self, target: StreamTarget, stream_id: str) -> tuple[str, ...]:
        """Return newly-seen chatters, or ``()`` when a name cannot be known."""
        if target.platform is not Platform.TWITCH:
            # target.name carries no platform information, so the store lookup
            # below is a bare login — a YouTube target sharing a handle with an
            # authorized Twitch account would otherwise be named from that
            # account's chat roster. YouTube arrivals are never named.
            return ()
        # Only the two expected degradations are absorbed: anything else is a
        # bug, and must reach the monitor's boundary handler rather than being
        # swallowed as "no names" on every rise forever.
        try:
            token = self.store.get(target.name)
        except TokenStoreError as error:
            _LOGGER.warning("could not read the token store for %s: %s", target.key, error)
            return ()
        if token is None:
            return ()
        try:
            current = await asyncio.to_thread(self.client.chatters, token)
        except TwitchError as error:
            _LOGGER.warning("could not read chat roster for %s: %s", target.key, error)
            return ()

        previous = self._state.get(target.key)
        self._state[target.key] = (stream_id, current)
        # Seed on the first sight of a channel, and again on a new broadcast:
        # diffing against the previous stream's roster would under-report.
        if previous is None or previous[0] != stream_id:
            return ()
        return tuple(sorted(current - previous[1]))
