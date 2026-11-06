"""The attribution ladder: every fixture replayed through the real binder, scored against hand-authored truth.

The point is not that the binder is good at one meeting. It is that the ladder states, fixture by fixture, what a change to the
binder is allowed to do: an honest hint may cost UNKNOWN frames but must never produce a wrong name, including when Meet hands a
channel to a different person; a hint that lies, misses one of two voices, or names the bot late has its damage pinned so a change
is visible. See docs/google-meet/ATTRIBUTION_EVAL.md.
"""
import pytest

from tara.meet.binding import BinderConfig
from tara.meet.eval import Frame, GlowEdge, Scenario, Turn, replay, score
from tests.meet.attribution_fixtures import (
    ALL,
    BOUNDARY_OVERLAP,
    CHANNEL_REUSE,
    CLEAN_TURNS,
    DUPLICATE_STREAM,
    FLICKER_GLOW,
    GLOW_LAG,
    LATE_SELF_NAME,
    PARTIAL_GLOW,
    RAPID_SWITCHES,
    RECONNECT,
    SAFE,
    SILENT_CHANNEL,
    SOLO,
    STUCK_GLOW,
)


def answers_for(frames: tuple[Frame, ...], channel: int) -> list[str | None]:
    return [f.answer for f in frames if f.channel == channel]


# ---- the gate --------------------------------------------------------------------------------------------------------
def test_every_fixture_replays_byte_identically_twice():
    """A virtual clock, no I/O and no randomness: the same fixture must produce the same frames and the same score."""
    for scenario in ALL:
        first, second = replay(scenario), replay(scenario)
        assert first == second, scenario.name
        assert score(scenario, first) == score(scenario, second), scenario.name


def test_an_honest_hint_never_names_the_wrong_person():
    """The hard gate: a truthful, late or flickering glow may leave a channel unknown, never wrong — and that also has to hold for a
    channel Meet has since handed to somebody else, which the recency rule makes true beyond its 800 ms window."""
    for scenario in SAFE:
        result = score(scenario, replay(scenario))
        assert result.wrong_frames == 0, f"{result.line} {result.wrong_names}"


def test_every_speaking_frame_is_accounted_for():
    for scenario in ALL:
        result = score(scenario, replay(scenario))
        assert result.correct_frames + result.wrong_frames + result.unknown_frames == result.speaking_frames
        assert result.named_frames == result.correct_frames + result.wrong_frames


# ---- the ideal cases -------------------------------------------------------------------------------------------------
def test_solo_is_named_after_three_agreeing_frames():
    frames = replay(SOLO)
    assert answers_for(frames, 1)[:4] == [None, None, "Alex", "Alex"]       # 100 ms frames: 300 ms of evidence
    result = score(SOLO, frames)
    assert (result.completeness, result.correct_turn_rate, result.precision, result.leakage) == (1.0, 1.0, 1.0, 0.0)
    assert result.unknown_frames == 2                                       # only the two frames before the floor


def test_clean_turns_name_each_channel_its_own_speaker():
    frames = replay(CLEAN_TURNS)
    assert set(answers_for(frames, 1)) == {None, "Alex"}
    assert set(answers_for(frames, 2)) == {None, "Sam"}
    assert set(answers_for(frames, 3)) == {None, "Bea"}
    result = score(CLEAN_TURNS, frames)
    assert (result.completeness, result.correct_turn_rate) == (1.0, 1.0)
    assert (result.precision, result.leakage, result.repaints, result.dropouts) == (1.0, 0.0, 0, 0)


def test_overlap_costs_unknown_frames_and_never_a_cross_channel_name():
    result = score(BOUNDARY_OVERLAP, replay(BOUNDARY_OVERLAP))
    assert result.wrong_frames == 0
    assert result.correct_turns == result.turns == 3
    assert result.unknown_frames > 0                                        # the overlap itself is never claimed
    assert result.repaints == 0


