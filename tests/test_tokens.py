from pathlib import Path
import os
import stat
import tempfile
import threading
import time
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

    def test_replacing_a_loose_store_is_owner_only_during_and_after_the_write(
        self,
    ) -> None:
        # Regression test: if tokens.json exists at 0644 (e.g., from an older
        # version), the secrets must never land in a world-readable file — not
        # while they are being written, and not once the write is done. An
        # implementation that copied the destination's mode across the replace
        # would leave a 0644 file full of secrets.
        self.path.write_text("{}", encoding="utf-8")
        self.path.chmod(0o644)

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
            store = TokenStore(self.path)
            store.save(_token("watchmepivot"))

        self.assertEqual(len(mode_at_write), 1, "json.dump should be called exactly once")
        self.assertEqual(
            mode_at_write[0], 0o600, "file mode must be 0600 when secrets are written"
        )
        self.assertEqual(
            stat.S_IMODE(self.path.stat().st_mode),
            0o600,
            "the replaced store must not inherit the loose file's mode",
        )

    def test_a_crash_mid_write_leaves_the_previous_tokens_intact(self) -> None:
        # An interrupted write must not truncate the store: a half-written file
        # is unparseable JSON, which fails every later read for every account.
        store = TokenStore(self.path)
        store.save(_token("watchmepivot"))

        with patch(
            "youtube_live_count_chime.tokens.json.dump", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                store.save(_token("samtriestobuild"))

        reloaded = TokenStore(self.path)
        restored = reloaded.get("watchmepivot")
        assert restored is not None
        self.assertEqual(restored.user_id, "id-watchmepivot")
        self.assertIsNone(reloaded.get("samtriestobuild"))
        # And no half-written temp file is left behind next to the store.
        self.assertEqual(
            sorted(p.name for p in Path(self._dir.name).iterdir()), ["tokens.json"]
        )

    def test_concurrent_saves_keep_both_accounts(self) -> None:
        # Both watched channels refresh through one store from their own
        # to_thread workers. An unlocked load-modify-write interleaves as
        # load-load-write-write and drops whichever token was written first.
        store = TokenStore(self.path)
        original_dump = json.dump

        def slow_dump(obj: object, fp: IO[str], *, indent: int | None = None) -> None:
            # Widen the window between the load and the write so an unlocked
            # implementation loses a token deterministically. It only has to
            # outlast thread-start latency, so keep it small.
            time.sleep(0.005)
            original_dump(obj, fp, indent=indent)

        with patch(
            "youtube_live_count_chime.tokens.json.dump", side_effect=slow_dump
        ):
            threads = [
                threading.Thread(target=store.save, args=(_token(login),))
                for login in ("watchmepivot", "samtriestobuild")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        reloaded = TokenStore(self.path)
        self.assertIsNotNone(reloaded.get("watchmepivot"))
        self.assertIsNotNone(reloaded.get("samtriestobuild"))


if __name__ == "__main__":
    unittest.main()
