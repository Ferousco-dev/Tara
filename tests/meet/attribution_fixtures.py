"""Committed replay fixtures for the attribution ladder (see `src/tara/meet/eval.py` and docs/google-meet/ATTRIBUTION_EVAL.md).

A fixture is a `Scenario`: who really spoke on which channel and when, plus the glow edges Meet's call screen actually showed.
The truth is authored by hand and never derived from the binder's output. Glow is derived from the truth by `glow_from_turns`
(the ideal capture) unless a fixture deliberately distorts it — that is the whole point of the ladder: the hint is allowed to be
late, to flicker, to lie or to be absent, and the binder is judged on what it does about it.

Scenario order is the ladder: ideal capture first, then one realistic degradation at a time, then the cases where the premise
itself breaks and the failure is pinned instead of hidden.
"""
from __future__ import annotations

from tara.meet.eval import GlowEdge, Scenario, Turn


def glow_from_turns(turns: tuple[Turn, ...], *, lag_ms: int = 0) -> tuple[GlowEdge, ...]:
    """The ideal hint: each speaker's tile is lit exactly while that speaker is talking, optionally `lag_ms` late.

    A speaker's turns are merged first, so back-to-back turns do not produce an offset and an onset at the same instant, and a
    `lag_ms` shift is clamped at the start of the meeting.
    """
    spans: dict[str, list[list[int]]] = {}
    for turn in turns:
        spans.setdefault(turn.speaker, []).append([turn.start_ms, turn.end_ms])
    edges: list[GlowEdge] = []
    for speaker, intervals in spans.items():
        intervals.sort()
        merged = [intervals[0][:]]
        for start, end in intervals[1:]:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            edges.append(GlowEdge(speaker, max(0, start + lag_ms), end=False))
            edges.append(GlowEdge(speaker, max(0, end + lag_ms), end=True))
    return tuple(edges)


def _turns(*turns: Turn) -> tuple[Turn, ...]:
    return turns


# ---- the ladder -------------------------------------------------------------------------------------------------------

SOLO = Scenario(
    name="solo",
    turns=_turns(Turn("Alex", 1, 0, 3000)),
    glow=glow_from_turns((Turn("Alex", 1, 0, 3000),)),
    duration_ms=3500,
)

CLEAN_TURNS = Scenario(
    name="clean_turns",
    turns=_turns(
        Turn("Alex", 1, 0, 2000),
        Turn("Sam", 2, 2000, 4000),
        Turn("Bea", 3, 4000, 6000),
    ),
    glow=glow_from_turns(_turns(
        Turn("Alex", 1, 0, 2000),
        Turn("Sam", 2, 2000, 4000),
        Turn("Bea", 3, 4000, 6000),
    )),
    duration_ms=6500,
)

BOUNDARY_OVERLAP = Scenario(
    name="boundary_overlap",
    turns=_turns(
        Turn("Alex", 1, 0, 2500),
        Turn("Sam", 2, 1800, 4500),
        Turn("Bea", 3, 4200, 6000),
    ),
    glow=glow_from_turns(_turns(
        Turn("Alex", 1, 0, 2500),
        Turn("Sam", 2, 1800, 4500),
        Turn("Bea", 3, 4200, 6000),
    )),
    duration_ms=6500,
)

_RAPID = _turns(
    Turn("Alex", 1, 0, 700),
    Turn("Sam", 2, 700, 1400),
    Turn("Alex", 1, 1400, 2100),
    Turn("Sam", 2, 2100, 2800),
    Turn("Alex", 1, 2800, 3500),
    Turn("Sam", 2, 3500, 4200),
)
RAPID_SWITCHES = Scenario(
    name="rapid_switches",
    turns=_RAPID,
    glow=glow_from_turns(_RAPID, lag_ms=150),       # the page polls the DOM: an onset is seen up to a poll late
    duration_ms=4600,
)

GLOW_LAG = Scenario(
    name="glow_lag",
    turns=_turns(Turn("Alex", 1, 0, 3000)),
    glow=glow_from_turns((Turn("Alex", 1, 0, 3000),), lag_ms=300),
    duration_ms=3500,
)

