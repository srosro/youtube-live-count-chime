import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from youtube_live_count_chime.chatters import (
    ChatterClient,
    TwitchChatterNamer,
    parse_chatters,
)
from youtube_live_count_chime.models import Platform, StreamTarget
from youtube_live_count_chime.tokens import StoredToken, TokenStoreError
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
        raise TwitchError("chatters unavailable")


class _BuggyClient:
    """A namer collaborator with a programming error, not a degradation."""

    def chatters(self, token: StoredToken) -> frozenset[str]:
        raise AttributeError("StoredToken has no attribute 'acess_token'")


class _ExplodingStore:
    """A token store whose backing file has gone corrupt mid-run."""

    def get(self, login: str) -> StoredToken | None:
        raise TokenStoreError("token file is corrupt")


class ParseChattersTests(unittest.TestCase):
    def test_well_formed_payload_yields_the_logins(self) -> None:
        payload = {"data": [{"user_login": "joe_doe"}, {"user_login": "amy"}]}

        self.assertEqual(parse_chatters(payload), frozenset({"joe_doe", "amy"}))

    def test_a_payload_that_is_not_a_chatters_object_is_rejected(self) -> None:
        # What a proxy's HTML error page or a truncated body actually parses to.
        for payload in ("nope", {"data": "x"}, {}):
            with self.subTest(payload=payload):
                with self.assertRaises(TwitchError):
                    parse_chatters(payload)

    def test_a_non_dict_entry_is_rejected(self) -> None:
        payload = {"data": [{"user_login": "joe_doe"}, "not-a-dict"]}

        with self.assertRaises(TwitchError):
            parse_chatters(payload)

    def test_a_missing_or_empty_user_login_is_rejected(self) -> None:
        for entry in ({"nickname": "joe_doe"}, {"user_login": ""}):
            with self.subTest(entry=entry):
                with self.assertRaises(TwitchError):
                    parse_chatters({"data": [entry]})


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

    async def test_a_programming_error_is_not_swallowed_as_no_names(self) -> None:
        # Only TwitchError/TokenStoreError are degradations. A bug must escape
        # to monitor's boundary handler instead of silently killing naming for
        # the rest of the run while the watcher reports healthy.
        namer = TwitchChatterNamer(_BuggyClient(), _FakeStore(TOKEN))

        with self.assertRaises(AttributeError):
            await namer.arrivals(TARGET, "stream-1")

    async def test_a_corrupt_token_store_degrades_to_no_names(self) -> None:
        # A malformed token file must not escape as an exception and kill the
        # watcher for every channel — it degrades to () like every other
        # arrivals() failure path.
        namer = TwitchChatterNamer(_FakeClient([]), _ExplodingStore())

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())


class _RecordingStore:
    """Saving a rotated token is all ChatterClient asks of a store."""

    def __init__(self) -> None:
        self.saved: list[StoredToken] = []

    def save(self, token: StoredToken) -> None:
        self.saved.append(token)


class ChatterClientTests(unittest.TestCase):
    def _http_error(self, code: int) -> HTTPError:
        return HTTPError("https://api.twitch.tv", code, "nope", Message(), None)

    def test_expired_token_is_refreshed_once_and_the_read_retried(self) -> None:
        store = _RecordingStore()
        client = ChatterClient(TwitchCredentials("id", "secret"), store)
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
        self.assertEqual(len(store.saved), 1)  # the rotated token was persisted

    def test_a_permanent_401_is_remembered_instead_of_refreshed_every_rise(self) -> None:
        # A missing moderator:read:chatters scope answers 401 forever. Retrying
        # would POST a refresh per rise, and Twitch rate-limits refreshes and
        # invalidates a redeemed refresh token — eventually destroying the grant.
        store = _RecordingStore()
        client = ChatterClient(TwitchCredentials("id", "secret"), store)
        first_rise: list[object] = [
            self._http_error(401),  # the read
            {"access_token": "fresh", "refresh_token": "rotated"},  # the refresh
            self._http_error(401),  # the retry: still refused
        ]
        with patch(
            "youtube_live_count_chime.chatters.get_json", side_effect=first_rise
        ):
            with self.assertLogs("youtube_live_count_chime.chatters", "ERROR") as logs:
                with self.assertRaises(TwitchError):
                    client.chatters(TOKEN)
        self.assertIn("--auth watchmepivot", "\n".join(logs.output))

        with patch("youtube_live_count_chime.chatters.get_json") as get_json:
            with self.assertNoLogs("youtube_live_count_chime.chatters", "ERROR"):
                with self.assertRaises(TwitchError):
                    client.chatters(TOKEN)

        # No read, no refresh POST, no token-file rewrite, and told only once.
        self.assertEqual(get_json.call_count, 0)
        self.assertEqual(len(store.saved), 1)

    def test_a_non_401_failure_is_not_retried(self) -> None:
        client = ChatterClient(TwitchCredentials("id", "secret"), _RecordingStore())
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
