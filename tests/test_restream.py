import asyncio
from base64 import b64decode
from email.message import Message
from io import BytesIO
import json
import logging
import subprocess
import traceback
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget
from youtube_live_count_chime.restream import (
    ACCESS_TOKEN_SERVICE,
    CLIENT_ID_SERVICE,
    CLIENT_SECRET_SERVICE,
    REFRESH_TOKEN_SERVICE,
    KeychainStore,
    RestreamClient,
    RestreamError,
    RestreamSource,
    TokenPair,
    parse_channel,
    parse_status_message,
    parse_token_payload,
)


TOKEN_PAYLOAD = {
    "access_token": "access",
    "refresh_token": "refresh",
    "expires_in": 3600,
    "scopeJson": ["channels.default.read", "stream.default.read"],
}
CHANNEL_PAYLOAD = {
    "id": 16972669,
    "streamingPlatformId": 1,
    "identifier": "1485450484",
    "displayName": "samtriestobuild",
    "url": "https://www.twitch.tv/samtriestobuild",
}
STATUS_PAYLOAD = {
    "action": "updateStatuses",
    "eventId": "restream-event-a",
    "eventIdentifier": "",
    "channelId": 16972669,
    "platformId": 1,
    "online": True,
    "viewers": 4,
    "followers": 3,
}


class ParseTokenPayloadTests(unittest.TestCase):
    def test_accepts_current_default_scopes(self) -> None:
        pair = parse_token_payload(TOKEN_PAYLOAD)

        self.assertEqual(
            pair,
            TokenPair(
                access_token="access",
                refresh_token="refresh",
                expires_in=3600,
                scopes=frozenset(
                    {"channels.default.read", "stream.default.read"}
                ),
            ),
        )

    def test_accepts_legacy_scopes(self) -> None:
        payload = {
            **TOKEN_PAYLOAD,
            "scopeJson": ["channels.read", "stream.read"],
        }

        pair = parse_token_payload(payload)

        self.assertEqual(pair.scopes, frozenset({"channels.read", "stream.read"}))

    def test_rejects_each_missing_capability(self) -> None:
        cases = (
            ("channels", ["stream.default.read"]),
            ("stream", ["channels.default.read"]),
        )
        for name, scopes in cases:
            with self.subTest(name=name), self.assertRaises(RestreamError):
                parse_token_payload({**TOKEN_PAYLOAD, "scopeJson": scopes})

    def test_rejects_malformed_token_fields_without_echoing_values(self) -> None:
        secret = "do-not-echo-this-token"
        with self.assertRaises(RestreamError) as raised:
            parse_token_payload({**TOKEN_PAYLOAD, "access_token": secret, "expires_in": 0})

        self.assertNotIn(secret, str(raised.exception))


class ParseChannelTests(unittest.TestCase):
    def test_extracts_twitch_handle(self) -> None:
        channel = parse_channel(CHANNEL_PAYLOAD)

        self.assertEqual(channel.channel_id, 16972669)
        self.assertEqual(channel.twitch_handle, "samtriestobuild")

    def test_rejects_a_malformed_channel_without_echoing_payload_values(self) -> None:
        secret = "do-not-echo-this-channel"
        with self.assertRaises(RestreamError) as raised:
            parse_channel({**CHANNEL_PAYLOAD, "url": secret})

        self.assertNotIn(secret, str(raised.exception))


class ParseStatusMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = StreamTarget(Platform.TWITCH, "samtriestobuild")
        self.targets = {16972669: self.target}

    def test_parses_live_twitch_status(self) -> None:
        snapshot = parse_status_message(STATUS_PAYLOAD, self.targets)

        self.assertEqual(
            snapshot,
            StreamSnapshot(
                target=self.target,
                stream_id="restream-event-a",
                viewers=4,
                followers=3,
                url="https://www.twitch.tv/samtriestobuild",
            ),
        )

    def test_parses_offline_twitch_status(self) -> None:
        snapshot = parse_status_message(
            {**STATUS_PAYLOAD, "online": False},
            self.targets,
        )

        self.assertEqual(
            snapshot,
            StreamSnapshot.offline(
                self.target,
                url="https://www.twitch.tv/samtriestobuild",
            ),
        )

    def test_live_status_allows_missing_follower_count(self) -> None:
        snapshot = parse_status_message(
            {**STATUS_PAYLOAD, "followers": None},
            self.targets,
        )

        self.assertEqual(
            snapshot,
            StreamSnapshot(
                target=self.target,
                stream_id="restream-event-a",
                viewers=4,
                followers=None,
                url="https://www.twitch.tv/samtriestobuild",
            ),
        )

    def test_offline_status_allows_missing_counts(self) -> None:
        snapshot = parse_status_message(
            {
                **STATUS_PAYLOAD,
                "online": False,
                "viewers": None,
                "followers": None,
            },
            self.targets,
        )

        self.assertEqual(
            snapshot,
            StreamSnapshot.offline(
                self.target,
                url="https://www.twitch.tv/samtriestobuild",
            ),
        )

    def test_accepts_text_websocket_messages(self) -> None:
        snapshot = parse_status_message(json.dumps(STATUS_PAYLOAD), self.targets)

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.viewers, 4)

    def test_ignores_irrelevant_or_invalid_statuses(self) -> None:
        cases = (
            ("unrelated-action", {**STATUS_PAYLOAD, "action": "other"}),
            ("unmapped-channel", {**STATUS_PAYLOAD, "channelId": 7}),
            ("non-twitch-platform", {**STATUS_PAYLOAD, "platformId": 2}),
            ("negative-viewers", {**STATUS_PAYLOAD, "viewers": -1}),
            ("negative-followers", {**STATUS_PAYLOAD, "followers": -1}),
            ("invalid-viewers", {**STATUS_PAYLOAD, "viewers": "4"}),
            ("missing-stream-id", {**STATUS_PAYLOAD, "eventId": ""}),
        )
        for name, payload in cases:
            with self.subTest(name=name):
                self.assertIsNone(parse_status_message(payload, self.targets))


