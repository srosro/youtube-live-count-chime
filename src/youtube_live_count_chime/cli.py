"""Command-line entry point for the YouTube live-count watcher."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Final, Literal, Sequence, cast

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
    previous: int | None = None

    print(f"Watching {config.url}", flush=True)
    try:
        while True:
            try:
                current = fetch_viewer_count(config.url)
                if previous is None:
                    print(f"Baseline: {current} watching now", flush=True)
                    previous = current
                elif current != previous:
                    direction: Literal["up", "down"]
                    if current > previous:
                        direction = "up"
                        sound = config.up_sound
                    else:
                        direction = "down"
                        sound = config.down_sound
                    while True:
                        try:
                            play_sound(sound)
                        except SoundPlaybackError as error:
                            print(f"Warning: {error}", file=sys.stderr, flush=True)
                            time.sleep(config.interval_seconds)
                        else:
                            break
                    print(
                        f"{previous} -> {current} ({direction})",
                        flush=True,
                    )
                    previous = current
            except ViewerCountError as error:
                print(f"Warning: {error}", file=sys.stderr, flush=True)
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and start the watcher."""
    return run(parse_config(argv))
