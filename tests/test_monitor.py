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
