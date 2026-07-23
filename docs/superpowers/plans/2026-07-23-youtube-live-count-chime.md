# YouTube Live Count Chime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a strictly typed macOS command-line watcher that plays one direction-specific chime whenever the supplied YouTube livestream's viewer count changes.

**Architecture:** A standard-library HTTP adapter extracts YouTube's public `originalViewCount`, while a stateful watcher compares only valid observations and emits typed transitions. The CLI connects those pieces to macOS `afplay`, owns retry behavior, and exposes configurable URL, interval, and sound paths.

**Tech Stack:** Python 3.11, standard library (`urllib`, `argparse`, `subprocess`, `unittest`), setuptools, mypy strict mode, git, and GitHub CLI.

## Global Constraints

- Create the project in `/Users/so/Hacking/youtube-live-count-chime`.
- Target Python 3.11 or newer and use `/Users/so/.local/bin/python3.11` for the local virtual environment.
- Use no third-party runtime dependencies.
- Run `mypy --strict` over both production code and tests.
- Establish the first valid viewer count silently.
- Play exactly one increase chime or one decrease chime for each observed count change, regardless of jump size.
- Never chime or replace the last valid count after a failed fetch or parse.
- Default to a five-second interval, `Glass.aiff` for increases, `Basso.aiff` for decreases, and `https://www.youtube.com/watch?v=zUMYDcYRsFg`.
- Publish `main` to a private personal GitHub repository named `youtube-live-count-chime`.
- Open a pull request from `feat/live-count-chime` to `main` and run the requested `/babysit-pr` workflow to convergence without merging unless separately authorized.

## File Map

- `.gitignore`: Ignore local worktrees, the virtual environment, and generated Python/tooling files.
- `pyproject.toml`: Package metadata, console entry point, development dependency, and strict mypy configuration.
- `src/youtube_live_count_chime/__init__.py`: Package version.
- `src/youtube_live_count_chime/__main__.py`: `python -m` entry point.
- `src/youtube_live_count_chime/youtube.py`: Public-page fetch and live-count parsing.
- `src/youtube_live_count_chime/monitor.py`: Typed transition model and last-valid-count state.
- `src/youtube_live_count_chime/sounds.py`: Sound-path validation and `afplay` adapter.
- `src/youtube_live_count_chime/cli.py`: Configuration, transition notification, polling loop, and shutdown.
- `tests/fixtures/live_page.html`: Minimal representative YouTube page fragment.
- `tests/test_youtube.py`: Parser behavior.
- `tests/test_monitor.py`: Baseline, transition, unchanged-count, and failed-source behavior.
- `tests/test_sounds.py`: Sound-file validation.
- `tests/test_cli.py`: CLI validation and direction-to-sound behavior.
- `README.md`: Setup, use, customization, limitations, and development commands.

---

### Task 1: Package Foundation and YouTube Count Parser

**Files:**
- Modify: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/youtube_live_count_chime/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/live_page.html`
- Create: `tests/test_youtube.py`
- Create: `src/youtube_live_count_chime/youtube.py`

**Interfaces:**
- Produces: `ViewerCountError`
- Produces: `parse_viewer_count(page_html: str) -> int`
- Produces: `fetch_viewer_count(url: str, timeout_seconds: float = 10.0) -> int`

- [ ] **Step 1: Add package metadata and ignore rules**

Update `.gitignore`:

```gitignore
.worktrees/
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.mypy_cache/
build/
dist/
```

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "youtube-live-count-chime"
version = "0.1.0"
description = "Play a macOS chime when a YouTube livestream viewer count changes."
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["mypy>=1.10,<2"]

[project.scripts]
youtube-live-count-chime = "youtube_live_count_chime.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.mypy]
python_version = "3.11"
strict = true
files = ["src", "tests"]
```

Create `src/youtube_live_count_chime/__init__.py`:

```python
"""YouTube livestream viewer-count chimes for macOS."""

__version__ = "0.1.0"
```

Create an empty `tests/__init__.py`.

- [ ] **Step 2: Create the local development environment**

Run:

