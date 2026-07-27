"""Persist per-account Twitch user tokens used to read chat rosters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from json import JSONDecodeError
import os
from pathlib import Path
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

    __slots__ = ("path",)

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

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
        tokens = self._load()
        tokens[token.login] = token
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create with 0600 before writing so the secret is never briefly
        # world-readable.
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({k: asdict(v) for k, v in tokens.items()}, handle, indent=2)
        self.path.chmod(0o600)
