from pathlib import Path
import stat
import tempfile
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
        self.path = Path(self._dir.name) / "tokens.json"

    def test_missing_file_has_no_tokens(self) -> None:
        self.assertIsNone(TokenStore(self.path).get("watchmepivot"))

    def test_saved_token_round_trips(self) -> None:
        store = TokenStore(self.path)
        store.save(_token("watchmepivot"))

        restored = TokenStore(self.path).get("watchmepivot")
        assert restored is not None
        self.assertEqual(restored.user_id, "id-watchmepivot")

    def test_two_accounts_coexist(self) -> None:
        # The whole reason the store is keyed by login: one token per account.
        store = TokenStore(self.path)
        store.save(_token("watchmepivot"))
        store.save(_token("samtriestobuild"))

        reloaded = TokenStore(self.path)
        self.assertIsNotNone(reloaded.get("watchmepivot"))
        self.assertIsNotNone(reloaded.get("samtriestobuild"))

    def test_resaving_a_login_replaces_it(self) -> None:
        store = TokenStore(self.path)
        store.save(_token("watchmepivot"))
        store.save(StoredToken("watchmepivot", "id-new", "a2", "r2"))

        restored = TokenStore(self.path).get("watchmepivot")
        assert restored is not None
        self.assertEqual(restored.user_id, "id-new")

    def test_file_is_not_readable_by_other_users(self) -> None:
        TokenStore(self.path).save(_token("watchmepivot"))

        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_corrupt_file_fails_loudly(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(TokenStoreError):
            TokenStore(self.path).get("watchmepivot")

    def test_existing_loose_file_is_tightened_before_writing(self) -> None:
        # Regression test: if tokens.json exists at 0644 (e.g., from an older
        # version), save() must narrow the mode to 0600 BEFORE json.dump writes
        # the secrets, not after. Otherwise the secrets are briefly world-readable.
        self.path.write_text("{}", encoding="utf-8")
        self.path.chmod(0o644)

        mode_at_write: list[int] = []

        # Patch json.dump to record the file mode when it is called (during the
        # write). This captures whether the file is already secured before secrets
        # are actually written to disk.
        original_dump = json.dump

        def recording_dump(obj: object, fp: object, **kwargs: object) -> None:
            mode_at_write.append(stat.S_IMODE(self.path.stat().st_mode))
            return original_dump(obj, fp, **kwargs)  # type: ignore

        with patch("youtube_live_count_chime.tokens.json.dump", side_effect=recording_dump):
            store = TokenStore(self.path)
            store.save(_token("watchmepivot"))

        self.assertEqual(len(mode_at_write), 1, "json.dump should be called exactly once")
        self.assertEqual(
            mode_at_write[0], 0o600, "file mode must be 0600 when secrets are written"
        )


if __name__ == "__main__":
    unittest.main()
