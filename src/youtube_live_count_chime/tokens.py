"""Persist per-account Twitch user tokens used to read chat rosters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from json import JSONDecodeError
import os
from pathlib import Path
import tempfile
import threading
from typing import Final, cast


DEFAULT_PATH: Final = Path.home() / ".config" / "count-chime" / "tokens.json"

_FIELDS: Final = ("login", "user_id", "access_token", "refresh_token")


class TokenStoreError(RuntimeError):
    """Raised when the token file exists but cannot be used."""


@dataclass(frozen=True, slots=True)
class StoredToken:
    """One Twitch account's user token, keyed in the store by login."""

    login: str
    user_id: str
    access_token: str
    refresh_token: str


class TokenStore:
    """A JSON file of user tokens, keyed by Twitch login, readable only by us."""

    __slots__ = ("path", "_lock")

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        # save() is a read-modify-write, and every watched channel refreshes
        # its own token through this one store from its own to_thread worker.
        # Without the lock two concurrent rotations interleave load-load-
        # write-write and one account's new token is lost.
        self._lock = threading.Lock()

    def _load(self) -> dict[str, StoredToken]:
        if not self.path.is_file():
            return {}
        try:
            raw = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (JSONDecodeError, OSError, UnicodeDecodeError) as error:
            raise TokenStoreError(f"could not read {self.path}: {error}") from error
        if not isinstance(raw, dict):
            raise TokenStoreError(f"{self.path} is not a token object")

        tokens: dict[str, StoredToken] = {}
        for login, entry in cast("dict[str, object]", raw).items():
            if not isinstance(entry, dict):
                raise TokenStoreError(f"{self.path} has a malformed entry")
            fields = cast("dict[str, object]", entry)
            values = [fields.get(name) for name in _FIELDS]
            if not all(isinstance(value, str) and value for value in values):
                raise TokenStoreError(f"{self.path} has a malformed entry")
            tokens[login] = StoredToken(*cast("list[str]", values))
        return tokens

    def get(self, login: str) -> StoredToken | None:
        """Return the stored token for a login, or ``None`` if not authorized."""
        return self._load().get(login)

    def save(self, token: StoredToken) -> None:
        """Store one account's token, replacing any previous one for that login."""
        with self._lock:
            tokens = self._load()
            tokens[token.login] = token
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(tokens)

    def _write(self, tokens: dict[str, StoredToken]) -> None:
        """Replace the store with ``tokens``, atomically and owner-only."""
        # Write a sibling temp file and rename it over the real path: a crash
        # (or a full disk) mid-write then leaves the previous store intact
        # instead of truncated JSON that fails every later read. mkstemp
        # creates the temp at 0600, so the secrets are never written to a
        # world-readable file, and the rename carries that mode across even if
        # the existing store was looser.
        descriptor, name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f"{self.path.name}.", suffix=".tmp"
        )
        temp = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({k: asdict(v) for k, v in tokens.items()}, handle, indent=2)
            os.replace(temp, self.path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
