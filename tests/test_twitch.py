from collections.abc import Iterator
from email.message import Message
from http.client import BadStatusLine
import json
import os
import threading
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from youtube_live_count_chime.models import Platform, SourceFetchError, StreamTarget
from youtube_live_count_chime.twitch import (
    TwitchClient,
    TwitchCredentials,
    TwitchError,
    TwitchRequestError,
    TwitchSource,
    parse_app_token,
    parse_stream,
)


TARGET = StreamTarget(Platform.TWITCH, "shroud")
CREDS = TwitchCredentials("id", "secret")


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _FakeHttp:
    """Serve queued token/streams responses and record which endpoints were hit."""

    def __init__(
        self,
        *,
        token: list[object] | None = None,
        streams: list[object] | None = None,
    ) -> None:
        self.token = list(token or [])
        self.streams = list(streams or [])
        self.token_calls = 0
        self.streams_calls = 0
        self.streams_auth: list[str | None] = []
        self._lock = threading.Lock()

    def __call__(self, request: Request, timeout: float | None = None) -> _FakeResponse:
        with self._lock:
            if "oauth2/token" in request.full_url:
                self.token_calls += 1
                result = self.token.pop(0)
            else:
                self.streams_calls += 1
                self.streams_auth.append(request.get_header("Authorization"))
                result = self.streams.pop(0)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://twitch.test", code, "error", Message(), None)


LIVE = {"data": [{"id": "1", "user_login": "shroud", "viewer_count": 5}]}


class CredentialsTests(unittest.TestCase):
    def test_from_env_reads_both_values(self) -> None:
        env = {"TWITCH_CLIENT_ID": "abc", "TWITCH_CLIENT_SECRET": "xyz"}
        with patch.dict(os.environ, env, clear=True):
            creds = TwitchCredentials.from_env()
        self.assertEqual(creds.client_id, "abc")
        self.assertEqual(creds.client_secret, "xyz")

    def test_from_env_missing_var_raises_named_error(self) -> None:
        with patch.dict(os.environ, {"TWITCH_CLIENT_ID": "abc"}, clear=True):
            with self.assertRaises(TwitchError) as ctx:
                TwitchCredentials.from_env()
        self.assertIn("TWITCH_CLIENT_SECRET", str(ctx.exception))
        self.assertNotIn("abc", str(ctx.exception))


class ParseTokenTests(unittest.TestCase):
    def test_valid_token(self) -> None:
        self.assertEqual(parse_app_token({"access_token": "tok"}), "tok")

    def test_invalid_token_raises_a_retryable_error(self) -> None:
        cases: tuple[object, ...] = ({}, {"access_token": ""}, {"access_token": 1}, [])
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(TwitchRequestError):
                parse_app_token(payload)


class ParseStreamTests(unittest.TestCase):
    def test_offline_when_data_empty(self) -> None:
        snapshot = parse_stream({"data": []}, TARGET)
        self.assertIsNone(snapshot.stream_id)
        self.assertIsNone(snapshot.viewers)

    def test_live_stream_snapshot(self) -> None:
        payload = {"data": [{"id": "42", "user_login": "shroud", "viewer_count": 1234}]}
        snapshot = parse_stream(payload, TARGET)
        self.assertEqual(snapshot.stream_id, "42")
        self.assertEqual(snapshot.viewers, 1234)

    def test_malformed_stream_raises(self) -> None:
        cases = (
            [{"id": "", "viewer_count": 5}],
            [{"id": "1", "viewer_count": -1}],
            [{"id": "1", "viewer_count": True}],
            [{"id": "1"}],
            "nope",
        )
        for data in cases:
            with self.subTest(data=data), self.assertRaises(TwitchRequestError):
                parse_stream({"data": data}, TARGET)