```bash
/Users/so/.local/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Expected: installation succeeds and `.venv/bin/mypy --version` reports mypy 1.x.

- [ ] **Step 3: Write the parser fixtures and failing tests**

Create `tests/fixtures/live_page.html`:

```html
<!doctype html>
<html>
  <script>
    var ytInitialData = {
      "videoViewCountRenderer": {
        "viewCount": {"runs": [{"text": "1 watching now"}]},
        "isLive": true,
        "originalViewCount": "1"
      }
    };
  </script>
</html>
```

Create `tests/test_youtube.py`:

```python
from pathlib import Path
import unittest

from youtube_live_count_chime.youtube import ViewerCountError, parse_viewer_count


FIXTURE = Path(__file__).parent / "fixtures" / "live_page.html"


class ParseViewerCountTests(unittest.TestCase):
    def test_extracts_original_live_viewer_count(self) -> None:
        page_html = FIXTURE.read_text(encoding="utf-8")

        self.assertEqual(parse_viewer_count(page_html), 1)

    def test_accepts_viewer_counts_with_multiple_digits(self) -> None:
        page_html = '{"originalViewCount":"12345"}'

        self.assertEqual(parse_viewer_count(page_html), 12_345)

    def test_rejects_page_without_live_viewer_count(self) -> None:
        with self.assertRaisesRegex(
            ViewerCountError, "live viewer count was not found"
        ):
            parse_viewer_count("<html>not a livestream count</html>")

    def test_rejects_non_numeric_live_viewer_count(self) -> None:
        with self.assertRaisesRegex(
            ViewerCountError, "live viewer count was not found"
        ):
            parse_viewer_count('{"originalViewCount":"many"}')


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the parser tests and verify the expected failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_youtube -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'youtube_live_count_chime.youtube'`.

- [ ] **Step 5: Implement the parser and HTTP adapter**

Create `src/youtube_live_count_chime/youtube.py`:

```python
"""Fetch and parse a YouTube livestream's public viewer count."""

from __future__ import annotations

import re
from re import Pattern
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_COUNT_PATTERN: Final[Pattern[str]] = re.compile(
    r'"originalViewCount":"(?P<count>[0-9]+)"'
)


class ViewerCountError(RuntimeError):
    """Raised when a valid live viewer count cannot be obtained."""


def parse_viewer_count(page_html: str) -> int:
    """Extract the exact public live viewer count from YouTube page HTML."""
    match = _COUNT_PATTERN.search(page_html)
    if match is None:
        raise ViewerCountError("live viewer count was not found in the page")
    return int(match.group("count"))


