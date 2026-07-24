from collections.abc import Iterator
import os
import unittest
from unittest.mock import patch

from youtube_live_count_chime.models import Platform, StreamTarget
from youtube_live_count_chime.twitch import (
    TwitchCredentials,
    TwitchError,
    TwitchSource,
    parse_app_token,
    parse_stream,
)


TARGET = StreamTarget(Platform.TWITCH, "shroud")


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


class TwitchSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_yields_live_then_offline_snapshots(self) -> None:
        creds = TwitchCredentials("id", "secret")
        source = TwitchSource.for_login("Shroud", creds, poll_interval=0.0)
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
