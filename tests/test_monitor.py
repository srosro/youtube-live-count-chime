import io
import unittest
from collections.abc import AsyncIterator
from contextlib import redirect_stdout
from pathlib import Path

from youtube_live_count_chime.chatters import TwitchChatterNamer
from youtube_live_count_chime.models import Platform, StreamSnapshot, StreamTarget
from youtube_live_count_chime.monitor import ChimeConfig, monitor
from youtube_live_count_chime.notify import NotificationError
from youtube_live_count_chime.sounds import SoundPlaybackError
from youtube_live_count_chime.tokens import StoredToken


UP = Path("/System/Library/Sounds/Glass.aiff")
DOWN = Path("/System/Library/Sounds/Basso.aiff")
TOKEN = StoredToken("chan", "42", "access-placeholder", "refresh-placeholder")


class FakeSource:
    def __init__(self, target: StreamTarget, snaps: list[StreamSnapshot]) -> None:
        self.target = target
        self.name = target.key
        self._snaps = snaps

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        for snap in self._snaps:
            yield snap


class ExplodingSource:
    target = StreamTarget(Platform.TWITCH, "channel-x")
    name = "channel-x"

    async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
        raise RuntimeError("upstream failure")
        yield  # pragma: no cover - marks this an async generator


class FakeStore:
    """Every channel under test is authorized; the token itself is inert."""

    def get(self, login: str) -> StoredToken | None:
        return TOKEN


class FakeChatters:
    """Serve queued chat rosters, recording each read on a shared event log."""

    def __init__(
        self, rosters: list[frozenset[str]], events: list[str] | None = None
    ) -> None:
        self._rosters = rosters
        self.events = events if events is not None else []
        self.calls = 0

    def chatters(self, token: StoredToken) -> frozenset[str]:
        self.calls += 1
        self.events.append("arrivals")
        return self._rosters.pop(0) if self._rosters else frozenset()


def live(target: StreamTarget, viewers: int) -> StreamSnapshot:
    return StreamSnapshot(target, "s1", viewers)