def fetch_viewer_count(url: str, timeout_seconds: float = 10.0) -> int:
    """Download a YouTube watch page and return its live viewer count."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            page_html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ViewerCountError(f"could not fetch the livestream page: {error}") from error

    return parse_viewer_count(page_html)
```

- [ ] **Step 6: Run parser tests and strict typing**

Run:

```bash
.venv/bin/python -m unittest tests.test_youtube -v
.venv/bin/mypy src tests
```

Expected: four tests pass and mypy reports `Success: no issues found`.

- [ ] **Step 7: Commit the parser slice**

Run:

```bash
git add .gitignore pyproject.toml src/youtube_live_count_chime tests
git commit -m "feat: parse YouTube live viewer counts"
```

Expected: one commit containing the package foundation, fixture, parser tests, and parser implementation.

---

### Task 2: Typed Count-Transition Watcher

**Files:**
- Create: `tests/test_monitor.py`
- Create: `src/youtube_live_count_chime/monitor.py`

**Interfaces:**
- Consumes: any callable implementing `CountSource.__call__() -> int`
- Consumes: any callable implementing `TransitionSink.__call__(transition: Transition) -> None`
- Produces: `Direction.UP` and `Direction.DOWN`
- Produces: `Transition(previous: int, current: int, direction: Direction)`
- Produces: `Watcher(source: CountSource, sink: TransitionSink)` with `poll() -> int`

- [ ] **Step 1: Write the failing watcher tests**

Create `tests/test_monitor.py`:

```python
import unittest

from youtube_live_count_chime.monitor import Direction, Transition, Watcher


class SequenceSource:
    def __init__(self, values: list[int | Exception]) -> None:
        self._values = values.copy()

    def __call__(self) -> int:
        value = self._values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class RecordingSink:
    def __init__(self) -> None:
        self.transitions: list[Transition] = []

    def __call__(self, transition: Transition) -> None:
        self.transitions.append(transition)


class WatcherTests(unittest.TestCase):
    def test_first_valid_count_is_a_silent_baseline(self) -> None:
        sink = RecordingSink()
        watcher = Watcher(SequenceSource([1]), sink)

        self.assertEqual(watcher.poll(), 1)
        self.assertEqual(sink.transitions, [])

    def test_upward_jump_emits_one_increase_transition(self) -> None:
        sink = RecordingSink()
        watcher = Watcher(SequenceSource([1, 4]), sink)

        watcher.poll()
        watcher.poll()

        self.assertEqual(
            sink.transitions,
            [Transition(previous=1, current=4, direction=Direction.UP)],
        )

    def test_downward_jump_emits_one_decrease_transition(self) -> None:
        sink = RecordingSink()
        watcher = Watcher(SequenceSource([7, 2]), sink)

        watcher.poll()
        watcher.poll()

        self.assertEqual(
            sink.transitions,
            [Transition(previous=7, current=2, direction=Direction.DOWN)],
        )

    def test_unchanged_count_emits_no_transition(self) -> None:
        sink = RecordingSink()
        watcher = Watcher(SequenceSource([3, 3]), sink)

        watcher.poll()
        watcher.poll()

        self.assertEqual(sink.transitions, [])

    def test_source_failure_preserves_last_valid_count(self) -> None:
        sink = RecordingSink()
        watcher = Watcher(
            SequenceSource([1, RuntimeError("temporary failure"), 2]),
            sink,
        )

        watcher.poll()
        with self.assertRaisesRegex(RuntimeError, "temporary failure"):
            watcher.poll()
        watcher.poll()

        self.assertEqual(
            sink.transitions,
            [Transition(previous=1, current=2, direction=Direction.UP)],
        )

    def test_negative_count_is_rejected_without_replacing_baseline(self) -> None:
        sink = RecordingSink()
        watcher = Watcher(SequenceSource([2, -1, 3]), sink)

        watcher.poll()
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            watcher.poll()
        watcher.poll()

        self.assertEqual(
            sink.transitions,
            [Transition(previous=2, current=3, direction=Direction.UP)],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the watcher tests and verify the expected failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_monitor -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'youtube_live_count_chime.monitor'`.

- [ ] **Step 3: Implement typed transition detection**

Create `src/youtube_live_count_chime/monitor.py`:

```python
"""Track valid viewer counts and emit direction-aware transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Direction(Enum):
    """The direction of a live viewer-count change."""

    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class Transition:
    """A change between two valid live viewer counts."""

    previous: int
    current: int
    direction: Direction


class CountSource(Protocol):
    """Return the next valid viewer count or raise an exception."""

    def __call__(self) -> int:
        """Read one viewer count."""
        ...


class TransitionSink(Protocol):
    """Receive a viewer-count transition."""

    def __call__(self, transition: Transition) -> None:
        """Handle one transition."""
        ...


class Watcher:
    """Compare successful observations while retaining the last valid count."""

    def __init__(self, source: CountSource, sink: TransitionSink) -> None:
        self._source = source
        self._sink = sink
        self._previous: int | None = None

    def poll(self) -> int:
        """Read once, emit at most one transition, and return the count."""
        current = self._source()
        if current < 0:
            raise ValueError("viewer count cannot be negative")

        previous = self._previous
        if previous is None or current == previous:
            self._previous = current
            return current

        direction = Direction.UP if current > previous else Direction.DOWN
        transition = Transition(
            previous=previous,
            current=current,
            direction=direction,
        )
        self._previous = current
        self._sink(transition)
        return current
