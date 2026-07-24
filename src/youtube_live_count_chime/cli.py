"""Command-line entry point for the multi-platform viewer-count watcher."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Final, Sequence, cast

from youtube_live_count_chime.models import StreamSource
from youtube_live_count_chime.monitor import ChimeConfig, monitor
from youtube_live_count_chime.twitch import TwitchCredentials, TwitchError, TwitchSource
from youtube_live_count_chime.youtube import YouTubeSource


DEFAULT_UP_SOUND: Final = "/System/Library/Sounds/Glass.aiff"
DEFAULT_DOWN_SOUND: Final = "/System/Library/Sounds/Basso.aiff"
DEFAULT_POLL_INTERVAL: Final = 5.0


@dataclass(frozen=True, slots=True)
class Config:
    """Validated command-line configuration."""

    youtube: tuple[str, ...]
    twitch: tuple[str, ...]
    up_sound: Path
    down_sound: Path
    poll_interval: float


def _sound_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"not a sound file: {path}")
    return path


def _poll_interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("poll interval must be finite and positive")
    return interval


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Play macOS chimes when the live viewer count of YouTube or Twitch "
            "channels rises or falls."
        )
    )
    parser.add_argument(
        "-y", "--youtube", action="append", metavar="HANDLE", default=[]
    )
    parser.add_argument(
        "-t", "--twitch", action="append", metavar="LOGIN", default=[]
    )
    parser.add_argument("--up-sound", type=_sound_file, default=DEFAULT_UP_SOUND)
    parser.add_argument("--down-sound", type=_sound_file, default=DEFAULT_DOWN_SOUND)
    parser.add_argument(
        "--poll-interval", type=_poll_interval, default=DEFAULT_POLL_INTERVAL
    )
    return parser


def parse_config(argv: Sequence[str] | None = None) -> Config:
    """Parse and validate command-line arguments."""
    namespace = _build_parser().parse_args(argv)
    return Config(
        youtube=tuple(cast("list[str]", namespace.youtube)),
        twitch=tuple(cast("list[str]", namespace.twitch)),
        up_sound=cast(Path, namespace.up_sound),
        down_sound=cast(Path, namespace.down_sound),
        poll_interval=cast(float, namespace.poll_interval),
    )


def build_sources(config: Config) -> list[StreamSource]:
    """Build one polling source per requested handle, reading Twitch creds once."""
    if not config.youtube and not config.twitch:
        raise SystemExit("provide at least one --youtube or --twitch handle")

    sources: list[StreamSource] = [
        YouTubeSource.for_handle(handle, poll_interval=config.poll_interval)
        for handle in config.youtube
    ]
    if config.twitch:
        credentials = TwitchCredentials.from_env()
        sources.extend(
            TwitchSource.for_login(
                login, credentials, poll_interval=config.poll_interval
            )
            for login in config.twitch
        )
    return sources


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, build sources, and watch every channel until interrupted."""
    config = parse_config(argv)
    try:
        sources = build_sources(config)
    except TwitchError as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 2

    chime = ChimeConfig(config.up_sound, config.down_sound)
    names = ", ".join(source.name for source in sources)
    print(f"Monitoring {names}. Press Ctrl-C to stop.", flush=True)
    try:
        asyncio.run(monitor(sources, chime))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0
