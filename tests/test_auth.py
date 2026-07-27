import io
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
from youtube_live_count_chime.tokens import StoredToken, TokenStore
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
        # Every shape a malformed Helix body takes must stay inside the
        # module's AuthError contract — a non-object entry used to escape as
        # an AttributeError and reach the user as a traceback.
        for payload in ("nope", {}, {"data": ["x"]}, {"data": [{"login": "wmp"}]}):
            with self.subTest(payload=payload):
                with self.assertRaises(AuthError):
                    parse_identity(payload)


class _FakeCallbackServer:
    """Stand in for HTTPServer: each handle_request() replays one scripted event.

    A ``None`` event is a connection accepted without a request line ever
    arriving (a browser preconnect), which leaves ``path_seen`` unset and must
    not end the wait. A ``str`` event is a GET for that path, delivered through
    the real ``_CallbackHandler.do_GET`` (with only the socket-facing bits
    stubbed out) so tests exercise the actual callback-vs-stray-request
    filtering, not a reimplementation of it. Every ``timeout`` the flow
    assigns is recorded, so the per-attempt remainder of the overall budget
    can be asserted.
    """

    def __init__(self, *events: str | None) -> None:
        self._events = list(events)
        self.timeout = 0.0
        self.timeouts: list[float] = []

    def __enter__(self) -> "_FakeCallbackServer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def handle_request(self) -> None:
        self.timeouts.append(self.timeout)
        event = self._events.pop(0) if self._events else None
        if event is None:
            return
        handler = _CallbackHandler.__new__(_CallbackHandler)
        handler.path = event
        handler.wfile = io.BytesIO()
        with (
            patch.object(_CallbackHandler, "send_response", lambda self, code: None),
            patch.object(
                _CallbackHandler, "send_header", lambda self, name, value: None
            ),
            patch.object(_CallbackHandler, "end_headers", lambda self: None),
        ):
            handler.do_GET()


CALLBACK = "/?code=abc123&state=fixed-state"


class RunAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = TokenStore(Path(self._dir.name) / "tokens.json")

    def _run_flow(
        self,
        server: _FakeCallbackServer,
        *,
        login: str = "WatchMePivot",
        identified: tuple[str, str] = ("watchmepivot", "42"),
    ) -> StoredToken:
        """Run the flow with the network, the browser, and the server faked."""
        with (
            patch(
                "youtube_live_count_chime.auth.secrets.token_urlsafe",
                return_value="fixed-state",
            ),
            patch("youtube_live_count_chime.auth.HTTPServer", return_value=server),
            patch(
                "youtube_live_count_chime.auth._exchange_code",
                return_value=("access-tok", "refresh-tok"),
            ),
            patch("youtube_live_count_chime.auth._identify", return_value=identified),
        ):
            return run_auth_flow(
                login, CREDENTIALS, self.store, open_browser=lambda url: True
            )

    def test_normalizes_a_mixed_case_login_before_comparing_and_storing(self) -> None:
        # Twitch always returns logins lowercased; a raw mixed-case request
        # login must not (a) spuriously fail the authorized-account check or
        # (b) get stored under a key chatters.py can never look back up.
        token = self._run_flow(_FakeCallbackServer(CALLBACK))

        self.assertEqual(token.login, "watchmepivot")
        stored = self.store.get("watchmepivot")
        assert stored is not None
        self.assertEqual(stored.access_token, "access-tok")

    def test_authorizing_as_the_wrong_account_fails_and_stores_nothing(self) -> None:
        # The easy two-account mistake: the browser was signed in as the other
        # one. Storing that token would key someone else's grant under the
        # requested login and silently name arrivals from the wrong channel.
        with self.assertRaisesRegex(AuthError, "samtriestobuild"):
            self._run_flow(
                _FakeCallbackServer(CALLBACK), identified=("samtriestobuild", "99")
            )

        self.assertFalse(self.store.path.exists())

    def test_a_silent_connection_does_not_end_the_wait(self) -> None:
        # A browser preconnect is accepted and carries nothing. The real
        # callback arrives on a later connection, so the loop must keep going.
        server = _FakeCallbackServer(None, CALLBACK)

        token = self._run_flow(server)

        self.assertEqual(token.login, "watchmepivot")
        self.assertEqual(len(server.timeouts), 2)

    def test_a_stray_favicon_request_does_not_end_the_wait(self) -> None:
        # A GET with no code/error — e.g. a browser fetching /favicon.ico
        # against the loopback port — must not be mistaken for the callback.
        # If it were, the loop would end on it and parse_callback would then
        # fail on a state mismatch, and the real callback would never be read.
        server = _FakeCallbackServer("/favicon.ico", CALLBACK)

        token = self._run_flow(server)

        self.assertEqual(token.login, "watchmepivot")
        self.assertEqual(len(server.timeouts), 2)

    def test_the_overall_budget_bounds_the_wait_and_shrinks_per_attempt(self) -> None:
        # Without a deadline a browser that never returns hangs the terminal
        # forever, and each attempt gets only what is left of the budget.
        server = _FakeCallbackServer(None, None, None)
        clock = [0.0, 1.0, 2.0]
        with (
            patch("youtube_live_count_chime.auth._FLOW_TIMEOUT_SECONDS", 2.0),
            patch(
                "youtube_live_count_chime.auth.time.monotonic", side_effect=clock
            ),
        ):
            with self.assertRaisesRegex(AuthError, "timed out"):
                self._run_flow(server)

        self.assertEqual(server.timeouts, [1.0])  # 2.0 budget, 1.0 already spent
        self.assertFalse(self.store.path.exists())


if __name__ == "__main__":
    unittest.main()