```

- [ ] **Step 4: Run watcher tests, the full suite, and strict typing**

Run:

```bash
.venv/bin/python -m unittest tests.test_monitor -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy src tests
```

Expected: ten total tests pass and mypy reports `Success: no issues found`.

- [ ] **Step 5: Commit the watcher slice**

Run:

```bash
git add src/youtube_live_count_chime/monitor.py tests/test_monitor.py
git commit -m "feat: detect live viewer count transitions"
```

Expected: one commit containing only transition state and its tests.

---

### Task 3: macOS Sounds and Typed CLI

**Files:**
- Create: `tests/test_sounds.py`
- Create: `tests/test_cli.py`
- Create: `src/youtube_live_count_chime/sounds.py`
- Create: `src/youtube_live_count_chime/cli.py`
- Create: `src/youtube_live_count_chime/__main__.py`

**Interfaces:**
- Consumes: `Direction`, `Transition`, `Watcher`, and `fetch_viewer_count`
- Produces: `require_sound_file(path: Path) -> Path`
- Produces: `play_sound(path: Path) -> None`
- Produces: `Config(url: str, interval_seconds: float, up_sound: Path, down_sound: Path)`
- Produces: `parse_config(argv: Sequence[str] | None = None) -> Config`
- Produces: `ChimeNotifier(config: Config, player: SoundPlayer, stdout: TextIO, stderr: TextIO)`
- Produces: `run(config: Config) -> int` and `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Write failing sound validation tests**

Create `tests/test_sounds.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from youtube_live_count_chime.sounds import SoundConfigurationError, require_sound_file


class SoundValidationTests(unittest.TestCase):
    def test_accepts_existing_regular_file(self) -> None:
        with TemporaryDirectory() as directory:
            sound = Path(directory) / "tone.aiff"
            sound.write_bytes(b"test audio fixture")

            self.assertEqual(require_sound_file(sound), sound)

    def test_rejects_missing_file(self) -> None:
        missing = Path("/definitely/missing/youtube-live-count-chime.aiff")

        with self.assertRaisesRegex(SoundConfigurationError, "does not exist"):
            require_sound_file(missing)

    def test_rejects_directory(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                SoundConfigurationError, "is not a regular file"
            ):
                require_sound_file(Path(directory))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run sound tests and verify the expected failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_sounds -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'youtube_live_count_chime.sounds'`.

- [ ] **Step 3: Implement sound validation and playback**

Create `src/youtube_live_count_chime/sounds.py`:

```python
"""Validate and play local audio files through macOS afplay."""

from __future__ import annotations

from pathlib import Path
import subprocess


class SoundConfigurationError(ValueError):
    """Raised when a configured sound path is unusable."""


class SoundPlaybackError(RuntimeError):
    """Raised when macOS cannot play a configured sound."""


def require_sound_file(path: Path) -> Path:
    """Return an existing regular sound file or raise a configuration error."""
    if not path.exists():
        raise SoundConfigurationError(f"sound file does not exist: {path}")
    if not path.is_file():
        raise SoundConfigurationError(f"sound path is not a regular file: {path}")
    return path


def play_sound(path: Path) -> None:
    """Play one sound synchronously with the macOS system audio player."""
    try:
        subprocess.run(
            ("/usr/bin/afplay", str(path)),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SoundPlaybackError(f"could not play {path}: {error}") from error
```

- [ ] **Step 4: Run sound tests and strict typing**

Run:

```bash
.venv/bin/python -m unittest tests.test_sounds -v
.venv/bin/mypy src tests
```

Expected: three sound tests pass and mypy reports `Success: no issues found`.

- [ ] **Step 5: Write failing CLI configuration and notification tests**

Create `tests/test_cli.py`:

