import unittest
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from youtube_live_count_chime.chatters import (
    ChatterClient,
    TwitchChatterNamer,
    parse_chatters,
)
from youtube_live_count_chime.models import Platform, StreamTarget
from youtube_live_count_chime.tokens import StoredToken, TokenStore, TokenStoreError
from youtube_live_count_chime.twitch import (
    CREDENTIAL_REJECTED_STATUSES,
    TwitchAuthError,
    TwitchCredentials,
    TwitchRequestError,
)


TARGET = StreamTarget(Platform.TWITCH, "watchmepivot")
TOKEN = StoredToken("watchmepivot", "42", "access-placeholder", "refresh-placeholder")


def _next_response(script: list[object]) -> object:
    """Interpret one scripted get_json response: raise it, or return it."""
    item = script.pop(0)
    if isinstance(item, HTTPError):
        raise item
    return item


class _FakeStore:
    def __init__(self, token: StoredToken | None) -> None:
        self._token = token

    def get(self, login: str) -> StoredToken | None:
        return self._token


class _FakeClient:
    """Replay scripted chatters outcomes — a roster or a raise — in order."""

    def __init__(self, responses: list[frozenset[str] | Exception]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chatters(self, token: StoredToken) -> frozenset[str]:
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


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
                with self.assertRaises(TwitchRequestError):
                    parse_chatters(payload)

    def test_a_non_dict_entry_is_rejected(self) -> None:
        payload = {"data": [{"user_login": "joe_doe"}, "not-a-dict"]}

        with self.assertRaises(TwitchRequestError):
            parse_chatters(payload)

    def test_a_missing_or_empty_user_login_is_rejected(self) -> None:
        for entry in ({"nickname": "joe_doe"}, {"user_login": ""}):
            with self.subTest(entry=entry):
                with self.assertRaises(TwitchRequestError):
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

    async def test_a_read_after_an_outage_reseeds_instead_of_diffing_across_it(
        self,
    ) -> None:
        # A roster kept across a failure is a *pre-outage* sample: diffing the
        # next successful read against it names everyone who joined while the
        # watcher was blind, as if they had just arrived.
        for outage in (
            TwitchRequestError("chatters unavailable"),
            TokenStoreError("token file is not writable"),
        ):
            with self.subTest(outage=type(outage).__name__):
                client = _FakeClient(
                    [
                        frozenset({"lurker"}),  # seeds
                        outage,  # blind: joe_doe joins during this gap
                        frozenset({"lurker", "joe_doe"}),  # first read back
                    ]
                )
                namer = TwitchChatterNamer(client, _FakeStore(TOKEN))
                await namer.arrivals(TARGET, "stream-1")
                await namer.arrivals(TARGET, "stream-1")

                self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

    async def test_an_absent_token_also_reseeds_the_next_successful_read(self) -> None:
        # Same gap, different cause: --auth revoked mid-broadcast and restored.
        class _IntermittentStore:
            def __init__(self) -> None:
                self.token: StoredToken | None = TOKEN

            def get(self, login: str) -> StoredToken | None:
                return self.token

        store = _IntermittentStore()
        client = _FakeClient([frozenset({"lurker"}), frozenset({"lurker", "joe_doe"})])
        namer = TwitchChatterNamer(client, store)
        await namer.arrivals(TARGET, "stream-1")
        store.token = None
        await namer.arrivals(TARGET, "stream-1")
        store.token = TOKEN

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

    async def test_unauthorized_channel_yields_no_names_without_calling_out(
        self,
    ) -> None:
        client = _FakeClient([frozenset({"joe_doe"})])
        namer = TwitchChatterNamer(client, _FakeStore(None))

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())
        self.assertEqual(client.calls, 0)

    async def test_a_chatters_failure_degrades_to_no_names(self) -> None:
        # Both failure shapes are degradations: a transient request failure and
        # a permanent authorization failure. Neither may escape into the
        # monitor loop, where it would tear the whole watcher down over a
        # nice-to-have name.
        for error in (
            TwitchRequestError("chatters unavailable"),
            TwitchAuthError("watchmepivot is not authorized to read its own chat roster"),
            TokenStoreError("token file is corrupt"),
        ):
            with self.subTest(error=type(error).__name__):
                namer = TwitchChatterNamer(_FakeClient([error]), _FakeStore(TOKEN))

                self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

    async def test_a_programming_error_is_not_swallowed_as_no_names(self) -> None:
        # Only TwitchRequestError/TwitchAuthError/TokenStoreError are
        # degradations. A bug must escape
        # to monitor's boundary handler instead of silently killing naming for
        # the rest of the run while the watcher reports healthy.
        bug = AttributeError("StoredToken has no attribute 'acess_token'")
        namer = TwitchChatterNamer(_FakeClient([bug]), _FakeStore(TOKEN))

        with self.assertRaises(AttributeError):
            await namer.arrivals(TARGET, "stream-1")

    async def test_a_sustained_outage_warns_once_and_re_arms_on_recovery(self) -> None:
        # The monitor samples every poll (12/min per channel), so a channel
        # that never granted the scope — or a Helix outage — would otherwise
        # write ~17k identical warnings a day. Warn on the transition into
        # failure only, and re-arm once a read succeeds.
        outage_error = TwitchRequestError("chatters unavailable")
        client = _FakeClient(
            [
                outage_error,
                outage_error,
                outage_error,
                outage_error,
                frozenset({"lurker"}),  # recovers: seeds and re-arms
                frozenset({"lurker", "joe_doe"}),
                outage_error,  # a second outage, after a healthy stretch
            ]
        )
        namer = TwitchChatterNamer(client, _FakeStore(TOKEN))

        with self.assertLogs("youtube_live_count_chime.chatters", "WARNING") as outage:
            for _ in range(4):
                self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())
        self.assertEqual(len(outage.records), 1)

        await namer.arrivals(TARGET, "stream-1")
        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ("joe_doe",))

        # A second, distinct outage is worth telling the operator about again.
        with self.assertLogs("youtube_live_count_chime.chatters", "WARNING") as again:
            self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())
        self.assertEqual(len(again.records), 1)

    async def test_a_change_of_cause_warns_again_with_the_new_diagnosis(self) -> None:
        # The two failure paths report different causes, and an operator who
        # repairs the token file only to hit a never-granted scope must be told
        # about the second one — otherwise they are left holding a permanently
        # wrong diagnosis while every poll fails silently.
        class _RepairableStore:
            def __init__(self) -> None:
                self.corrupt = True

            def get(self, login: str) -> StoredToken | None:
                if self.corrupt:
                    raise TokenStoreError("token file is corrupt")
                return TOKEN

        store = _RepairableStore()
        client = _FakeClient([TwitchAuthError("not authorized")] * 2)
        namer = TwitchChatterNamer(client, store)

        with self.assertLogs("youtube_live_count_chime.chatters", "WARNING") as logs:
            self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())  # cause A
            self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())  # same: quiet
            store.corrupt = False
            self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())  # cause B
            self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())  # same: quiet

        self.assertEqual(len(logs.records), 2)
        self.assertIn("could not read the token store", logs.output[0])
        self.assertIn("could not read chat roster", logs.output[1])

    async def test_a_change_of_cause_within_one_branch_also_warns_again(self) -> None:
        # The roster branch raises three different exceptions behind a single
        # message, so keying the warn-once on the message alone would leave an
        # operator holding "Helix is down" long after the real cause had become
        # an unwritable token store during a token refresh.
        client = _FakeClient(
            [
                TwitchRequestError("chatters unavailable"),
                TwitchRequestError("chatters unavailable"),
                TokenStoreError("token file is not writable"),
                TokenStoreError("token file is not writable"),
            ]
        )
        namer = TwitchChatterNamer(client, _FakeStore(TOKEN))

        with self.assertLogs("youtube_live_count_chime.chatters", "WARNING") as logs:
            for _ in range(4):
                self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

        # Two warnings, not one (message-keyed) and not four (no warn-once).
        self.assertEqual(len(logs.records), 2)
        self.assertIn("chatters unavailable", logs.output[0])
        self.assertIn("token file is not writable", logs.output[1])

    async def test_a_corrupt_token_store_degrades_to_no_names(self) -> None:
        # A malformed token file must not escape as an exception and kill the
        # watcher for every channel — it degrades to () like every other
        # arrivals() failure path.
        namer = TwitchChatterNamer(_FakeClient([]), _ExplodingStore())

        self.assertEqual(await namer.arrivals(TARGET, "stream-1"), ())

    async def test_an_unwritable_store_during_a_refresh_degrades_to_no_names(
        self,
    ) -> None:
        # The hot path: user tokens expire every few hours, so 401 -> refresh ->
        # save runs constantly. This wires the *real* TokenStore in as the
        # client's saver — the fakes above raise TokenStoreError directly and so
        # cannot see a store that lets a bare OSError out, which the monitor
        # would wrap as "source X failed" and use to cancel every channel.
        with TemporaryDirectory() as tmp:
            # A regular file where the config directory should be: the write
            # cannot land, for root or anyone else.
            blocked = Path(tmp) / "count-chime"
            blocked.write_text("", encoding="utf-8")
            client = ChatterClient(
                TwitchCredentials("id", "secret"), TokenStore(blocked / "tokens")
            )
            responses: list[object] = [
                HTTPError("https://api.twitch.tv", 401, "nope", Message(), None),
                {"access_token": "fresh-placeholder", "refresh_token": "rotated"},
            ]

            namer = TwitchChatterNamer(client, _FakeStore(TOKEN))
            with patch(
                "youtube_live_count_chime.chatters.get_json",
                side_effect=responses,
            ):
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

        with patch(
            "youtube_live_count_chime.chatters.get_json",
            side_effect=lambda request: _next_response(responses),
        ):
            self.assertEqual(client.chatters(TOKEN), frozenset({"joe_doe"}))

        self.assertEqual(responses, [])  # exactly one refresh, one retry
        self.assertEqual(len(store.saved), 1)  # the rotated token was persisted

    def _permanently_refuse(self) -> tuple[_RecordingStore, ChatterClient, StoredToken]:
        # A missing moderator:read:chatters scope answers 401 forever. Retrying
        # would POST a refresh per rise, and Twitch rate-limits refreshes and
        # invalidates a redeemed refresh token — eventually destroying the grant.
        store = _RecordingStore()
        client = ChatterClient(TwitchCredentials("id", "secret"), store)
        refused: list[object] = [
            self._http_error(401),  # the read
            {"access_token": "rotated-placeholder", "refresh_token": "r2"},  # refresh
            self._http_error(401),  # the retry: still refused
        ]

        with patch(
            "youtube_live_count_chime.chatters.get_json",
            side_effect=refused,
        ):
            with self.assertLogs("youtube_live_count_chime.chatters", "ERROR") as logs:
                with self.assertRaises(TwitchAuthError):
                    client.chatters(TOKEN)
        self.assertIn("--auth watchmepivot", "\n".join(logs.output))
        return store, client, store.saved[-1]

    def test_a_refused_token_and_its_rotation_are_both_short_circuited(self) -> None:
        # The memo is keyed by the presented token, not by login. Both keys
        # matter: a stale caller re-presents the original, while the running
        # watcher reads back the rotation _refresh persisted. Either must
        # cost no read, no refresh POST, no further save, and no second
        # telling — otherwise every rise rate-limits the grant toward
        # revocation.
        store, client, rotated = self._permanently_refuse()

        with patch("youtube_live_count_chime.chatters.get_json") as get_json:
            with self.assertNoLogs("youtube_live_count_chime.chatters", "ERROR"):
                for name, token in (("presented", TOKEN), ("rotated", rotated)):
                    with self.subTest(name):
                        with self.assertRaises(TwitchAuthError):
                            client.chatters(token)

        self.assertEqual(get_json.call_count, 0)
        self.assertEqual(len(store.saved), 1)  # only _refresh's own save

    def test_a_token_from_a_later_auth_is_retried_rather_than_short_circuited(
        self,
    ) -> None:
        # Re-running --auth is the documented repair for a permanent 401, so a
        # freshly granted token must not inherit the refusal — otherwise the
        # watcher ignores the new grant until it is restarted.
        _, client, _ = self._permanently_refuse()
        granted = StoredToken("watchmepivot", "42", "granted-placeholder", "r3")

        with patch(
            "youtube_live_count_chime.chatters.get_json",
            return_value={"data": [{"user_login": "joe_doe"}]},
        ):
            self.assertEqual(client.chatters(granted), frozenset({"joe_doe"}))

    def test_a_failed_save_during_a_refresh_short_circuits_later_polls(self) -> None:
        # Twitch redeems the old refresh token the moment the refresh POST
        # succeeds, so a save that then fails leaves the rotated credentials
        # lost and the previous grant dead. Refreshing again would present a
        # redeemed refresh token every poll, so the stale stored token must be
        # short-circuited — and the operator told what to repair, once.
        class _UnwritableStore:
            def save(self, token: StoredToken) -> None:
                raise TokenStoreError("could not write tokens.json: read-only")

        client = ChatterClient(TwitchCredentials("id", "secret"), _UnwritableStore())
        responses: list[object] = [
            self._http_error(401),  # the read: token expired
            {"access_token": "fresh-placeholder", "refresh_token": "rotated"},
        ]

        with patch(
            "youtube_live_count_chime.chatters.get_json",
            side_effect=lambda request: _next_response(responses),
        ):
            with self.assertLogs("youtube_live_count_chime.chatters", "ERROR") as logs:
                with self.assertRaises(TokenStoreError):
                    client.chatters(TOKEN)

        self.assertEqual(responses, [])  # one read, one refresh, no retry
        told = "\n".join(logs.output)
        self.assertIn("Repair the token store", told)
        self.assertIn("--auth watchmepivot", told)

        # The store still holds the stale token, so every later poll presents
        # it: no read, no second refresh POST, and no second telling.
        with patch("youtube_live_count_chime.chatters.get_json") as get_json:
            with self.assertNoLogs("youtube_live_count_chime.chatters", "ERROR"):
                with self.assertRaises(TwitchAuthError) as refused:
                    client.chatters(TOKEN)
        self.assertEqual(get_json.call_count, 0)
        # That refusal is the *recurring* operator-facing signal, and the fault
        # is the store: reporting a never-granted scope here would point them
        # at --auth forever while the unwritable store went unmentioned.
        self.assertIn("token store", str(refused.exception))
        self.assertNotIn("not authorized", str(refused.exception))

    def test_a_permanently_rejected_refresh_short_circuits_later_polls(self) -> None:
        # Every status the token endpoint uses to refuse a grant or the
        # application's own credentials: a revoked or redeemed refresh token, a
        # rotated client secret. No later poll can recover any of them. Treated
        # as retryable, the 5s poll resubmits the same dead grant forever, and
        # the operator is never told what to repair.
        for code in sorted(CREDENTIAL_REJECTED_STATUSES):
            with self.subTest(code=code):
                store = _RecordingStore()
                client = ChatterClient(TwitchCredentials("id", "secret"), store)
                responses: list[object] = [
                    self._http_error(401),  # the read: token expired
                    self._http_error(code),  # the refresh: refused for good
                ]

                with patch(
                    "youtube_live_count_chime.chatters.get_json",
                    side_effect=lambda request: _next_response(responses),
                ):
                    with self.assertLogs(
                        "youtube_live_count_chime.chatters", "ERROR"
                    ) as logs:
                        with self.assertRaises(TwitchAuthError):
                            client.chatters(TOKEN)

                self.assertEqual(responses, [])  # one read, one refresh, no retry
                # The status alone cannot say which cause it is, so both are named.
                told = "\n".join(logs.output)
                self.assertIn("--auth watchmepivot", told)
                self.assertIn("application credentials", told)
                self.assertEqual(store.saved, [])

                # The store still holds the stale token, so every later poll
                # presents it: no read, no second refresh POST, no second telling.
                with patch("youtube_live_count_chime.chatters.get_json") as get_json:
                    with self.assertNoLogs("youtube_live_count_chime.chatters", "ERROR"):
                        with self.assertRaises(TwitchAuthError):
                            client.chatters(TOKEN)
                self.assertEqual(get_json.call_count, 0)

    def test_a_transient_refresh_failure_is_not_memoized(self) -> None:
        # The other half of that split. A Helix outage during a refresh is
        # transient, so it must raise TwitchRequestError and remember nothing:
        # an implementation that memoized every failed refresh would silence
        # naming for the channel until the process restarts, after one blip.
        store = _RecordingStore()
        client = ChatterClient(TwitchCredentials("id", "secret"), store)
        for code in (500, 503):
            with self.subTest(code=code):
                responses: list[object] = [
                    self._http_error(401),  # the read: token expired
                    self._http_error(code),  # the refresh: Twitch is degraded
                ]
                with patch(
                    "youtube_live_count_chime.chatters.get_json",
                    side_effect=lambda request: _next_response(responses),
                ):
                    with self.assertRaises(TwitchRequestError):
                        client.chatters(TOKEN)
                self.assertEqual(responses, [])  # one read, one refresh, no retry

        # Nothing was refused, so the next poll runs the whole path again and
        # succeeds the moment Twitch recovers.
        recovered: list[object] = [
            self._http_error(401),
            {"access_token": "fresh-placeholder", "refresh_token": "rotated"},
            {"data": [{"user_login": "joe_doe"}]},
        ]
        with patch(
            "youtube_live_count_chime.chatters.get_json",
            side_effect=lambda request: _next_response(recovered),
        ):
            self.assertEqual(client.chatters(TOKEN), frozenset({"joe_doe"}))
        self.assertEqual(recovered, [])

    def test_a_non_401_failure_is_not_retried(self) -> None:
        client = ChatterClient(TwitchCredentials("id", "secret"), _RecordingStore())
        responses: list[object] = [self._http_error(500)]

        with patch(
            "youtube_live_count_chime.chatters.get_json",
            side_effect=lambda request: _next_response(responses),
        ):
            with self.assertRaises(TwitchRequestError):
                client.chatters(TOKEN)

        self.assertEqual(responses, [])  # exactly one call: no refresh, no retry


if __name__ == "__main__":
    unittest.main()
