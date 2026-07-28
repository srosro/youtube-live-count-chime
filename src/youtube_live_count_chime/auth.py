"""One-time browser OAuth to obtain a per-account Twitch user token.

Requires ``http://127.0.0.1:8419`` to be registered as an OAuth Redirect URL on
the Twitch application at dev.twitch.tv/console. It must be the literal loopback
address, not ``localhost``: the callback server binds IPv4 loopback only, and
the same string is sent in the code exchange, where Twitch compares it verbatim.
"""

from __future__ import annotations

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from typing import Final, cast
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request
import webbrowser

from youtube_live_count_chime.chatters import CHATTERS_SCOPE
from youtube_live_count_chime.models import normalize_handle
from youtube_live_count_chime.tokens import StoredToken, TokenStore
from youtube_live_count_chime.twitch import (
    TOKEN_URL,
    TwitchCredentials,
    TwitchRequestError,
    get_json,
    object_dict,
)


AUTHORIZE_URL: Final = "https://id.twitch.tv/oauth2/authorize"
USERS_URL: Final = "https://api.twitch.tv/helix/users"
REDIRECT_HOST: Final = "127.0.0.1"
REDIRECT_PORT: Final = 8419
REDIRECT_URI: Final = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}"
SCOPE: Final = CHATTERS_SCOPE
# Overall budget for the whole wait (covers preconnects/favicon probes that eat
# a request without carrying the callback); per-socket-read budget below it so
# one silent connection can't consume the whole window.
_FLOW_TIMEOUT_SECONDS: Final = 300.0
_SOCKET_TIMEOUT_SECONDS: Final = 30.0


class AuthError(RuntimeError):
    """Raised when the browser authorization flow cannot complete."""


