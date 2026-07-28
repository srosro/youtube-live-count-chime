from pathlib import Path
import os
import stat
import tempfile
from typing import IO
import unittest
from unittest.mock import patch
import json

from youtube_live_count_chime.tokens import StoredToken, TokenStore, TokenStoreError


def _token(login: str) -> StoredToken:
    return StoredToken(login, f"id-{login}", "access-placeholder", "refresh-placeholder")


class TokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.directory = Path(self._dir.name) / "tokens"

    def _file(self, login: str) -> Path:
        return self.directory / f"{login}.json"

    def test_missing_file_has_no_tokens(self) -> None:
        self.assertIsNone(TokenStore(self.directory).get("watchmepivot"))

    def test_saved_token_round_trips(self) -> None:
        store = TokenStore(self.directory)
        store.save(_token("watchmepivot"))

        restored = TokenStore(self.directory).get("watchmepivot")
        assert restored is not None
        self.assertEqual(restored.user_id, "id-watchmepivot")

    def test_two_accounts_coexist(self) -> None:
        # The whole reason the store is keyed by login: one token per account.
        # Saving B must not rewrite A's file at all, which is what makes
        # concurrent saves from different processes safe.
        store = TokenStore(self.directory)
        store.save(_token("watchmepivot"))
        before = self._file("watchmepivot").read_bytes()

        store.save(_token("samtriestobuild"))

        reloaded = TokenStore(self.directory)
        self.assertIsNotNone(reloaded.get("watchmepivot"))
        self.assertIsNotNone(reloaded.get("samtriestobuild"))
        self.assertEqual(self._file("watchmepivot").read_bytes(), before)

    def test_a_login_is_looked_up_and_stored_under_its_normalized_form(self) -> None:
        # The file name is derived from the login, so a mixed-case or @-prefixed
        # request must reach the same file the watcher reads back.
        TokenStore(self.directory).save(_token("watchmepivot"))

        self.assertIsNotNone(TokenStore(self.directory).get("@WatchMePivot"))

    def test_resaving_a_login_replaces_it(self) -> None:
        store = TokenStore(self.directory)
        store.save(_token("watchmepivot"))
        store.save(StoredToken("watchmepivot", "id-new", "a2", "r2"))

        restored = TokenStore(self.directory).get("watchmepivot")
        assert restored is not None
        self.assertEqual(restored.user_id, "id-new")

    def test_neither_the_file_nor_its_directory_is_readable_by_other_users(self) -> None:
        TokenStore(self.directory).save(_token("watchmepivot"))

        self.assertEqual(stat.S_IMODE(self._file("watchmepivot").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_corrupt_file_fails_loudly(self) -> None:
        self.directory.mkdir(parents=True)
        self._file("watchmepivot").write_text("{not json", encoding="utf-8")

        with self.assertRaises(TokenStoreError):
            TokenStore(self.directory).get("watchmepivot")

    def test_replacing_a_loose_file_is_owner_only_during_and_after_the_write(
        self,
    ) -> None:
        # Regression test: if the account's file exists at 0644 (e.g., from an
        # older version), the secrets must never land in a world-readable file —
        # not while they are being written, and not once the write is done. An
        # implementation that copied the destination's mode across the replace
        # would leave a 0644 file full of secrets. Same for a loose directory.
        self.directory.mkdir(parents=True)
        self.directory.chmod(0o755)
        self._file("watchmepivot").write_text("{}", encoding="utf-8")
        self._file("watchmepivot").chmod(0o644)

        mode_at_write: list[int] = []

        # Patch json.dump to record the mode of the file *actually receiving
        # the bytes* — fstat on the descriptor being written, not a stat of a
        # path that may not be the write target. A post-hoc check of the final
        # file would pass even if the secrets had landed somewhere loose first.
        original_dump = json.dump

        def recording_dump(obj: object, fp: IO[str], *, indent: int | None = None) -> None:
            mode_at_write.append(stat.S_IMODE(os.fstat(fp.fileno()).st_mode))
            original_dump(obj, fp, indent=indent)

        with patch("youtube_live_count_chime.tokens.json.dump", side_effect=recording_dump):
            TokenStore(self.directory).save(_token("watchmepivot"))

        self.assertEqual(len(mode_at_write), 1, "json.dump should be called exactly once")
        self.assertEqual(
            mode_at_write[0], 0o600, "file mode must be 0600 when secrets are written"
        )
        self.assertEqual(
            stat.S_IMODE(self._file("watchmepivot").stat().st_mode),
            0o600,
            "the replaced file must not inherit the loose file's mode",
        )
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_a_crash_while_rotating_leaves_the_previous_token_readable(self) -> None:
        # The one production path that overwrites an existing token file is a
        # refresh rotation (ChatterClient._refresh -> save). An implementation
        # that truncated or unlinked the target before writing would destroy a
        # live token on every failed refresh, leaving unparseable JSON that
        # fails every later read for that account.
        store = TokenStore(self.directory)
        store.save(_token("watchmepivot"))

        with patch(
            "youtube_live_count_chime.tokens.json.dump", side_effect=OSError("disk full")
        ):
            with self.subTest("rotation"):
                with self.assertRaises(TokenStoreError):
                    store.save(StoredToken("watchmepivot", "id-rotated", "a2", "r2"))

                restored = TokenStore(self.directory).get("watchmepivot")
                assert restored is not None
                self.assertEqual(restored.user_id, "id-watchmepivot")

            with self.subTest("never-authorized"):
                # A failed save for a login with no file yet must leave that
                # login reading back as None (not authorized, run --auth), not
                # raise TokenStoreError. An implementation that opened the
                # destination path directly would leave a zero-length file
                # there, flipping one into the other.
                with self.assertRaises(TokenStoreError):
                    TokenStore(self.directory).save(_token("samtriestobuild"))

                self.assertIsNone(TokenStore(self.directory).get("samtriestobuild"))

        # And no half-written temp file or zero-length leftover is left
        # behind next to the pre-existing account's file.
        self.assertEqual(
            sorted(p.name for p in self.directory.iterdir()), ["watchmepivot.json"]
        )

    def test_an_unwritable_location_fails_as_a_store_error_not_a_bare_oserror(
        self,
    ) -> None:
        # Callers absorb TokenStoreError as a degradation and let everything
        # else through to the watcher's fail-loud boundary. A write that cannot
        # land — an unwritable ~/.config, a full disk, a parent path occupied
        # by a file — is a store failure, not a bug, so it must not escape as a
        # bare OSError and cancel every watched channel. (A regular file where
        # the parent directory should be reproduces that without depending on
        # permissions, which do not constrain a root test runner.)
        blocked = Path(self._dir.name) / "count-chime"
        blocked.write_text("", encoding="utf-8")

        with self.assertRaises(TokenStoreError):
            TokenStore(blocked / "tokens").save(_token("watchmepivot"))


if __name__ == "__main__":
    unittest.main()
