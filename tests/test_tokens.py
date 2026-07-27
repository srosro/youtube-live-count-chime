import json
from pathlib import Path
import stat
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
