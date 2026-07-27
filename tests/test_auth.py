from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from youtube_live_count_chime.auth import (
    REDIRECT_URI,
    SCOPE,
    AuthError,
    _CallbackHandler,
    authorize_url,
    parse_callback,
    parse_identity,
    parse_token_response,
    run_auth_flow,
)
from youtube_live_count_chime.tokens import TokenStore
from youtube_live_count_chime.twitch import TwitchCredentials


CREDENTIALS = TwitchCredentials("client-123", "secret-placeholder")


class AuthorizeUrlTests(unittest.TestCase):
    def test_requests_the_chatters_scope_at_the_registered_redirect(self) -> None:
        query = parse_qs(urlparse(authorize_url("client-123", "state-abc")).query)

        self.assertEqual(query["client_id"], ["client-123"])
        self.assertEqual(query["scope"], [SCOPE])
        self.assertEqual(query["redirect_uri"], [REDIRECT_URI])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["state"], ["state-abc"])


class ParseCallbackTests(unittest.TestCase):
    def test_returns_the_code_when_state_matches(self) -> None:
        self.assertEqual(
            parse_callback("/?code=abc123&state=expected", "expected"), "abc123"
        )

    def test_rejects_a_mismatched_state(self) -> None:
        # CSRF guard: a callback we did not initiate must never be accepted.
        with self.assertRaises(AuthError):
            parse_callback("/?code=abc123&state=attacker", "expected")

    def test_surfaces_a_denied_consent(self) -> None:
        with self.assertRaises(AuthError):
            parse_callback("/?error=access_denied&state=expected", "expected")

    def test_rejects_a_callback_with_no_code(self) -> None:
        with self.assertRaises(AuthError):
            parse_callback("/?state=expected", "expected")

    def test_failure_reason_precedence(self) -> None:
        # State is checked first, and error before code — verified by the
        # exact message raised, not just "some AuthError was raised", so a
        # future reordering of the checks would fail loudly instead of
        # shipping silently.
        cases = {
            "state_mismatch_over_a_present_error": (
                "/?error=access_denied&state=attacker",
                "expected",
                "state did not match",
            ),
            "state_mismatch_over_a_present_code": (
                "/?code=abc123&state=attacker",
                "expected",
                "state did not match",
            ),
            "error_wins_over_code_when_state_matches": (
                "/?code=abc123&error=access_denied&state=expected",
                "expected",
                "was refused",
            ),
        }
        for name, (path, expected_state, message_fragment) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(AuthError, message_fragment):
                    parse_callback(path, expected_state)


class ParseTokenResponseTests(unittest.TestCase):
    def test_returns_access_and_refresh_tokens(self) -> None:
        self.assertEqual(
            parse_token_response({"access_token": "access-tok", "refresh_token": "refresh-tok"}),
            ("access-tok", "refresh-tok"),
        )

    def test_rejects_a_response_missing_an_access_token(self) -> None:
        with self.assertRaises(AuthError):
            parse_token_response({"refresh_token": "refresh-tok"})

    def test_rejects_a_response_missing_a_refresh_token(self) -> None:
        with self.assertRaises(AuthError):
            parse_token_response({"access_token": "access-tok"})


class ParseIdentityTests(unittest.TestCase):
    def test_returns_login_and_user_id(self) -> None:
        self.assertEqual(
            parse_identity({"data": [{"login": "watchmepivot", "id": "42"}]}),
            ("watchmepivot", "42"),
        )

    def test_rejects_an_empty_user_list(self) -> None:
        with self.assertRaises(AuthError):
            parse_identity({"data": []})

    def test_rejects_a_malformed_user(self) -> None:
        with self.assertRaises(AuthError):
            parse_identity({"data": [{"login": "watchmepivot"}]})


class _FakeCallbackServer:
    """Stand in for HTTPServer: one handle_request() delivers a fixed callback."""

    def __init__(self, path: str) -> None:
        self._path = path

    def __enter__(self) -> "_FakeCallbackServer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def handle_request(self) -> None:
        _CallbackHandler.path_seen = self._path


class RunAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = TokenStore(Path(self._dir.name) / "tokens.json")

    def test_normalizes_a_mixed_case_login_before_comparing_and_storing(self) -> None:
        # Twitch always returns logins lowercased; a raw mixed-case request
        # login must not (a) spuriously fail the authorized-account check or
        # (b) get stored under a key chatters.py can never look back up.
        fake_server = _FakeCallbackServer("/?code=abc123&state=fixed-state")
        with (
            patch(
                "youtube_live_count_chime.auth.secrets.token_urlsafe",
                return_value="fixed-state",
            ),
            patch(
                "youtube_live_count_chime.auth.HTTPServer",
                return_value=fake_server,
            ),
            patch(
                "youtube_live_count_chime.auth._exchange_code",
                return_value=("access-tok", "refresh-tok"),
            ),
            patch(
                "youtube_live_count_chime.auth._identify",
                return_value=("watchmepivot", "42"),
            ),
        ):
            token = run_auth_flow(
                "WatchMePivot",
                CREDENTIALS,
                self.store,
                open_browser=lambda url: True,
            )

        self.assertEqual(token.login, "watchmepivot")
        stored = self.store.get("watchmepivot")
        assert stored is not None
        self.assertEqual(stored.access_token, "access-tok")


if __name__ == "__main__":
    unittest.main()