def authorize_url(client_id: str, state: str) -> str:
    """Build the Twitch consent URL for the chatters scope."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _query(path: str) -> dict[str, list[str]]:
    """Parse the query string of a request path."""
    return parse_qs(urlparse(path).query)


def _state_matches(query: dict[str, list[str]], expected_state: str) -> bool:
    """Whether a parsed query carries the state of the flow that expects it."""
    return query.get("state", [""])[0] == expected_state


def parse_callback(path: str, expected_state: str) -> str:
    """Return the authorization code from a callback path, verifying state."""
    query = _query(path)
    if not _state_matches(query, expected_state):
        raise AuthError("authorization state did not match; ignoring callback")
    error = query.get("error", [""])[0]
    if error:
        raise AuthError(f"authorization was refused ({error})")
    code = query.get("code", [""])[0]
    if not code:
        raise AuthError("authorization callback carried no code")
    return code


def parse_token_response(payload: object) -> tuple[str, str]:
    """Validate a code-exchange response and return (access_token, refresh_token)."""
    fields = object_dict(payload)
    if fields is None:
        raise AuthError("Twitch returned a malformed token response")
    access = fields.get("access_token")
    refresh = fields.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise AuthError("Twitch returned no access token")
    if not isinstance(refresh, str) or not refresh:
        raise AuthError("Twitch returned no refresh token")
    return access, refresh


def parse_identity(payload: object) -> tuple[str, str]:
    """Validate a users-endpoint response and return (login, user_id)."""
    fields = object_dict(payload)
    if fields is None:
        raise AuthError("Twitch returned a malformed user response")
    data = fields.get("data")
    if not isinstance(data, list) or not data:
        raise AuthError("Twitch returned no user for this token")
    entry = object_dict(cast("list[object]", data)[0])
    if entry is None:
        raise AuthError("Twitch returned a malformed user")
    login = entry.get("login")
    user_id = entry.get("id")
    if not isinstance(login, str) or not isinstance(user_id, str):
        raise AuthError("Twitch returned a malformed user")
    return login, user_id


def _exchange_code(code: str, credentials: TwitchCredentials) -> tuple[str, str]:
    request = Request(
        TOKEN_URL,
        data=urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            }
        ).encode("ascii"),
        method="POST",
    )
    try:
        payload = get_json(request)
    except HTTPError as error:
        raise AuthError(f"code exchange failed (HTTP {error.code})") from error
    except TwitchRequestError as error:
        raise AuthError(f"code exchange failed: {error}") from error
    return parse_token_response(payload)


def _identify(access_token: str, credentials: TwitchCredentials) -> tuple[str, str]:
    """Return the (login, user_id) that actually authorized."""
    request = Request(
        USERS_URL,
        headers={
            "Client-Id": credentials.client_id,
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    try:
        payload = get_json(request)
    except HTTPError as error:
        raise AuthError(f"could not identify the account (HTTP {error.code})") from error
    except TwitchRequestError as error:
        raise AuthError(f"could not identify the account: {error}") from error
    return parse_identity(payload)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Capture one OAuth callback, then let the server shut down."""

    path_seen: str | None = None
    # The state run_auth_flow generated for the flow in progress; ``None``
    # until the first flow arms it.
    expected_state: str | None = None
    # Whether a callback for some *other* state has already been reported this
    # flow. It is worth saying once — see do_GET — and only once.
    mismatch_reported: bool = False
    # Bounds a single accepted-but-silent connection (e.g. a browser
    # preconnect that never sends a request line) so it cannot block forever
    # regardless of the overall flow deadline.
    timeout = _SOCKET_TIMEOUT_SECONDS

    def do_GET(self) -> None:
        # Only a request carrying this flow's own state *and* a code or error
        # is the real OAuth callback. Anything else — a preconnect that did
        # send a request line, a favicon probe, or any other local process
        # poking the port with ?error=x — gets a neutral reply and must not be
        # mistaken for the callback: the wait loop below would end on it and
        # the real callback would never be read. Checking the state here, and
        # not only in parse_callback, is what stops such a request from ending
        # the wait. It raises the bar rather than closing the hole: the state
        # is printed to the terminal and handed to webbrowser.open, so it
        # lands in a child process's argv where any process running as this
        # user can read it.
        query = _query(self.path)
        expected = type(self).expected_state
        carries_callback = "code" in query or "error" in query
        is_callback = (
            expected is not None
            and _state_matches(query, expected)
            and carries_callback
        )
        if is_callback:
            type(self).path_seen = self.path
        elif carries_callback and not type(self).mismatch_reported:
            # Swallowing this silently is right for a hostile probe but wrong
            # for the common cause: a consent tab left over from an earlier
            # --auth run, reloaded by browser session restore. Without a word
            # the user watches a silent five-minute hang and then a bare
            # timeout, so say it once. (log_message is suppressed, so print.)
            type(self).mismatch_reported = True
            print(
                "Ignoring an authorization callback from a different run "
                "(most likely a stale authorization tab reloaded by your "
                "browser). Still waiting for this run's callback — complete "
                "the consent page just opened.",
                flush=True,
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if is_callback:
            # Neutral on purpose: a state-valid ?error=access_denied is the
            # callback and ends the wait, but parse_callback then rejects it.
            # Saying "Authorized" here would contradict the terminal.
            self.wfile.write(b"Authorization response received; return to the terminal.")
        else:
            self.wfile.write(b"Waiting for the authorization callback...")

    def log_message(self, format: str, *args: object) -> None:
        return  # keep the watcher's stdout clean


def run_auth_flow(
    login: str,
    credentials: TwitchCredentials,
    store: TokenStore,
    *,
    open_browser: Callable[[str], bool] = webbrowser.open,
) -> StoredToken:
    """Run the browser consent flow for one account and store its token."""
    # Twitch always returns logins lowercased; normalize the request to match,
    # since chatters.py later looks the token up by the normalized handle.
    login = normalize_handle(login)
    state = secrets.token_urlsafe(24)
    _CallbackHandler.path_seen = None
    _CallbackHandler.mismatch_reported = False
    _CallbackHandler.expected_state = state
    try:
        server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _CallbackHandler)
    except OSError as error:
        raise AuthError(
            f"could not bind to port {REDIRECT_PORT} (already in use?)"
        ) from error

    # Only now, with the redirect listener owning the port: authorizing first
    # would leave a window in which any local process could bind 8419 and
    # receive the one-time authorization code.
    url = authorize_url(credentials.client_id, state)
    print(f"Opening browser to authorize {login} …", flush=True)
    print(f"If it does not open, visit:\n  {url}", flush=True)
    open_browser(url)

    with server:
        deadline = time.monotonic() + _FLOW_TIMEOUT_SECONDS
        # do_GET only records path_seen for a request that actually carries
        # this flow's state and the callback (a code or error), so a stray
        # request — a preconnect that did send a request line, a favicon
        # probe, another local process hitting the port with ?error=x — is
        # tolerated and does not end this loop. A connection that never sends
        # a request line at all (e.g. a bare TCP preconnect) is also
        # tolerated, just silently rather than with a reply. Keep accepting
        # requests until we see the real callback or the budget expires.
        while _CallbackHandler.path_seen is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            server.timeout = remaining
            server.handle_request()

    seen = _CallbackHandler.path_seen
    if seen is None:
        raise AuthError("timed out waiting for the browser callback")

    access, refresh = _exchange_code(parse_callback(seen, state), credentials)
    authorized_login, user_id = _identify(access, credentials)
    if authorized_login != login:
        # Easy mistake with two accounts: the browser was signed in as the other one.
        raise AuthError(
            f"authorized as {authorized_login!r}, not {login!r}; "
            "sign out of Twitch in your browser and retry"
        )
    token = StoredToken(login, user_id, access, refresh)
    store.save(token)
    return token
