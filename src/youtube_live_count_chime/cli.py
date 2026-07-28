"""Command-line entry point for the multi-platform viewer-count watcher."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import sys
from typing import Final, Sequence, cast

from youtube_live_count_chime.auth import AuthError, run_auth_flow
from youtube_live_count_chime.chatters import ChatterClient, TwitchChatterNamer
from youtube_live_count_chime.models import StreamSource
from youtube_live_count_chime.monitor import ChimeConfig, monitor
from youtube_live_count_chime.tokens import TokenStore, TokenStoreError
from youtube_live_count_chime.twitch import (
    TwitchAuthError,
    TwitchClient,
    TwitchCredentials,
    TwitchSource,
)
from youtube_live_count_chime.youtube import YouTubeSource


DEFAULT_UP_SOUND: Final = "/System/Library/Sounds/Glass.aiff"
DEFAULT_DOWN_SOUND: Final = "/System/Library/Sounds/Basso.aiff"
_LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Config:
    """Validated command-line configuration."""

    youtube: tuple[str, ...]
    twitch: tuple[str, ...]
    up_sound: Path
    down_sound: Path
    check: bool
    auth: str | None


def _sound_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"not a sound file: {path}")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Play macOS chimes when the live viewer count of YouTube or Twitch "
            "channels rises or falls."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-y",
        "--youtube",
        action="append",
        metavar="HANDLE",
        default=[],
        help="YouTube channel handle to watch, e.g. @mkbhd (repeatable)",
    )
    parser.add_argument(
        "-t",
        "--twitch",
        action="append",
        metavar="LOGIN",
        default=[],
        help="Twitch channel login to watch, e.g. shroud (repeatable)",
    )
    parser.add_argument(
        "--up-sound",
        type=_sound_file,
        default=DEFAULT_UP_SOUND,
        help="sound played when a viewer count rises",
    )
    parser.add_argument(
        "--down-sound",
        type=_sound_file,
        default=DEFAULT_DOWN_SOUND,
        help="sound played when a viewer count falls",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate flags, handles, and that Twitch credentials are set, then exit",
    )
    parser.add_argument(
        "--auth",
        metavar="LOGIN",
        default=None,
        help=(
            "authorize one Twitch account in the browser so arrivals can be named, "
            "then exit (run once per account)"
        ),
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
        check=cast(bool, namespace.check),
        auth=cast("str | None", namespace.auth),
    )


def build_sources(config: Config) -> list[StreamSource]:
    """Build one deduplicated polling source per requested handle.

    All Twitch logins share a single client so the application token is
    fetched once rather than once per login.
    """
    if not config.youtube and not config.twitch:
        raise ValueError("provide at least one --youtube or --twitch handle")

    sources: list[StreamSource] = []
    seen: set[str] = set()

    def add(source: StreamSource) -> None:
        if source.name not in seen:
            seen.add(source.name)
            sources.append(source)

    for handle in config.youtube:
        add(YouTubeSource.for_handle(handle))

    if config.twitch:
        client = TwitchClient(TwitchCredentials.from_env())
        for login in config.twitch:
            add(TwitchSource.for_login(login, client))

    return sources


def build_namer(sources: Sequence[StreamSource]) -> TwitchChatterNamer | None:
    """Build the chat-roster namer, or ``None`` when no Twitch channel is watched.

    Derived from the sources build_sources already produced, so the credentials
    are read from the environment once and "is this a Twitch run?" is decided
    in one place.
    """
    twitch = [source for source in sources if isinstance(source, TwitchSource)]
    if not twitch:
        return None
    store = TokenStore()
    return TwitchChatterNamer(ChatterClient(twitch[0].client.credentials, store), store)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, build sources, and watch every channel until interrupted."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = parse_config(argv)

    if config.check and config.auth is not None:
        print(
            "Error: --check and --auth cannot be combined; --check validates a "
            "configuration without acting, --auth opens a browser to authorize "
            "one account. Run them separately.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    if config.auth is not None:
        try:
            token = run_auth_flow(config.auth, TwitchCredentials.from_env(), TokenStore())
        except (AuthError, TwitchAuthError, TokenStoreError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr, flush=True)
            return 2
        print(
            f"Authorized {token.login}. Arrivals on that channel can now be named.",
            flush=True,
        )
        return 0

    try:
        sources = build_sources(config)
    except (ValueError, TwitchAuthError) as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 2

    if config.check:
        print(f"OK — {len(sources)} channel(s).", flush=True)
        return 0

    chime = ChimeConfig(config.up_sound, config.down_sound)
    names = ", ".join(source.name for source in sources)
    print(f"Monitoring {names}. Press Ctrl-C to stop.", flush=True)
    try:
        asyncio.run(monitor(sources, chime, namer=build_namer(sources)))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    except Exception as error:
        _LOGGER.exception("stopped watching after an unexpected error: %s", error)
        return 1
    return 0