def silent(title: str, body: str) -> None:
    """Swallow notifications in tests that assert only on chime behavior.

    monitor() defaults to the real osascript notifier, so a rise in a test
    that does not inject one posts an actual banner (and fails off macOS).
    """


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_chimes_up_and_down_but_not_on_baseline_or_no_change(self) -> None:
        target = StreamTarget(Platform.YOUTUBE, "a")
        snaps = [live(target, 5), live(target, 5), live(target, 9), live(target, 2)]
        played: list[Path] = []
        await monitor(
            [FakeSource(target, snaps)],
            ChimeConfig(UP, DOWN),
            play=played.append,
            notify=silent,
        )
        self.assertEqual(played, [UP, DOWN])  # 5->5 silent, 5->9 up, 9->2 down

    async def test_tracks_each_target_independently(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")
        b = StreamTarget(Platform.TWITCH, "b")
        played: list[Path] = []
        await monitor(
            [
                FakeSource(a, [live(a, 1), live(a, 2)]),
                FakeSource(b, [live(b, 100), live(b, 100)]),
            ],
            ChimeConfig(UP, DOWN),
            play=played.append,
            notify=silent,
        )
        self.assertEqual(played, [UP])  # a 1->2 up; b unchanged; baselines silent

    async def test_prints_the_transition_in_order(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "chan")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            await monitor(
                [FakeSource(a, [live(a, 5), live(a, 9)])],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
                notify=silent,
            )
        # Pins previous->current order and the up/down word (a swap would show
        # "9 -> 5" or "(down)").
        self.assertIn("youtube:chan: 5 -> 9 (up)", buffer.getvalue())

    async def test_reset_then_resume_chiming(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")
        played: list[Path] = []
        await monitor(
            [
                FakeSource(
                    a,
                    [
                        live(a, 5),  # baseline
                        StreamSnapshot.offline(a),  # offline clears the baseline
                        live(a, 9),  # fresh baseline after the reset (silent)
                        StreamSnapshot(a, "s2", 20),  # different stream re-baselines
                        StreamSnapshot(a, "s2", 25),  # same stream 20->25 -> chimes
                    ],
                )
            ],
            ChimeConfig(UP, DOWN),
            play=played.append,
            notify=silent,
        )
        # Every reset step is silent, and chiming resumes on the next real delta.
        self.assertEqual(played, [UP])

    async def test_playback_failure_warns_and_keeps_watching(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "a")

        def boom(path: Path) -> None:
            raise SoundPlaybackError("audio device gone")

        # A transient audio failure must not tear the watcher down: both deltas
        # (1->2 and 2->3) are attempted, each warned and skipped, and monitor
        # completes normally. Two warnings pins that it kept going after the first.
        with self.assertLogs("youtube_live_count_chime.monitor", "WARNING") as logs:
            await monitor(
                [FakeSource(a, [live(a, 1), live(a, 2), live(a, 3)])],
                ChimeConfig(UP, DOWN),
                play=boom,
                notify=lambda title, body: None,
            )
        self.assertEqual(len(logs.records), 2)

    async def test_unexpected_source_error_stops_the_watcher(self) -> None:
        healthy = StreamTarget(Platform.YOUTUBE, "a")
        with self.assertRaises(ExceptionGroup) as ctx:
            await monitor(
                [
                    ExplodingSource(),
                    FakeSource(healthy, [live(healthy, 1), live(healthy, 4)]),
                ],
                ChimeConfig(UP, DOWN),
                play=lambda path: None,
                notify=silent,
            )
        # Pin the full wrapper message and the preserved cause: this fails if
        # the channel-naming wrapper or its `from error` chaining is dropped.
        messages = [str(error) for error in ctx.exception.exceptions]
        self.assertIn("source channel-x failed", messages)
        causes = [type(error.__cause__) for error in ctx.exception.exceptions]
        self.assertIn(RuntimeError, causes)


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_chime_plays_before_naming_and_notifying(self) -> None:
        # The chime is the pre-existing signal and owes nothing to the
        # network. Naming costs a chat-roster round trip and the banner an
        # osascript call (bounded at 10s), so ordering either before the
        # chime delays every chime behind I/O.
        a = StreamTarget(Platform.TWITCH, "chan")
        events: list[str] = []
        client = FakeChatters(
            [frozenset(), frozenset({"joe_doe"})], events
        )

        await monitor(
            [FakeSource(a, [live(a, 1), live(a, 2)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: events.append("chime"),
            notify=lambda title, body: events.append("notify"),
            namer=TwitchChatterNamer(client, FakeStore()),
        )

        # The baseline poll samples the roster too (the diff window is one
        # poll); within the rising poll the chime still comes first.
        self.assertEqual(events, ["arrivals", "chime", "arrivals", "notify"])

    async def test_rise_notifies_with_named_arrival_and_own_current_count(self) -> None:
        a = StreamTarget(Platform.TWITCH, "watchmepivot")
        b = StreamTarget(Platform.YOUTUBE, "srosrosr")
        posted: list[tuple[str, str]] = []

        client = FakeChatters([frozenset(), frozenset({"joe_doe"})])

        # The other source (b) is present to exercise the multi-source shape
        # of Roster construction, but its own rendered count depends on
        # cross-task scheduling order, which monitor() does not guarantee —
        # that full-roster rendering (fixed order, unchanged/offline entries)
        # is exhaustively covered directly against Roster in test_digest.py.
        # Only the rising channel's own count is deterministic here: its
        # roster entry is always written before it notifies.
        await monitor(
            [
                FakeSource(a, [live(a, 1), live(a, 2)]),
                FakeSource(b, [live(b, 7)]),
            ],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
            namer=TwitchChatterNamer(client, FakeStore()),
        )

        self.assertEqual(len(posted), 1)
        title, body = posted[0]
        self.assertEqual(title, "joe_doe is now watching twitch watchmepivot")
        self.assertIn("twitch watchmepivot 2", body)
        # Once per live poll of the *Twitch* source, and never for the YouTube
        # one: sampling only on a rise would widen the diff window, and naming
        # the YouTube target from this roster would name the wrong channel.
        self.assertEqual(client.calls, 2)

    async def test_a_fall_posts_no_notification_though_the_roster_is_still_sampled(
        self,
    ) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        posted: list[tuple[str, str]] = []

        client = FakeChatters([])
        await monitor(
            [FakeSource(a, [live(a, 5), live(a, 2)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
            namer=TwitchChatterNamer(client, FakeStore()),
        )

        self.assertEqual(posted, [])
        # The roster is still sampled on the fall — that is what keeps the diff
        # window one poll wide — but a fall never names or notifies anyone.
        self.assertEqual(client.calls, 2)

    async def test_rise_without_a_namer_falls_back_to_a_bare_count(self) -> None:
        a = StreamTarget(Platform.YOUTUBE, "chan")
        posted: list[tuple[str, str]] = []
        await monitor(
            [FakeSource(a, [live(a, 1), live(a, 3)])],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
        )

        self.assertEqual(posted[0][0], "+2 watching youtube chan")

    async def test_a_youtube_rise_is_never_named_even_with_a_colliding_twitch_login(
        self,
    ) -> None:
        # The user monitors both twitch:watchmepivot and youtube:watchmepivot.
        # A rise on the YouTube source must never be attributed to a name
        # pulled from the Twitch chat roster, even though the bare handle
        # ("watchmepivot") the namer would look up collides between the two
        # platforms. Naming is Twitch-only by design.
        youtube_target = StreamTarget(Platform.YOUTUBE, "watchmepivot")
        client = FakeChatters([frozenset({"joe_doe"})])
        posted: list[tuple[str, str]] = []

        await monitor(
            [
                FakeSource(
                    youtube_target, [live(youtube_target, 1), live(youtube_target, 2)]
                )
            ],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
            namer=TwitchChatterNamer(client, FakeStore()),
        )

        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][0], "+1 watching youtube watchmepivot")
        # The Twitch chat roster must never even be queried for a YouTube rise.
        self.assertEqual(client.calls, 0)

    async def test_a_notification_failure_does_not_stop_the_watcher(self) -> None:
        a = StreamTarget(Platform.TWITCH, "chan")
        played: list[Path] = []

        def explode(title: str, body: str) -> None:
            raise NotificationError("banner refused")

        # The warning is the operator's only signal that banners are broken.
        with self.assertLogs("youtube_live_count_chime.monitor", "WARNING") as logs:
            await monitor(
                [FakeSource(a, [live(a, 1), live(a, 2), live(a, 5)])],
                ChimeConfig(UP, DOWN),
                play=played.append,
                notify=explode,
            )

        self.assertEqual(played, [UP, UP])  # both rises still chimed
        self.assertEqual(len(logs.records), 2)  # each failure warned

    async def test_an_unnameable_rise_still_notifies_with_a_bare_count(self) -> None:
        # The namer absorbs its own expected failures and answers with no
        # names; the rise must still chime and still post a banner.
        a = StreamTarget(Platform.TWITCH, "chan")
        posted: list[tuple[str, str]] = []
        played: list[Path] = []

        class _UnauthorizedStore:
            def get(self, login: str) -> StoredToken | None:
                return None

        await monitor(
            [FakeSource(a, [live(a, 1), live(a, 2), live(a, 5)])],
            ChimeConfig(UP, DOWN),
            play=played.append,
            notify=lambda title, body: posted.append((title, body)),
            namer=TwitchChatterNamer(FakeChatters([]), _UnauthorizedStore()),
        )

        self.assertEqual(
            [title for title, _ in posted],
            ["+1 watching twitch chan", "+3 watching twitch chan"],
        )
        self.assertEqual(played, [UP, UP])

    async def test_a_chatter_who_joined_on_a_flat_poll_is_not_named_on_the_next_rise(
        self,
    ) -> None:
        # The window the roster diff spans must be one poll, not "since the
        # last rise". Two rises with a flat poll between them: joe_doe joins
        # during the flat poll, so by the second rise he is already in chat and
        # nobody arrived — the rise is an embed or mobile viewer chat cannot
        # see. Sampling only on rises would diff the second rise against the
        # *first* rise's roster and announce "joe_doe is now watching".
        a = StreamTarget(Platform.TWITCH, "chan")

        class _Channel:
            """A source whose chat roster advances in lockstep with each poll.

            The roster is a function of poll number rather than of call
            number, so what the namer sees does not depend on how often the
            monitor chooses to ask — which is exactly what is under test.
            """

            target = a
            name = "twitch:chan"

            def __init__(self, script: list[tuple[int, frozenset[str]]]) -> None:
                self._script = script
                self._roster: frozenset[str] = frozenset()
                self.reads = 0

            async def snapshots(self) -> AsyncIterator[StreamSnapshot]:
                for viewers, roster in self._script:
                    self._roster = roster
                    yield live(a, viewers)

            def chatters(self, token: StoredToken) -> frozenset[str]:
                self.reads += 1
                return self._roster

        channel = _Channel(
            [
                (1, frozenset({"lurker"})),  # baseline: seeds
                (2, frozenset({"lurker", "amy"})),  # rise 1: amy arrived
                (2, frozenset({"lurker", "amy", "joe_doe"})),  # flat: joe_doe joins
                (3, frozenset({"lurker", "amy", "joe_doe"})),  # rise 2: nobody new
            ]
        )
        posted: list[tuple[str, str]] = []

        await monitor(
            [channel],
            ChimeConfig(UP, DOWN),
            play=lambda path: None,
            notify=lambda title, body: posted.append((title, body)),
            namer=TwitchChatterNamer(channel, FakeStore()),
        )

        self.assertEqual(
            [title for title, _ in posted],
            ["amy is now watching twitch chan", "+1 watching twitch chan"],
        )
        # One roster read per live poll. Pinning the count is what stops a
        # namer that is never called at all from passing this test.
        self.assertEqual(channel.reads, 4)


if __name__ == "__main__":
    unittest.main()
