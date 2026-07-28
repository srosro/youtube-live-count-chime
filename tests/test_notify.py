import subprocess
import unittest
from unittest.mock import patch

from youtube_live_count_chime.models import POLL_INTERVAL_SECONDS
from youtube_live_count_chime.notify import NotificationError, post_notification


class PostNotificationTests(unittest.TestCase):
    def test_runs_osascript_bounded_and_checked_with_text_as_argv(self) -> None:
        with patch("youtube_live_count_chime.notify.subprocess.run") as run:
            post_notification("a title", "a body")

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/osascript")
        # Body then title, positionally, after the argv separator.
        self.assertEqual(command[-3:], ("--", "a body", "a title"))
        # The bounded wait and the failure detection the tests below rest on:
        # without them a wedged Notification Center blocks the calling thread
        # forever and a non-zero exit becomes a silent no-op, both with the
        # rest of this suite still green.
        # Bounded by the constraint rather than by its current tuning: the
        # notifier is awaited inside the consumer's poll loop, so the timeout
        # is exactly how long a wedged banner can stall that channel — it must
        # cost at most a poll or two, and must not be so short that every real
        # banner times out instead of showing.
        timeout = run.call_args.kwargs["timeout"]
        self.assertLessEqual(timeout, 2 * POLL_INTERVAL_SECONDS)
        self.assertGreater(timeout, POLL_INTERVAL_SECONDS / 5)
        self.assertIs(run.call_args.kwargs["check"], True)

        # Notification text is not ours to trust. It must reach AppleScript as
        # a single opaque argv element, never as script text.
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
        # carry the argv: that is the counts of every watched channel.
        argv = ["/usr/bin/osascript", "--", "twitch chan 4", "+2 watching twitch chan"]
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
                        post_notification("+2 watching twitch chan", "twitch chan 4")

                rendered = f"{ctx.exception}{ctx.exception.__cause__}"
                self.assertNotIn("watching", rendered)
                self.assertNotIn("twitch chan", rendered)
                self.assertIn(type(failure).__name__, rendered)


if __name__ == "__main__":
    unittest.main()
