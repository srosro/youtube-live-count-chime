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

        # A chatter handle is third-party input. It must reach AppleScript as a
        # single opaque argv element, never as script text.
        hostile = '" & (do shell script "touch /tmp/pwned") & "'
        with patch("youtube_live_count_chime.notify.subprocess.run") as run:
            post_notification(hostile, "body")

        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ("--", "body", hostile))
        self.assertNotIn(hostile, " ".join(command[:-1]))

    def test_failure_becomes_notification_error_without_the_notification_text(
        self,
    ) -> None:
        # TimeoutExpired is the observable half of the bounded-wait contract: a
        # wedged osascript (Notification Center hung, a stuck TCC prompt) ends
        # as a NotificationError the caller can warn on, never a blocked caller.
        # The failure is warned into the LaunchAgent's log file, so it must not
        # carry the argv: that is the inferred chatter handle and the counts of
        # every watched channel.
        argv = ["/usr/bin/osascript", "--", "twitch chan 4", "joe_doe is watching"]
        failures: tuple[Exception, ...] = (
            subprocess.CalledProcessError(1, argv, stderr="boom"),
            FileNotFoundError("osascript not found"),
            subprocess.TimeoutExpired(argv, 10.0),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch(
                    "youtube_live_count_chime.notify.subprocess.run", side_effect=failure
                ):
                    with self.assertRaises(NotificationError) as ctx:
                        post_notification("joe_doe is watching", "twitch chan 4")

                rendered = f"{ctx.exception}{ctx.exception.__cause__}"
                self.assertNotIn("joe_doe", rendered)
                self.assertNotIn("twitch chan", rendered)
                self.assertIn(type(failure).__name__, rendered)


if __name__ == "__main__":
    unittest.main()