```python
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from youtube_live_count_chime.cli import ChimeNotifier, Config, parse_config
from youtube_live_count_chime.monitor import Direction, Transition


class RecordingPlayer:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.paths.append(path)


class CliConfigurationTests(unittest.TestCase):
    def test_parses_url_interval_and_existing_sound_files(self) -> None:
        with TemporaryDirectory() as directory:
            up_sound = Path(directory) / "up.aiff"
            down_sound = Path(directory) / "down.aiff"
            up_sound.write_bytes(b"up")
            down_sound.write_bytes(b"down")

            config = parse_config(
                [
                    "https://example.test/live",
                    "--interval",
                    "2.5",
                    "--up-sound",
                    str(up_sound),
                    "--down-sound",
                    str(down_sound),
                ]
            )

        self.assertEqual(config.url, "https://example.test/live")
        self.assertEqual(config.interval_seconds, 2.5)
        self.assertEqual(config.up_sound, up_sound)
        self.assertEqual(config.down_sound, down_sound)

    def test_rejects_non_positive_interval(self) -> None:
        with self.assertRaises(SystemExit):
            parse_config(["--interval", "0"])

    def test_rejects_missing_sound_file(self) -> None:
        with self.assertRaises(SystemExit):
            parse_config(
                [
                    "--up-sound",
                    "/definitely/missing/up.aiff",
                    "--down-sound",
                    "/definitely/missing/down.aiff",
                ]
            )


class ChimeNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.player = RecordingPlayer()
        self.stdout = StringIO()
        self.stderr = StringIO()
        self.config = Config(
            url="https://example.test/live",
            interval_seconds=5.0,
            up_sound=Path("/sounds/up.aiff"),
            down_sound=Path("/sounds/down.aiff"),
        )
        self.notifier = ChimeNotifier(
            config=self.config,
            player=self.player,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def test_increase_uses_up_sound_once(self) -> None:
        self.notifier(
            Transition(previous=1, current=4, direction=Direction.UP)
        )

        self.assertEqual(self.player.paths, [self.config.up_sound])
        self.assertIn("1 -> 4 (up)", self.stdout.getvalue())

    def test_decrease_uses_down_sound_once(self) -> None:
        self.notifier(
            Transition(previous=5, current=2, direction=Direction.DOWN)
        )

        self.assertEqual(self.player.paths, [self.config.down_sound])
        self.assertIn("5 -> 2 (down)", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run CLI tests and verify the expected failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'youtube_live_count_chime.cli'`.

- [ ] **Step 7: Implement the typed CLI, notifier, and polling loop**

Create `src/youtube_live_count_chime/cli.py`:

```python
"""Command-line entry point for the YouTube live-count watcher."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Final, Protocol, Sequence, TextIO, cast

from youtube_live_count_chime.monitor import Direction, Transition, Watcher
from youtube_live_count_chime.sounds import (
    SoundConfigurationError,
    SoundPlaybackError,
    play_sound,
    require_sound_file,
)
from youtube_live_count_chime.youtube import ViewerCountError, fetch_viewer_count


DEFAULT_URL: Final = "https://www.youtube.com/watch?v=zUMYDcYRsFg"
DEFAULT_UP_SOUND: Final = Path("/System/Library/Sounds/Glass.aiff")
DEFAULT_DOWN_SOUND: Final = Path("/System/Library/Sounds/Basso.aiff")


@dataclass(frozen=True, slots=True)
class Config:
    """Validated command-line configuration."""

    url: str
    interval_seconds: float
    up_sound: Path
    down_sound: Path


class SoundPlayer(Protocol):
    """Play one configured sound path."""

    def __call__(self, path: Path) -> None:
        """Play one sound."""
        ...


@dataclass(slots=True)
class ChimeNotifier:
    """Print transitions and play the configured direction-specific sound."""

    config: Config
    player: SoundPlayer
    stdout: TextIO
    stderr: TextIO

    def __call__(self, transition: Transition) -> None:
        print(
            f"{transition.previous} -> {transition.current} "
            f"({transition.direction.value})",
            file=self.stdout,
            flush=True,
        )
        sound = (
            self.config.up_sound
            if transition.direction is Direction.UP
            else self.config.down_sound
        )
        try:
            self.player(sound)
        except SoundPlaybackError as error:
            print(f"Warning: {error}", file=self.stderr, flush=True)


def _positive_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("interval must be a number") from error
    if interval <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than zero")
    return interval


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Play different macOS chimes when a YouTube livestream viewer "
            "count rises or falls."
        )
    )
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--interval", type=_positive_interval, default=5.0)
    parser.add_argument("--up-sound", type=Path, default=DEFAULT_UP_SOUND)
    parser.add_argument("--down-sound", type=Path, default=DEFAULT_DOWN_SOUND)
    return parser


def parse_config(argv: Sequence[str] | None = None) -> Config:
    """Parse and validate command-line arguments."""
    parser = _build_parser()
    namespace = parser.parse_args(argv)
    url = cast(str, namespace.url)
    interval_seconds = cast(float, namespace.interval)
    up_sound = cast(Path, namespace.up_sound)
    down_sound = cast(Path, namespace.down_sound)

    try:
        require_sound_file(up_sound)
        require_sound_file(down_sound)
    except SoundConfigurationError as error:
        parser.error(str(error))

    return Config(
        url=url,
        interval_seconds=interval_seconds,
        up_sound=up_sound,
        down_sound=down_sound,
    )


def run(config: Config) -> int:
    """Run until interrupted, retrying invalid observations without chiming."""
    notifier = ChimeNotifier(
        config=config,
        player=play_sound,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    watcher = Watcher(
        source=lambda: fetch_viewer_count(config.url),
        sink=notifier,
    )
    baseline_seen = False

    print(f"Watching {config.url}", flush=True)
    try:
        while True:
            try:
                count = watcher.poll()
            except ViewerCountError as error:
                print(f"Warning: {error}", file=sys.stderr, flush=True)
            else:
                if not baseline_seen:
                    print(f"Baseline: {count} watching now", flush=True)
                    baseline_seen = True
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and start the watcher."""
    return run(parse_config(argv))
```

