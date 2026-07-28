"""Run macOS helper binaries as argv: bounded, checked, never through a shell."""

import subprocess
from typing import Final, Sequence


# Everything run_macos_command can fail with: a missing or non-executable
# binary, a non-zero exit, and the caller's bound firing.
MACOS_COMMAND_FAILURES: Final = (
    OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
)


def run_macos_command(command: Sequence[str], *, timeout: float) -> None:
    """Run one command synchronously, raising on failure or on the bound."""
    subprocess.run(
        tuple(command), check=True, timeout=timeout,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
