import unittest
from urllib.parse import parse_qs, urlparse

from youtube_live_count_chime.auth import (
    REDIRECT_URI,
    SCOPE,
    AuthError,
    authorize_url,
    parse_callback,
)


class AuthorizeUrlTests(unittest.TestCase):
    def test_requests_the_chatters_scope_at_the_registered_redirect(self) -> None:
        query = parse_qs(urlparse(authorize_url("client-123", "state-abc")).query)

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


if __name__ == "__main__":
    unittest.main()
