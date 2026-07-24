from collections.abc import Iterator
from email.message import Message
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from youtube_live_count_chime.models import Platform, StreamTarget
from youtube_live_count_chime.twitch import (
    TwitchClient,
    TwitchCredentials,
    TwitchError,
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

    def __call__(self, request: Request, timeout: float | None = None) -> _FakeResponse:
        url = request.full_url
        if "oauth2/token" in url:
            self.token_calls += 1
            result = self.token.pop(0)
        else:
            self.streams_calls += 1
            self.streams_auth.append(request.get_header("Authorization"))
            result = self.streams.pop(0)
        if isinstance(result, HTTPError):
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

    def test_invalid_token_raises(self) -> None:
        cases: tuple[object, ...] = ({}, {"access_token": ""}, {"access_token": 1}, [])
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(TwitchError):
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
            with self.subTest(data=data), self.assertRaises(TwitchError):
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

    def test_non_401_streams_error_names_status(self) -> None:
        http = _FakeHttp(token=[{"access_token": "tok"}], streams=[_http_error(500)])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            with self.assertRaises(TwitchError) as ctx:
                client.stream(TARGET)
        self.assertIn("500", str(ctx.exception))
        self.assertIn("streams", str(ctx.exception))

    def test_token_endpoint_error_names_token_not_streams(self) -> None:
        http = _FakeHttp(token=[_http_error(403)], streams=[])
        client = TwitchClient(CREDS)
        with patch("youtube_live_count_chime.twitch.urlopen", http):
            with self.assertRaises(TwitchError) as ctx:
                client.stream(TARGET)
        self.assertIn("token", str(ctx.exception))
        self.assertIn("403", str(ctx.exception))


class TwitchSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_yields_live_then_offline_snapshots(self) -> None:
        source = TwitchSource.for_login("Shroud", TwitchClient(CREDS), poll_interval=0.0)
        payloads: Iterator[object] = iter(
            (
                {"data": [{"id": "1", "user_login": "shroud", "viewer_count": 10}]},
                {"data": []},
            )
        )
        with patch(
            "youtube_live_count_chime.twitch.TwitchClient.stream",
            side_effect=lambda target: parse_stream(next(payloads), target),
        ):
            seen = []
            async for snapshot in source.snapshots():
                seen.append(snapshot)
                if len(seen) == 2:
                    break
        self.assertEqual(seen[0].viewers, 10)
        self.assertIsNone(seen[1].viewers)
        self.assertEqual(source.target.name, "shroud")


if __name__ == "__main__":
    unittest.main()
