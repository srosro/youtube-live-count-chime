"""Guard the installer's argument parser, where the failure actually occurred.

``--auth`` (and any abbreviation of it) exits after authorizing one account, so
baked into the LaunchAgent it respawn-loops under KeepAlive, and the installer's
validation run would have opened a browser. This runs the real script, so the
guard is exercised at the shell parser rather than restated in Python.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-launchagent.sh"


class InstallLaunchAgentArgumentTests(unittest.TestCase):
    def setUp(self) -> None:
        # The script must not be able to reach anything real. PATH holds only a
        # uname claiming macOS (so the parser is reached off macOS too) and a
        # stand-in watcher that records having been run at all; HOME is a
        # throwaway. A recorded run, or a LaunchAgents directory, means the
        # guard let the script past the parser and into the real install.
        root = Path(self.enterContext(TemporaryDirectory()))
        self.home = root / "home"
        self.home.mkdir()
        self.ran = root / "watcher-was-run"
        self.path = root / "bin"
        self.path.mkdir()
        self._fake("uname", "echo Darwin")
        self._fake("youtube-live-count-chime", f'touch "{self.ran}"')
        self._fake("launchctl", "exit 0")

    def _fake(self, name: str, body: str) -> None:
        stub = self.path / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)

    def _install(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *args],
            # The fakes shadow the real uname, launchctl, and watcher; the rest
            # of PATH is only there for coreutils.
            env={"PATH": f"{self.path}:/usr/bin:/bin", "HOME": str(self.home)},
            capture_output=True,
            text=True,
            timeout=30.0,
        )

    def test_auth_and_its_abbreviations_are_refused_before_anything_happens(
        self,
    ) -> None:
        for flag in ("--auth", "--au", "--a"):
            with self.subTest(flag=flag):
                result = self._install(flag, "watchmepivot", "-t", "watchmepivot")

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(flag, result.stderr)
                self.assertFalse(self.ran.exists())  # no --check, so no browser
                self.assertFalse((self.home / "Library").exists())

    def test_channel_flags_alone_get_past_the_parser(self) -> None:
        # Otherwise the guard above would pass just as well by rejecting
        # everything. The stand-in watcher stands in for --check succeeding.
        result = self._install("-y", "@mkbhd")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.ran.exists())
        self.assertTrue((self.home / "Library" / "LaunchAgents").is_dir())


if __name__ == "__main__":
    unittest.main()