def test_a_late_glow_costs_unknown_frames_not_wrong_ones():
    frames = replay(GLOW_LAG)
    first_named = next(f.ts_ms for f in frames if f.answer is not None)
    assert first_named == 500                                               # onset at 300 ms + three frames of evidence
    result = score(GLOW_LAG, frames)
    assert result.wrong_frames == 0 and result.precision == 1.0
    assert result.unknown_rate == pytest.approx(5 / 30)


def test_a_flickering_glow_does_not_withdraw_the_name():
    result = score(FLICKER_GLOW, replay(FLICKER_GLOW))
    assert result.wrong_frames == 0 and result.dropouts == 0
    assert result.correct_turn_rate == 1.0


def test_rapid_switches_leave_short_turns_unknown_rather_than_wrong():
    result = score(RAPID_SWITCHES, replay(RAPID_SWITCHES))
    assert result.wrong_frames == 0 and result.correct_turns == result.turns == 6
    assert result.unknown_rate > 0.2                                        # the price of 700 ms turns and a poll-late glow
    assert result.dropouts <= 2                                             # measured: a lagging glow briefly ties the channel


def test_a_duplicated_stream_is_named_once_and_the_copy_stays_unknown():
    frames = replay(DUPLICATE_STREAM)
    named = [channel for channel in (1, 2) if any(a is not None for a in answers_for(frames, channel))]
    assert named == [1]                                                     # one tile, one channel; the echo is not a second person
    assert answers_for(frames, 2) == [None] * len(answers_for(frames, 2))
    assert score(DUPLICATE_STREAM, frames).wrong_frames == 0


def test_a_reconnect_forgets_the_previous_owner():
    frames = replay(RECONNECT)
    after = [f for f in frames if f.ts_ms >= 2100]
    assert all(f.answer != "Alex" for f in after)                           # the reset clears the old evidence
    assert [f.answer for f in after[:3]] == [None, None, "Sam"]
    assert score(RECONNECT, frames).wrong_frames == 0


def test_a_muted_channel_is_never_named():
    frames = replay(SILENT_CHANNEL)
    assert answers_for(frames, 2) == [None] * len(answers_for(frames, 2))
    assert score(SILENT_CHANNEL, frames).silent_named >= 0                  # a channel nobody spoke on has no tail to measure


def test_a_reused_channel_is_released_once_the_previous_owner_goes_stale():
    """Meet hands the channel to somebody else after a 1 s gap. The recency rule (`BinderConfig.recent_ms`, 800 ms) stops a name being
    claimable once its tile has been dark for longer than that, so Sam's first frame is UNKNOWN rather than the previous owner, and
    Sam is named once his own evidence passes the floor. Refusing to guess costs four unknown frames; it never states a wrong name.
    This fixture used to be pinned as damage, which is exactly what the rule changed."""
    frames = replay(CHANNEL_REUSE)
    first_sam = next(f for f in frames if f.truth == "Sam")
    assert first_sam.answer is None                                         # unknown, never the previous owner
    assert all(f.answer != "Alex" for f in frames if f.truth == "Sam")      # his name never lands on Sam's turn
    result = score(CHANNEL_REUSE, frames)
    assert result.wrong_names == () and result.wrong_frames == 0
    assert (result.completeness, result.correct_turn_rate, result.precision) == (1.0, 1.0, 1.0)
    assert result.unknown_frames == 4                                       # Sam's first 400 ms, the price of not guessing
    assert (result.repaints, result.dropouts, result.silent_named) == (0, 0, 14)


def test_the_reused_channel_leak_returns_when_the_recency_rule_is_disabled():
    """The rule is load-bearing, not incidental. `recent_ms=None` restores the reference binder's behaviour, and the same fixture
    then reproduces what the ladder used to pin: 5 wrong frames where Sam is called Alex. Dropping or shortening the rule therefore
    shows up twice — as wrong names in the gate above, and as this exact count here."""
    result = score(CHANNEL_REUSE, replay(CHANNEL_REUSE, BinderConfig(recent_ms=None)))
    assert result.wrong_names == (("Sam", "Alex"),)
    assert result.wrong_frames == 5
    assert (result.correct_turns, result.turns) == (2, 2)                   # it does recover, and never names a third person