_FLICKER = (Turn("Alex", 1, 0, 3000),)
FLICKER_GLOW = Scenario(
    name="flicker_glow",
    turns=_FLICKER,
    glow=glow_from_turns(_FLICKER) + (GlowEdge("Alex", 1000, end=True), GlowEdge("Alex", 1100, end=False)),
    duration_ms=3500,
)

DUPLICATE_STREAM = Scenario(
    name="duplicate_stream",
    turns=_turns(Turn("Alex", 1, 0, 3000), Turn("Alex", 2, 0, 3000)),   # one voice arriving on two elements (an echo)
    glow=glow_from_turns((Turn("Alex", 1, 0, 3000),)),
    duration_ms=3500,
)

CHANNEL_REUSE = Scenario(
    name="channel_reuse",
    # Meet hands the same transport channel to somebody else. The 1 s gap is wider than the binder's recency window
    # (`binding.DEFAULT_RECENT_MS`, 800 ms), so the previous owner's evidence is stale by the time the new person speaks.
    turns=_turns(Turn("Alex", 1, 0, 2000), Turn("Sam", 1, 3000, 5000)),
    glow=glow_from_turns(_turns(Turn("Alex", 1, 0, 2000), Turn("Sam", 1, 3000, 5000))),
    duration_ms=5500,
)

LATE_SELF_NAME = Scenario(
    name="late_self_name",
    turns=_turns(Turn("Alex", 1, 0, 2000)),
    glow=(GlowEdge("Tara", 0, end=False), GlowEdge("Tara", 500, end=True),
          GlowEdge("Alex", 600, end=False), GlowEdge("Alex", 2000, end=True)),
    self_name="Tara",
    self_name_at_ms=600,                            # the local tile's name marker renders late
    duration_ms=2500,
)

RECONNECT = Scenario(
    name="reconnect",
    turns=_turns(Turn("Alex", 1, 0, 2000), Turn("Sam", 1, 2100, 4000)),
    glow=glow_from_turns(_turns(Turn("Alex", 1, 0, 2000), Turn("Sam", 1, 2100, 4000))),
    resets=(2050,),                                 # the page is replaced between the two turns; channels restart at 1
    duration_ms=4500,
)

SILENT_CHANNEL = Scenario(
    name="silent_channel",
    turns=_turns(Turn("Alex", 1, 0, 3000)),
    glow=glow_from_turns((Turn("Alex", 1, 0, 3000),)),
    channels=(2,),                                  # a participant joined, muted, and never made a sound
    duration_ms=3500,
)

STUCK_GLOW = Scenario(
    name="stuck_glow",
    turns=_turns(Turn("Alex", 1, 0, 2000), Turn("Sam", 1, 2000, 4000)),
    glow=(GlowEdge("Alex", 0, end=False), GlowEdge("Alex", 4000, end=True)),   # the glow never leaves Alex's tile
    duration_ms=4500,
)

PARTIAL_GLOW = Scenario(
    name="partial_glow",
    turns=_turns(Turn("Sam", 1, 0, 3000), Turn("Alex", 2, 0, 3000)),   # two voices, one tile lit; Sam's channel is fed first
    glow=(GlowEdge("Alex", 0, end=False), GlowEdge("Alex", 3000, end=True)),
    duration_ms=3500,
)

# The ladder as a whole. The zero-leak gate covers every fixture with an honest hint — including `channel_reuse`, where Meet hands a
# channel to somebody else: the binder's recency rule (`binding.BinderConfig.recent_ms`, 800 ms) stops a name being claimable once
# its tile has been dark for longer than that, so the fixture's 1 s gap costs UNKNOWN frames instead of a wrong name.
# `late_self_name`, `stuck_glow` and `partial_glow` stay outside the gate on purpose: each breaks the binder's premise — a self name
# that has not rendered, a glow that lies, a glow that misses one of two voices — so the tests pin their actual damage there instead
# of pretending it cannot happen.
SAFE = (
    SOLO,
    CLEAN_TURNS,
    BOUNDARY_OVERLAP,
    RAPID_SWITCHES,
    GLOW_LAG,
    FLICKER_GLOW,
    DUPLICATE_STREAM,
    RECONNECT,
    SILENT_CHANNEL,
    CHANNEL_REUSE,
)

PINNED = (LATE_SELF_NAME, STUCK_GLOW, PARTIAL_GLOW)
ALL = SAFE + PINNED
