"""Command-line entry point for the YouTube live-count watcher."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
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
    if not math.isfinite(interval):
        raise argparse.ArgumentTypeError("interval must be finite")
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
