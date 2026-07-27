import unittest
from email.message import Message
from typing import cast
from unittest.mock import patch
from urllib.error import HTTPError

from youtube_live_count_chime.chatters import ChatterClient, TwitchChatterNamer
from youtube_live_count_chime.models import Platform, StreamTarget
from youtube_live_count_chime.tokens import StoredToken, TokenStore, TokenStoreError
from youtube_live_count_chime.twitch import TwitchCredentials, TwitchError


TARGET = StreamTarget(Platform.TWITCH, "watchmepivot")
TOKEN = StoredToken("watchmepivot", "42", "access-placeholder", "refresh-placeholder")


class _FakeStore:
    def __init__(self, token: StoredToken | None) -> None:
        self._token = token

    def get(self, login: str) -> StoredToken | None:
        return self._token


class _FakeClient:
    """Serve queued chat rosters and count how often it was asked."""

    def __init__(self, rosters: list[frozenset[str]]) -> None:
        self._rosters = list(rosters)
        self.calls = 0

    def chatters(self, token: StoredToken) -> frozenset[str]:
        self.calls += 1
        return self._rosters.pop(0)


class _ExplodingClient:
    def chatters(self, token: StoredToken) -> frozenset[str]:
        raise RuntimeError("chatters unavailable")


class _ExplodingStore:
    """A token store whose backing file has gone corrupt mid-run."""

    def get(self, login: str) -> StoredToken | None:
        raise TokenStoreError("token file is corrupt")


class ChatterNamerTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_call_seeds_silently(self) -> None:
        # Otherwise the first tick after startup announces everyone already in chat.
        namer = TwitchChatterNamer(_FakeClient([frozenset({"lurker_a", "lurker_b"})]), _FakeStore(TOKEN))

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

    async def test_names_only_who_arrived_since_the_previous_call(self) -> None:
        client = _FakeClient(
            [frozenset({"lurker_a"}), frozenset({"lurker_a", "joe_doe"})]
        )
        namer = TwitchChatterNamer(client, _FakeStore(TOKEN))
        await namer.arrivals(TARGET, "stream-1")

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ("joe_doe",))

    async def test_names_are_sorted_for_a_deterministic_title(self) -> None:
        client = _FakeClient([frozenset(), frozenset({"zed", "amy"})])
        namer = TwitchChatterNamer(client, _FakeStore(TOKEN))
        await namer.arrivals(TARGET, "stream-1")

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ("amy", "zed"))

    async def test_a_new_broadcast_reseeds_instead_of_diffing_the_old_roster(
        self,
    ) -> None:
        client = _FakeClient(
            [
                frozenset({"lurker_a"}),
                frozenset({"lurker_a", "joe_doe"}),  # stream-2 seed roster
                frozenset({"lurker_a", "joe_doe", "amy"}),  # stream-2 next roster
            ]
        )
        namer = TwitchChatterNamer(client, _FakeStore(TOKEN))
        await namer.arrivals(TARGET, "stream-1")

        self.assertEqual(await namer.arrivals(TARGET, "stream-2"), ())

        # Diffed against stream-2's seed roster ({lurker_a, joe_doe}), not
        # stream-1's ({lurker_a}) — an implementation that recorded the new
        # stream_id but kept the old roster would wrongly report joe_doe here.
        self.assertEqual(await namer.arrivals(TARGET, "stream-2"), ("amy",))

    async def test_unauthorized_channel_yields_no_names_without_calling_out(
        self,
    ) -> None:
        client = _FakeClient([frozenset({"joe_doe"})])
        namer = TwitchChatterNamer(client, _FakeStore(None))

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())
        self.assertEqual(client.calls, 0)

    async def test_a_chatters_failure_degrades_to_no_names(self) -> None:
        namer = TwitchChatterNamer(_ExplodingClient(), _FakeStore(TOKEN))

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

    async def test_a_corrupt_token_store_degrades_to_no_names(self) -> None:
        # A malformed token file must not escape as an exception and kill the
        # watcher for every channel — it degrades to () like every other
        # arrivals() failure path.
        namer = TwitchChatterNamer(_FakeClient([]), _ExplodingStore())

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())


class ChatterClientTests(unittest.TestCase):
    def _http_error(self, code: int) -> HTTPError:
        return HTTPError("https://api.twitch.tv", code, "nope", Message(), None)

    def test_expired_token_is_refreshed_once_and_the_read_retried(self) -> None:
        saved: list[StoredToken] = []

        class _Store:
            def get(self, login: str) -> StoredToken | None:
                return TOKEN

            def save(self, token: StoredToken) -> None:
                saved.append(token)

        store = _Store()
        client = ChatterClient(TwitchCredentials("id", "secret"), cast(TokenStore, store))
        responses: list[object] = [
            self._http_error(401),  # first chatters read: token expired
            {"access_token": "fresh", "refresh_token": "rotated"},
            {"data": [{"user_login": "joe_doe"}]},  # retried read
        ]

        def fake_get_json(request: object) -> object:
            item = responses.pop(0)
            if isinstance(item, HTTPError):
                raise item
            return item

        with patch(
            "youtube_live_count_chime.chatters.get_json", side_effect=fake_get_json
        ):
            self.assertEqual(client.chatters(TOKEN), frozenset({"joe_doe"}))

        self.assertEqual(responses, [])  # exactly one refresh, one retry
        self.assertEqual(len(saved), 1)  # the rotated token was persisted

    def test_a_non_401_failure_is_not_retried(self) -> None:
        client = ChatterClient(TwitchCredentials("id", "secret"), cast(TokenStore, None))
        responses: list[object] = [self._http_error(500)]

        def fake_get_json(request: object) -> object:
            item = responses.pop(0)
            if isinstance(item, HTTPError):
                raise item
            return item

        with patch(
            "youtube_live_count_chime.chatters.get_json", side_effect=fake_get_json
        ):
            with self.assertRaises(TwitchError):
                client.chatters(TOKEN)

        self.assertEqual(responses, [])  # exactly one call: no refresh, no retry


if __name__ == "__main__":
    unittest.main()
