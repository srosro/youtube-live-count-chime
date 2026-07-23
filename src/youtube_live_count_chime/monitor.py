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