Create `src/youtube_live_count_chime/__main__.py`:

```python
"""Run the watcher with ``python -m youtube_live_count_chime``."""

from youtube_live_count_chime.cli import main


raise SystemExit(main())
```

- [ ] **Step 8: Run CLI tests, the full suite, and strict typing**

Run:

```bash
.venv/bin/python -m unittest tests.test_cli -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy src tests
```

Expected: eighteen total tests pass and mypy reports `Success: no issues found`.

- [ ] **Step 9: Verify CLI discovery**

Run:

```bash
.venv/bin/youtube-live-count-chime --help
.venv/bin/python -m youtube_live_count_chime --help
```

Expected: both commands exit zero and list `--interval`, `--up-sound`, and `--down-sound`.

- [ ] **Step 10: Commit the macOS sound and CLI slice**

Run:

```bash
git add src/youtube_live_count_chime tests/test_sounds.py tests/test_cli.py
git commit -m "feat: add direction-specific macOS chimes"
```

Expected: one commit containing sound validation/playback, the typed CLI, and their tests.

---

### Task 4: User Documentation and End-to-End Verification

**Files:**
- Create: `README.md`

**Interfaces:**
- Documents: `.venv/bin/youtube-live-count-chime [URL] [options]`
- Verifies: live parsing with `fetch_viewer_count(url) -> int`

- [ ] **Step 1: Write the README**

Create `README.md`:

```markdown
# YouTube Live Count Chime

A small, strictly typed macOS command-line watcher that plays one sound when a
YouTube livestream's displayed viewer count increases and a different sound when
it decreases.

## Requirements

- macOS
- Python 3.11 or newer
- Internet access to the public YouTube watch page

The watcher has no third-party runtime dependencies. Development type checking
uses mypy.

## Setup

```bash
/Users/so/.local/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Run

The configured livestream is the default:

```bash
.venv/bin/youtube-live-count-chime
```

The first valid count is a silent baseline. After that:

- an increase plays `/System/Library/Sounds/Glass.aiff`;
- a decrease plays `/System/Library/Sounds/Basso.aiff`;
- an unchanged or invalid observation is silent.

Press `Ctrl-C` to stop.

## Options

Pass the livestream URL explicitly or change the polling interval:

```bash
.venv/bin/youtube-live-count-chime \
  'https://www.youtube.com/watch?v=zUMYDcYRsFg' \
  --interval 10
```

Use different audio files:

```bash
.venv/bin/youtube-live-count-chime \
  --up-sound /path/to/increase.aiff \
  --down-sound /path/to/decrease.aiff
```

Run `.venv/bin/youtube-live-count-chime --help` for the full command reference.

## Limitations

YouTube exposes a current total, not individual join and departure events. A join
and departure that happen between polls can cancel each other out, and only the
resulting displayed count is observable. YouTube may also change its page format;
if parsing fails, the watcher warns and retains the previous valid count rather
than producing a false chime.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy src tests
```
```

- [ ] **Step 2: Run formatting and repository checks**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exits zero; only the new README is uncommitted.

