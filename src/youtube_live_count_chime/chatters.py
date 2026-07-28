"""Name Twitch arrivals by diffing the channel's chat roster.

Twitch exposes no viewer identities, so a rise in ``viewer_count`` is correlated
with a new entry in the chat roster. That is a proxy: it misses embed and some
mobile viewers, and includes people idling in chat. Every failure path here
degrades to ``()`` so a missing name never costs the notification.

The monitor samples this on every live poll of an authorized Twitch channel —
not only on a rise — so each diff spans exactly one poll interval. That costs
one Helix chatters request per poll per authorized channel (12/min per channel
at the 5s cadence), comfortably inside the Helix budget.
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
    TwitchAuthError,
    TwitchRequestError,
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
        raise TwitchRequestError("invalid Twitch chatters response")

    logins: set[str] = set()
    for item in cast("list[object]", data):
        entry = object_dict(item)
        if entry is None:
            raise TwitchRequestError("invalid Twitch chatter entry")
        login = entry.get("user_login")
        if not isinstance(login, str) or not login:
            raise TwitchRequestError("invalid Twitch chatter entry")
        logins.add(login)
    return frozenset(logins)


@dataclass(frozen=True, slots=True)
class ChatterClient:
    """Read a channel's chat roster with its broadcaster's user token."""

    credentials: TwitchCredentials
    store: _TokenSaver
    # Access tokens Helix still rejected after a refresh — a permanent 401 (the
    # moderator:read:chatters scope was never granted). Retrying would burn a
    # refresh rotation every poll, and Twitch rate-limits refreshes and
    # invalidates a redeemed one, so the loop could destroy the stored grant.
    # Keyed by the rejected token, not its login, so a token a later --auth
    # stored is tried rather than short-circuited until the watcher restarts.
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
            raise TwitchRequestError(f"token refresh failed (HTTP {error.code})") from error
        fields = object_dict(payload)
        if fields is None:
            raise TwitchRequestError("invalid Twitch refresh response")
        access = fields.get("access_token")
        refresh = fields.get("refresh_token")
        if not isinstance(access, str) or not access:
            raise TwitchRequestError("invalid Twitch refresh response")
        rotated = StoredToken(
            token.login,
            token.user_id,
            access,
            refresh if isinstance(refresh, str) and refresh else token.refresh_token,
        )
        try:
            self.store.save(rotated)
        except TokenStoreError:
            # The POST above redeemed the old refresh token, so the previous
            # grant is dead and the rotated one is now lost. Later polls would
            # re-present the stale stored token and refresh against a redeemed
            # one; memoize it, on the same one-telling path as a permanent 401.
            self._unauthorized.add(token.access_token)
            _LOGGER.error(
                "Could not store the refreshed Twitch token for %s, and the "
                "previous one is no longer valid. Repair the token store, then "
                "run --auth %s again. Arrivals on that channel will not be "
                "named until then.",
                token.login,
                token.login,
            )
            raise
        return rotated

    def _unauthorized_error(self, login: str) -> TwitchAuthError:
        return TwitchAuthError(f"{login} is not authorized to read its own chat roster")

    def chatters(self, token: StoredToken) -> frozenset[str]:
        """Return the chat roster, refreshing the user token once after HTTP 401."""
        if token.access_token in self._unauthorized:
            raise self._unauthorized_error(token.login)
        try:
            return self._request(token)
        except HTTPError as error:
            if error.code != 401:
                raise TwitchRequestError(
                    f"Twitch chatters request failed (HTTP {error.code})"
                ) from error
        rotated = self._refresh(token)
        try:
            return self._request(rotated)
        except HTTPError as error:
            if error.code != 401:
                raise TwitchRequestError(
                    f"Twitch chatters request failed (HTTP {error.code})"
                ) from error
            # A freshly refreshed token rejected again is permanent, not an
            # expiry. Remember both: the rotated one is what the store now
            # holds, the original what a stale caller would present.
            self._unauthorized.update((token.access_token, rotated.access_token))
            _LOGGER.error(
                "Twitch refuses the chat roster for %s. Run --auth %s again, "
                "signed in as the broadcaster, to grant the %s scope. Arrivals "
                "on that channel will not be named until then.",
                token.login,
                token.login,
                CHATTERS_SCOPE,
            )
            raise self._unauthorized_error(token.login) from error


@dataclass(slots=True)
class _ChannelState:
    """What is remembered about one channel between polls.

    ``roster`` is dropped by every failure path, so the next successful read
    reseeds instead of diffing across the gap and naming someone who joined
    during it. ``cause`` is the outage currently being suffered — these persist
    across polls, so warn only on the transition *into* one and re-arm on
    success. It holds the cause, not the message: the roster branch raises
    three exceptions behind one message, so keying by message would leave an
    operator holding "Helix is down" after the cause had become a bad token.
    """

    stream_id: str | None = None
    roster: frozenset[str] | None = None
    cause: str | None = None


class TwitchChatterNamer:
    """Name arrivals by diffing successive chat rosters per broadcast."""

    __slots__ = ("client", "store", "_state")

    def __init__(self, client: _Chatters, store: _TokenLookup) -> None:
        self.client = client
        self.store = store
        self._state: dict[str, _ChannelState] = {}

    def _degrade(
        self, state: _ChannelState, key: str, message: str, error: Exception
    ) -> tuple[str, ...]:
        """Drop the stale roster, warn once per cause, and yield no names."""
        state.roster = None
        cause = f"{message}:{type(error).__name__}"
        if state.cause != cause:
            state.cause = cause
            _LOGGER.warning("%s for %s: %s", message, key, error)
        return ()

    def invalidate(self, target: StreamTarget) -> None:
        """Drop a channel's roster baseline after a failed poll, so it reseeds.

        Diffing across the gap would name everyone who joined during it.
        ``cause`` survives: a failed *stream* poll is neither the transition
        into a roster outage nor a recovery, so re-arming would re-warn per flap.
        """
        self._state.setdefault(target.key, _ChannelState()).roster = None

    async def arrivals(self, target: StreamTarget, stream_id: str) -> tuple[str, ...]:
        """Return newly-seen chatters, or ``()`` when a name cannot be known."""
        if target.platform is not Platform.TWITCH:
            # target.name carries no platform information, so the store lookup
            # below is a bare login — a YouTube target sharing a handle with an
            # authorized Twitch account would otherwise be named from that
            # account's chat roster. YouTube arrivals are never named.
            return ()
        state = self._state.setdefault(target.key, _ChannelState())
        # Only the expected degradations are absorbed: a transient request
        # failure, a permanent authorization failure, and an unreadable token
        # store. Anything else is a bug, and must reach the monitor's boundary
        # handler rather than being swallowed as "no names" on every rise
        # forever.
        try:
            token = self.store.get(target.name)
        except TokenStoreError as error:
            return self._degrade(state, target.key, "could not read the token store", error)
        if token is None:
            state.roster = None
            return ()
        try:
            current = await asyncio.to_thread(self.client.chatters, token)
        except (TwitchRequestError, TwitchAuthError, TokenStoreError) as error:
            # TwitchAuthError is permanent, but the roster is a nice-to-have:
            # a channel that never grants the scope must not tear the watcher
            # down. The permanent-401 memoization above keeps this to one
            # Twitch request, and the error log there fires once.
            # TokenStoreError can surface here too — _refresh saves the
            # rotated token through the store.
            return self._degrade(state, target.key, "could not read chat roster", error)

        # Seed on the first sight of a channel, on a new broadcast, and after
        # any outage: diffing across those gaps reports arrivals that are not.
        previous = state.roster if state.stream_id == stream_id else None
        state.stream_id, state.roster, state.cause = stream_id, current, None
        if previous is None:
            return ()
        return tuple(sorted(current - previous))