class KeychainStoreTests(unittest.TestCase):
    def test_uses_exact_service_names_and_current_account(self) -> None:
        self.assertEqual(
            (
                CLIENT_ID_SERVICE,
                CLIENT_SECRET_SERVICE,
                ACCESS_TOKEN_SERVICE,
                REFRESH_TOKEN_SERVICE,
            ),
            (
                "youtube-live-count-chime-restream-client-id",
                "youtube-live-count-chime-restream-client-secret",
                "youtube-live-count-chime-restream-access-token",
                "youtube-live-count-chime-restream-refresh-token",
            ),
        )
        completed = subprocess.CompletedProcess(
            args=["security"],
            returncode=0,
            stdout="stored-value\n",
            stderr="",
        )
        with (
            patch(
                "youtube_live_count_chime.restream.getpass.getuser",
                return_value="current-user",
            ),
            patch(
                "youtube_live_count_chime.restream.subprocess.run",
                return_value=completed,
            ) as runner,
        ):
            store = KeychainStore()
            value = store.get(ACCESS_TOKEN_SERVICE)

        self.assertEqual(value, "stored-value")
        args = runner.call_args.args[0]
        self.assertIn("current-user", args)
        self.assertIn(ACCESS_TOKEN_SERVICE, args)

    def test_writes_credentials_via_stdin_instead_of_process_arguments(self) -> None:
        secret = "rotated-token-value"
        completed = subprocess.CompletedProcess(
            args=["security"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch(
            "youtube_live_count_chime.restream.subprocess.run",
            return_value=completed,
        ) as runner:
            KeychainStore(account="current-user").set(ACCESS_TOKEN_SERVICE, secret)

        args = runner.call_args.args[0]
        self.assertNotIn(secret, args)
        self.assertEqual(args[-1], "-w")
        self.assertEqual(runner.call_args.kwargs["input"], f"{secret}\n")


class RestreamClientTests(unittest.TestCase):
    def test_refresh_sends_refresh_grant_and_stores_rotated_tokens(self) -> None:
        store = MagicMock(spec=KeychainStore)
        credentials = {
            CLIENT_ID_SERVICE: "client-id",
            CLIENT_SECRET_SERVICE: "client-secret",
            REFRESH_TOKEN_SERVICE: "old-refresh",
        }
        store.get.side_effect = credentials.__getitem__
        response = BytesIO(json.dumps(TOKEN_PAYLOAD).encode())

        with patch(
            "youtube_live_count_chime.restream.urlopen",
            return_value=response,
        ) as opener:
            pair = RestreamClient(store).refresh()

        request = opener.call_args.args[0]
        self.assertEqual(
            parse_qs(request.data.decode()),
            {
                "grant_type": ["refresh_token"],
                "refresh_token": ["old-refresh"],
            },
        )
        authorization = request.get_header("Authorization")
        assert authorization is not None
        scheme, encoded = authorization.split(" ", 1)
        self.assertEqual(scheme, "Basic")
        self.assertEqual(b64decode(encoded).decode(), "client-id:client-secret")
        self.assertEqual(pair.access_token, "access")
        self.assertEqual(
            store.set.call_args_list,
            [
                call(REFRESH_TOKEN_SERVICE, "refresh"),
                call(ACCESS_TOKEN_SERVICE, "access"),
            ],
        )

    def test_list_channels_refreshes_and_retries_once_on_401(self) -> None:
        store = MagicMock(spec=KeychainStore)
        store.get.return_value = "old-access"
        client = RestreamClient(store)
        rotated = TokenPair(
            "new-access",
            "new-refresh",
            3600,
            frozenset({"channels.default.read", "stream.default.read"}),
        )
        unauthorized = HTTPError(
            "https://api.restream.io/v2/user/channel/all",
            401,
            "Unauthorized",
            Message(),
            None,
        )
        response = BytesIO(json.dumps([CHANNEL_PAYLOAD]).encode())

        with (
            patch.object(
                RestreamClient, "refresh", return_value=rotated
            ) as refresh,
            patch(
                "youtube_live_count_chime.restream.urlopen",
                side_effect=[unauthorized, response],
            ) as opener,
        ):
            channels = client.list_channels()

        refresh.assert_called_once_with()
        self.assertEqual(channels[0].twitch_handle, "samtriestobuild")
        retried_request = opener.call_args_list[1].args[0]
        self.assertEqual(
            retried_request.get_header("Authorization"),
            "Bearer new-access",
        )

    def test_list_channels_keeps_twitch_after_empty_non_twitch_channel(self) -> None:
        non_twitch_channel = {
            "id": 12345678,
            "streamingPlatformId": 29,
            "identifier": "",
            "displayName": "Plow Main Channel",
            "url": "",
        }
        store = MagicMock(spec=KeychainStore)
        store.get.return_value = "access"
        response = BytesIO(
            json.dumps([non_twitch_channel, CHANNEL_PAYLOAD]).encode()
        )

        with patch(
            "youtube_live_count_chime.restream.urlopen",
            return_value=response,
        ):
            channels = RestreamClient(store).list_channels()

        self.assertEqual(len(channels), 2)
        self.assertIsNone(channels[0].twitch_handle)
        self.assertEqual(channels[1].twitch_handle, "samtriestobuild")

    def test_http_errors_never_include_token_values(self) -> None:
        secret = "do-not-echo-this-access-token"
        store = MagicMock(spec=KeychainStore)
        store.get.return_value = secret

        with (
            patch(
                "youtube_live_count_chime.restream.urlopen",
                side_effect=URLError(secret),
            ),
            self.assertRaises(RestreamError) as raised,
        ):
            RestreamClient(store).list_channels()

        self.assertNotIn(secret, str(raised.exception))
        rendered = "".join(
            traceback.format_exception(raised.exception)
        )
        self.assertNotIn(secret, rendered)


class _FakeWebSocket:
    def __init__(self, messages: list[str | Exception]) -> None:
        self._messages = iter(messages)

    async def recv(self) -> str:
        message = next(self._messages)
        if isinstance(message, Exception):
            raise message
        return message


class _FakeConnection:
    def __init__(
        self,
        websocket: _FakeWebSocket,
        *,
        clock: list[float] | None = None,
        enter_time: float | None = None,
        enter_error: Exception | None = None,
    ) -> None:
        self.websocket = websocket
        self.clock = clock
        self.enter_time = enter_time
        self.enter_error = enter_error

    async def __aenter__(self) -> _FakeWebSocket:
        if self.clock is not None and self.enter_time is not None:
            self.clock[0] = self.enter_time
        if self.enter_error is not None:
            raise self.enter_error
        return self.websocket

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None


class _StatusResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _HTTPStatusError(ConnectionError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = _StatusResponse(status_code)


class RestreamSourceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _token_pair(access_token: str) -> TokenPair:
        return TokenPair(
            access_token,
            "refresh",
            3600,
            frozenset({"channels.default.read", "stream.default.read"}),
        )

    async def test_refreshes_before_first_connection_and_uses_new_token(self) -> None:
        client = MagicMock(spec=RestreamClient)
        client.access_token.return_value = "aged-access"
        client.refresh.return_value = self._token_pair("token with?/reserved")
        target = StreamTarget(Platform.TWITCH, "samtriestobuild")
        websocket = _FakeWebSocket([json.dumps(STATUS_PAYLOAD)])

        with patch(
            "youtube_live_count_chime.restream.connect",
            return_value=_FakeConnection(websocket),
        ) as connector:
            source = RestreamSource(client, {16972669: target})
            snapshot = await anext(source.snapshots())

        client.refresh.assert_called_once_with()
        client.access_token.assert_not_called()
        self.assertEqual(
            connector.call_args.args[0],
            "wss://streaming.api.restream.io/ws?"
            "accessToken=token%20with%3F%2Freserved",
        )
        websocket_logger = connector.call_args.kwargs.get("logger")
        self.assertIsInstance(websocket_logger, logging.Logger)
        assert isinstance(websocket_logger, logging.Logger)
        self.assertFalse(websocket_logger.propagate)
        self.assertFalse(websocket_logger.isEnabledFor(logging.CRITICAL))
        self.assertEqual(snapshot.viewers, 4)

    async def test_transient_reconnect_does_not_reset_token_refresh_deadline(
        self,
    ) -> None:
        client = MagicMock(spec=RestreamClient)
        client.access_token.return_value = "initial-access"
        client.refresh.side_effect = [
            self._token_pair("initial-access"),
            self._token_pair("periodic-access"),
        ]
        target = StreamTarget(Platform.TWITCH, "samtriestobuild")
        clock = [0.0]
        connections = [
            _FakeConnection(
                _FakeWebSocket([ConnectionError("disconnected")]),
                clock=clock,
                enter_time=0.0,
            ),
            _FakeConnection(
                _FakeWebSocket([json.dumps(STATUS_PAYLOAD)]),
                clock=clock,
                enter_time=11.0,
            ),
            _FakeConnection(
                _FakeWebSocket([json.dumps(STATUS_PAYLOAD)]),
                clock=clock,
                enter_time=11.0,
            ),
        ]
        with (
            patch(
                "youtube_live_count_chime.restream.connect",
                side_effect=connections,
            ),
            patch(
                "youtube_live_count_chime.restream.monotonic",
                side_effect=lambda: clock[0],
            ),
            patch("youtube_live_count_chime.restream._LOGGER.warning"),
        ):
            source = RestreamSource(
                client,
                {16972669: target},
                refresh_interval=10.0,
                retry_interval=0.0,
            )
            snapshot = await anext(source.snapshots())

        self.assertEqual(client.refresh.call_count, 2)
        self.assertEqual(snapshot.viewers, 4)

    async def test_websocket_401_refreshes_only_once_until_connected(self) -> None:
        client = MagicMock(spec=RestreamClient)
        client.access_token.return_value = "initial-access"
        client.refresh.side_effect = [
            self._token_pair("initial-access"),
            self._token_pair("refreshed-access"),
        ]
        target = StreamTarget(Platform.TWITCH, "samtriestobuild")
        unauthorized = _HTTPStatusError("credential-bearing-url", 401)
        connections = [
            _FakeConnection(_FakeWebSocket([]), enter_error=unauthorized),
            _FakeConnection(_FakeWebSocket([]), enter_error=unauthorized),
            _FakeConnection(_FakeWebSocket([json.dumps(STATUS_PAYLOAD)])),
        ]

        with (
            patch(
                "youtube_live_count_chime.restream.connect",
                side_effect=connections,
            ) as connector,
            patch(
                "youtube_live_count_chime.restream.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
            patch("youtube_live_count_chime.restream._LOGGER.warning") as warning,
        ):
            source = RestreamSource(client, {16972669: target})
            snapshot = await anext(source.snapshots())

        self.assertEqual(client.refresh.call_count, 2)
        self.assertIn("accessToken=initial-access", connector.call_args_list[0].args[0])
        self.assertIn(
            "accessToken=refreshed-access",
            connector.call_args_list[1].args[0],
        )
        self.assertIn(
            "accessToken=refreshed-access",
            connector.call_args_list[2].args[0],
        )
        sleep.assert_awaited_once_with(5.0)
        warning.assert_called_once_with(
            "Restream WebSocket disconnected (%s)",
            "_HTTPStatusError",
        )
        self.assertNotIn("credential-bearing-url", str(warning.call_args))
        self.assertEqual(snapshot.viewers, 4)

    async def test_transient_disconnect_logs_only_class_and_backs_off(self) -> None:
        secret = "do-not-log-token-value"
        client = MagicMock(spec=RestreamClient)
        client.access_token.return_value = secret
        client.refresh.return_value = self._token_pair(secret)
        target = StreamTarget(Platform.TWITCH, "samtriestobuild")
        connections = [
            _FakeConnection(
                _FakeWebSocket(
                    [ConnectionError(f"failed URL accessToken={secret}")]
                )
            ),
            _FakeConnection(_FakeWebSocket([json.dumps(STATUS_PAYLOAD)])),
        ]

        with (
            patch(
                "youtube_live_count_chime.restream.connect",
                side_effect=connections,
            ),
            patch(
                "youtube_live_count_chime.restream.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
            patch("youtube_live_count_chime.restream._LOGGER.warning") as warning,
        ):
            source = RestreamSource(client, {16972669: target})
            snapshot = await anext(source.snapshots())

        sleep.assert_awaited_once_with(5.0)
        warning.assert_called_once_with(
            "Restream WebSocket disconnected (%s)",
            "ConnectionError",
        )
        self.assertNotIn(secret, str(warning.call_args))
        self.assertEqual(snapshot.viewers, 4)


if __name__ == "__main__":
    unittest.main()
