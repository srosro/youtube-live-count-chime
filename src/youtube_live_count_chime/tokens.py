"""Persist per-account Twitch user tokens used to read chat rosters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from json import JSONDecodeError
import os
from pathlib import Path
import tempfile
from typing import Final, cast

from youtube_live_count_chime.models import normalize_handle


DEFAULT_DIR: Final = Path.home() / ".config" / "count-chime" / "tokens"

_FIELDS: Final = ("login", "user_id", "access_token", "refresh_token")


class TokenStoreError(RuntimeError):
    """Raised when a token file cannot be read or written.

    Every store failure wears this type, reads and writes alike: callers absorb
    it as a degradation, and a bare OSError escaping a write (an unwritable
    ~/.config, ENOSPC, EACCES) would instead reach the monitor's boundary and
    tear down every watched channel over a token-file problem.
    """


@dataclass(frozen=True, slots=True)
class StoredToken:
    """One Twitch account's user token, stored in a file named for its login."""

    login: str
    user_id: str
    access_token: str
    refresh_token: str


class TokenStore:
    """One owner-only JSON file per Twitch login, in an owner-only directory."""

    __slots__ = ("directory",)

    def __init__(self, directory: Path = DEFAULT_DIR) -> None:
        self.directory = directory

    def _path(self, login: str) -> Path:
        # One file per account, so a save is a whole-file replace rather than a
        # read-modify-write: the watcher rotating one account's token and an
        # `--auth` in another terminal authorizing a second account write
        # different files and cannot lose each other's update. normalize_handle
        # is what keeps a login from naming a path outside the directory.
        return self.directory / f"{normalize_handle(login)}.json"

    def get(self, login: str) -> StoredToken | None:
        """Return the stored token for a login, or ``None`` if not authorized."""
        path = self._path(login)
        if not path.is_file():
            return None
        try:
            raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
        except (JSONDecodeError, OSError, UnicodeDecodeError) as error:
            raise TokenStoreError(f"could not read {path}: {error}") from error
        if not isinstance(raw, dict):
            raise TokenStoreError(f"{path} is not a token object")
        values = [cast("dict[str, object]", raw).get(name) for name in _FIELDS]
        if not all(isinstance(value, str) and value for value in values):
            raise TokenStoreError(f"{path} is malformed")
        return StoredToken(*cast("list[str]", values))

    def save(self, token: StoredToken) -> None:
        """Store one account's token, replacing any previous one for that login."""
        path = self._path(token.login)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # The file names are account logins, so the directory listing is
            # itself worth keeping private, and a directory left loose by an
            # older version must not stay that way.
            self.directory.chmod(0o700)
            self._write(path, token)
        except OSError as error:
            raise TokenStoreError(f"could not write {path}: {error}") from error

    def _write(self, path: Path, token: StoredToken) -> None:
        """Replace one account's file with ``token``, atomically and owner-only."""
        # Write a sibling temp file and rename it over the real path: a crash
        # (or a full disk) mid-write then leaves the previous file intact
        # instead of truncated JSON that fails every later read. mkstemp
        # creates the temp at 0600, so the secrets are never written to a
        # world-readable file, and the rename carries that mode across even if
        # the existing file was looser.
        descriptor, name = tempfile.mkstemp(
            dir=self.directory, prefix=f"{path.name}.", suffix=".tmp"
        )
        temp = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(asdict(token), handle, indent=2)
                # Closing only flushes to the page cache, so without this the
                # rename can reach disk before the data: a power loss then
                # leaves exactly the zero-length/partial file the atomic write
                # exists to prevent.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