class TwitchClientTests(unittest.TestCase):
    def test_reuses_cached_token_across_calls(self) -> None:
        http = _FakeHttp(token=[{"access_token": "tok"}], streams=[LIVE, LIVE])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            client.stream(TARGET)
            client.stream(TARGET)
        self.assertEqual((http.token_calls, http.streams_calls), (1, 2))

    def test_refreshes_token_once_after_401_and_retries_with_new_token(self) -> None:
        http = _FakeHttp(
            token=[{"access_token": "old"}, {"access_token": "new"}],
            streams=[_http_error(401), LIVE],
        )
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            snapshot = client.stream(TARGET)
        self.assertEqual(snapshot.viewers, 5)
        self.assertEqual((http.token_calls, http.streams_calls), (2, 2))
        self.assertEqual(http.streams_auth, ["Bearer old", "Bearer new"])

    def test_concurrent_first_polls_fetch_one_shared_token(self) -> None:
        # One queued token but two concurrent pollers: without the lock the
        # second poll pops an empty token queue, so token_calls != 1.
        http = _FakeHttp(token=[{"access_token": "tok"}], streams=[LIVE, LIVE])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            threads = [
                threading.Thread(target=client.stream, args=(TARGET,)) for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(http.token_calls, 1)
        self.assertEqual(http.streams_calls, 2)

    def test_refresh_dedups_when_token_already_rotated(self) -> None:
        http = _FakeHttp(token=[{"access_token": "t1"}, {"access_token": "t2"}])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            _, generation = client._token()  # fetch t1 (generation 1)
            first = client._refresh_token(generation)  # generation matches -> fetch t2
            second = client._refresh_token(generation)  # stale generation -> reuse t2
        self.assertEqual((first, second), ("t2", "t2"))
        self.assertEqual(http.token_calls, 2)  # initial + one refresh, second reused

    def test_non_401_streams_error_names_status(self) -> None:
        http = _FakeHttp(token=[{"access_token": "tok"}], streams=[_http_error(500)])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            with self.assertRaises(TwitchRequestError) as ctx:
                client.stream(TARGET)
        self.assertIn("500", str(ctx.exception))
        self.assertIn("streams", str(ctx.exception))

    def test_transport_exception_becomes_a_retryable_error(self) -> None:
        http = _FakeHttp(
            token=[{"access_token": "tok"}], streams=[BadStatusLine("garbage")]
        )
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            with self.assertRaises(TwitchRequestError):
                client.stream(TARGET)

    def test_token_endpoint_error_names_token_not_streams(self) -> None:
        http = _FakeHttp(token=[_http_error(403)], streams=[])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            with self.assertRaises(TwitchError) as ctx:
                client.stream(TARGET)
        self.assertIn("token", str(ctx.exception))
        self.assertIn("403", str(ctx.exception))
        # The whole point of the split: a rejected credential must not be the
        # class the poll loop swallows.
        self.assertNotIsInstance(ctx.exception, SourceFetchError)

    def test_token_endpoint_classifies_by_status(self) -> None:
        # 403 means the credentials are refused (fatal); 429/503 are the
        # endpoint being busy or degraded, which a later poll may survive.
        cases = {400: False, 401: False, 403: False, 429: True, 500: True, 503: True}
        for status, retryable in cases.items():
            http = _FakeHttp(token=[_http_error(status)], streams=[])
            client = TwitchClient(CREDS)
            with self.subTest(status=status):
                with patch("youtube_live_count_chime.twitch.urlopen", http):
                    with self.assertRaises(TwitchError) as ctx:
                        client.stream(TARGET)
                self.assertEqual(
                    isinstance(ctx.exception, SourceFetchError), retryable
                )

    def test_streams_rejecting_a_fresh_token_is_fatal(self) -> None:
        # Mirror of the bug the split exists to fix: a 401 that survives a
        # token refresh is a mismatch, so retrying it forever would be silent.
        http = _FakeHttp(
            token=[{"access_token": "old"}, {"access_token": "new"}],
            streams=[_http_error(401), _http_error(401)],
        )
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            with self.assertRaises(TwitchError) as ctx:
                client.stream(TARGET)
        self.assertNotIsInstance(ctx.exception, SourceFetchError)


class TwitchSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_for_login_normalizes_and_rejects(self) -> None:
        # Charset matrix is in test_models.NormalizeHandleTests; confirm delegation.
        self.assertEqual(
            TwitchSource.for_login("@Shroud", TwitchClient(CREDS)).name, "twitch:shroud"
        )
        with self.assertRaises(ValueError):
            TwitchSource.for_login("@", TwitchClient(CREDS))

    async def test_yields_live_then_offline_snapshots(self) -> None:
        source = TwitchSource.for_login("Shroud", TwitchClient(CREDS))
        payloads: Iterator[object] = iter(
            (
                {"data": [{"id": "1", "user_login": "shroud", "viewer_count": 10}]},
                {"data": []},
            )
        )
        with (
            patch(
                "youtube_live_count_chime.twitch.TwitchClient.stream",
                side_effect=lambda target: parse_stream(next(payloads), target),
            ),
            patch(
                "youtube_live_count_chime.models.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            seen = []
            async for snapshot in source.snapshots():
                seen.append(snapshot)
                if len(seen) == 2:
                    break
        self.assertEqual(seen[0].viewers, 10)
        self.assertIsNone(seen[1].viewers)
        self.assertEqual(source.target.name, "shroud")

    async def test_rejected_credentials_stop_the_source_instead_of_retrying(self) -> None:
        # Bad credentials fail every poll forever, so they must escape the poll
        # loop rather than be warned once and retried behind a quiet log.
        source = TwitchSource.for_login("shroud", TwitchClient(CREDS))
        with (
            patch(
                "youtube_live_count_chime.twitch.TwitchClient.stream",
                side_effect=TwitchError("Twitch token request failed (HTTP 401)"),
            ),
            patch("youtube_live_count_chime.models.asyncio.sleep", new_callable=AsyncMock),
            self.assertRaises(TwitchError),
        ):
            await anext(source.snapshots())


if __name__ == "__main__":
    unittest.main()
