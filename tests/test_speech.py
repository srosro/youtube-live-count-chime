import subprocess
import unittest
from unittest.mock import patch

from youtube_live_count_chime.models import POLL_INTERVAL_SECONDS
from youtube_live_count_chime.speech import SpeechError, speak_text


class SpeakTextTests(unittest.TestCase):
    def test_runs_say_bounded_and_checked_with_the_text_as_argv(self) -> None:
        # The spoken line is built from a channel handle, so it is not ours to
        # trust: it must reach say(1) as a single opaque argv element, never as
        # shell text or as a flag.
        hostile = "-o /tmp/pwned; touch /tmp/pwned"
        with patch("youtube_live_count_chime.speech.subprocess.run") as run:
            speak_text(hostile)

        command = run.call_args.args[0]
        self.assertEqual(command, ("/usr/bin/say", "--", hostile))
        self.assertNotIn("shell", run.call_args.kwargs)
        # Both halves of the bound matter, and only one of them is loud.
        # Unbounded, a wedged `say` mutes the whole fleet forever. Too tight,
        # every announcement is SIGKILLed mid-sentence — and that failure is
        # silent, since a truncated line still leaves the chime played and the
        # banner posted. A floor, like the sibling bound on the same lock.
        self.assertGreater(run.call_args.kwargs["timeout"], POLL_INTERVAL_SECONDS)
        self.assertIs(run.call_args.kwargs["check"], True)

    def test_failure_becomes_speech_error_without_the_spoken_text(self) -> None:
        # TimeoutExpired is the observable half of the bounded-wait contract: a
        # wedged say ends as a SpeechError the caller can warn on, never a
        # blocked caller holding the audio lock forever. The failure is warned
        # into the LaunchAgent's log file, so it must not carry the argv.
        argv = ["/usr/bin/say", "--", "2 new viewers on twitch chan"]
        failures: tuple[Exception, ...] = (
            subprocess.CalledProcessError(1, argv, stderr="boom"),
            FileNotFoundError("say not found"),
            subprocess.TimeoutExpired(argv, 10.0),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch(
                    "youtube_live_count_chime.speech.subprocess.run", side_effect=failure
                ):
                    with self.assertRaises(SpeechError) as ctx:
                        speak_text("2 new viewers on twitch chan")

                rendered = f"{ctx.exception}{ctx.exception.__cause__}"
                self.assertNotIn("new viewers", rendered)
                self.assertNotIn("twitch chan", rendered)
                self.assertIn(type(failure).__name__, rendered)


if __name__ == "__main__":
    unittest.main()
