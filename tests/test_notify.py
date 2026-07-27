import subprocess
import unittest
from unittest.mock import patch

from youtube_live_count_chime.notify import NotificationError, post_notification


class PostNotificationTests(unittest.TestCase):
    def test_passes_text_as_argv_after_a_separator(self) -> None:
        with patch("youtube_live_count_chime.notify.subprocess.run") as run:
            post_notification("a title", "a body")

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/osascript")
        # Body then title, positionally, after the argv separator.
        self.assertEqual(command[-3:], ("--", "a body", "a title"))

    def test_shell_metacharacters_survive_verbatim_as_one_argument(self) -> None:
        # A chatter handle is third-party input. It must reach AppleScript as a
        # single opaque argv element, never as script text.
        hostile = '" & (do shell script "touch /tmp/pwned") & "'
        with patch("youtube_live_count_chime.notify.subprocess.run") as run:
            post_notification(hostile, "body")

        command = run.call_args.args[0]
        self.assertEqual(command[-1], hostile)
        self.assertNotIn(hostile, " ".join(command[:-1]))

    def test_failure_becomes_notification_error(self) -> None:
        failures: tuple[Exception, ...] = (
            subprocess.CalledProcessError(1, "osascript", stderr="boom"),
            FileNotFoundError("osascript not found"),
            subprocess.TimeoutExpired("osascript", 10.0),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch(
                    "youtube_live_count_chime.notify.subprocess.run", side_effect=failure
                ):
                    with self.assertRaises(NotificationError):
                        post_notification("t", "b")

    def test_a_wedged_osascript_is_bounded_by_a_timeout(self) -> None:
        # A hung osascript (Notification Center wedged, a stuck TCC prompt)
        # must not block the caller forever — subprocess.run is given a
        # timeout, and subprocess enforces it by raising TimeoutExpired.
        with patch("youtube_live_count_chime.notify.subprocess.run") as run:
            post_notification("t", "b")

        self.assertEqual(run.call_args.kwargs["timeout"], 10.0)


if __name__ == "__main__":
    unittest.main()