# ---- pinned damage: the premise breaks, so the failure is measured rather than hidden --------------------------------
def test_the_self_name_arriving_late_costs_only_the_frames_before_it():
    """The marker renders late, so the bot's own glow can bind a remote channel until it arrives; the binder then purges it."""
    result = score(LATE_SELF_NAME, replay(LATE_SELF_NAME))
    assert result.wrong_names == (("Alex", "Tara"),)
    assert result.wrong_frames == 4                                         # 400 ms, all before the marker at 600 ms
    after = [f for f in replay(LATE_SELF_NAME) if f.ts_ms >= 600]
    assert all(f.answer != "Tara" for f in after)
    assert next(f.answer for f in after if f.answer is not None) == "Alex"


def test_a_stuck_glow_mislabels_the_channel_it_sticks_to():
    """Known limitation: the binder's premise is that the glow tracks the channel's own speaker. A glow that never moves cannot
    be detected from inside, so the whole of Sam's turn is published as Alex. Pinned so a future guard is visible here."""
    result = score(STUCK_GLOW, replay(STUCK_GLOW))
    assert result.wrong_names == (("Sam", "Alex"),)
    assert result.wrong_frames == 20                                        # Sam's entire 2 s turn
    assert result.correct_turns == 1


def test_a_glow_that_misses_a_voice_can_mislabel_the_loudest_channel():
    """Known limitation: with one tile lit while two people talk, the first channel the event loop feeds claims that tile. The
    fixture puts the wrong person first, so the damage is the honest worst case, not the lucky one."""
    result = score(PARTIAL_GLOW, replay(PARTIAL_GLOW))
    assert result.wrong_names == (("Sam", "Alex"),)
    assert result.wrong_frames == 28
    assert result.leakage == 1.0


# ---- the scorer itself -----------------------------------------------------------------------------------------------
def test_a_name_on_silence_is_a_decay_tail_not_a_leak():
    scenario = Scenario("silence", turns=(Turn("Alex", 1, 0, 100),), glow=(GlowEdge("Alex", 0), GlowEdge("Alex", 100, True)),
                        duration_ms=200)
    frames = (Frame(0, 1, "Alex", "Alex"), Frame(100, 1, None, "Alex"), Frame(200, 1, None, None))
    result = score(scenario, frames)
    assert (result.wrong_frames, result.named_frames, result.silent_named) == (0, 1, 1)


def test_a_malformed_fixture_is_refused_rather_than_scored():
    with pytest.raises(ValueError):
        Turn("Alex", 1, 100, 100)                                           # empty turn
    with pytest.raises(ValueError):
        Turn("", 1, 0, 100)                                                 # no speaker
    with pytest.raises(ValueError):
        Turn("Alex", 64, 0, 100)                                            # outside the bounded table
    with pytest.raises(ValueError):
        Scenario("x", (Turn("Alex", 1, 0, 200), Turn("Sam", 1, 100, 300)), ())   # two people on one channel
    with pytest.raises(ValueError):
        Scenario("x", (), (), frame_ms=0)
    with pytest.raises(ValueError):
        Scenario("x", (), (), self_name_at_ms=100)                          # a late name with no name
    with pytest.raises(ValueError):
        GlowEdge("", 0)


def test_the_report_line_carries_counts_beside_the_percentages():
    result = score(SOLO, replay(SOLO))
    line = result.line
    for field in ("turns=1", "completeness=1/1", "correct_turns=1/1", "precision=1.000", "leakage=0.000",
                  "wrong=0", "unknown=2", "silent_named=", "repaints=0", "dropouts=0"):
        assert field in line