- [ ] **Step 3: Run the complete automated verification**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy src tests
.venv/bin/youtube-live-count-chime --help
```

Expected: eighteen tests pass, mypy reports `Success: no issues found`, and CLI help exits zero.

- [ ] **Step 4: Run a live YouTube parsing smoke test**

Run:

```bash
.venv/bin/python -c 'from youtube_live_count_chime.youtube import fetch_viewer_count; print(fetch_viewer_count("https://www.youtube.com/watch?v=zUMYDcYRsFg"))'
```

Expected: exits zero and prints a non-negative integer representing the current live viewer count.

- [ ] **Step 5: Confirm macOS sound prerequisites**

Run:

```bash
test -x /usr/bin/afplay
test -f /System/Library/Sounds/Glass.aiff
test -f /System/Library/Sounds/Basso.aiff
```

Expected: all three checks exit zero.

- [ ] **Step 6: Commit the documentation**

Run:

```bash
git add README.md
git commit -m "docs: add setup and usage guide"
```

Expected: the README is committed.

---

### Task 5: Publish the Feature Branch and Open a Private Pull Request

**Files:**
- No tracked file changes expected.

**Interfaces:**
- Publishes: local `main` and `feat/live-count-chime` to `srosro/youtube-live-count-chime`
- Produces: `origin` pointing to the private GitHub repository
- Produces: an open pull request from `feat/live-count-chime` into `main`

- [ ] **Step 1: Verify local readiness and GitHub authentication**

Run:

```bash
git status --short --branch
git log --oneline --decorate -5
gh auth status
```

Expected: the worktree is clean on `feat/live-count-chime`, the implementation
commits are present, and GitHub CLI reports the active `srosro` account.

- [ ] **Step 2: Confirm the target repository name is available**

Run:

```bash
gh repo view srosro/youtube-live-count-chime
```

Expected: command exits non-zero with a repository-not-found response. If the
repository already exists, inspect it and stop before changing its remote or
contents.

- [ ] **Step 3: Create the empty private repository**

Run:

```bash
gh repo create youtube-live-count-chime --private --source=. --remote=origin
```

Expected: GitHub creates `srosro/youtube-live-count-chime` and adds `origin`
without pushing a branch.

- [ ] **Step 4: Push the baseline `main` branch and feature branch**

Run:

```bash
git push origin main:main
gh repo edit srosro/youtube-live-count-chime --default-branch main
git push -u origin feat/live-count-chime
```

Expected: the documentation baseline is on remote `main`, the feature branch
tracks `origin/feat/live-count-chime`, and GitHub reports `main` as the default
branch.

- [ ] **Step 5: Open the pull request**

Create `/tmp/youtube-live-count-chime-pr-body.md`:

```markdown
## Summary

- scrape the public YouTube livestream viewer count without runtime dependencies
- play distinct macOS chimes for upward and downward count changes
- provide strict typing, unit tests, configurable sounds, and setup documentation

## Verification

- `.venv/bin/python -m unittest discover -s tests -v`
- `.venv/bin/mypy src tests`
- `.venv/bin/youtube-live-count-chime --help`
- live fetch/parsing smoke test against the configured livestream
```

Run:

```bash
gh pr create \
  --base main \
  --head feat/live-count-chime \
  --title "feat: add YouTube live count chimes" \
  --body-file /tmp/youtube-live-count-chime-pr-body.md
```

Expected: GitHub creates an open pull request and prints its URL.

- [ ] **Step 6: Verify repository privacy, branch state, and pull request**

Run:

```bash
gh repo view srosro/youtube-live-count-chime --json nameWithOwner,isPrivate,url,defaultBranchRef
gh pr view --json number,state,isDraft,baseRefName,headRefName,url
git remote -v
git status --short --branch
test "$(git rev-parse main)" = "$(gh api repos/srosro/youtube-live-count-chime/commits/main --jq .sha)"
test "$(git rev-parse HEAD)" = "$(gh api repos/srosro/youtube-live-count-chime/commits/feat/live-count-chime --jq .sha)"
```

Expected: `isPrivate` is `true`, the default branch is `main`, the PR is open
and not a draft from `feat/live-count-chime` to `main`, `origin` points to the
new repository, the feature branch tracks its remote, the worktree is clean, and
both local branch SHAs match GitHub.
