"""Run macOS helper binaries as argv: bounded, checked, never through a shell."""

from __future__ import annotations

import subprocess
from typing import Final


# Everything run_macos_command can fail with: a missing or non-executable
# binary, a non-zero exit, and the caller's bound firing.
MACOS_COMMAND_FAILURES: Final = (
    OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
)


def run_macos_command(command: tuple[str, ...], *, timeout: float) -> None:
    """Run one command synchronously, raising on failure or on the bound.

    ``command`` is a tuple, not a ``Sequence[str]``: a bare ``str`` satisfies
    that alias, and passing one here would silently explode into a
    per-character argv — the exact opposite of what this function exists to
    guarantee. Centralising the call is what made that reachable; it was not
    when each caller passed an argv literal.
    """
    subprocess.run(
        command, check=True, timeout=timeout,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
