# Attribution eval — scoring the channel binder against known turns

**Status: implemented and tested offline (`tests/meet/test_attribution_eval.py`, 20 tests). It says nothing about a real Google Meet
call:** the fixtures are authored signals, not captured ones, and the page route the per-participant tap depends on is still
unproven (see [SPEAKER_BINDING](SPEAKER_BINDING.md)).

## The problem this answers

[SPEAKER_BINDING](SPEAKER_BINDING.md) decides whether a channel may carry a name. A threshold change that makes the binder name
more often also makes it likelier to name the wrong person, and a change judged on the meeting that motivated it has been judged
against the one sample guaranteed to agree with it. The ladder answers the other question: **what does this build do to the
scenarios it was not written for?**

It is the naming counterpart of Vexa's transcript judge (`core/meetings/eval/src/judge.py`) and its counting oracle
(`count-channelswitch.test.ts`, `COUNTING-FIXTURES.md`; Apache-2.0, read 2026-10-23). Vexa replays a synthetic meeting where
speakers count 1, 2, 3, … so every missing number is a drop and every number under the wrong speaker is a mislabel. Tara has no
transcript on this path, so the oracle is the thing the binder actually decides on: **(channel, time) → speaker**, with the truth
authored by hand. A replay is pure metadata — no audio, no browser, no model, no network, no wall clock.

## The three metrics

Scored per frame (one energy reading per channel per 100 ms) against the turn covering that instant:

| Metric | Definition | Why it is the one that matters |
|---|---|---|
| **completeness** | true turns the binder named *at all* / true turns | a turn it never names is invisible to every rule that keys on a speaker, even though its audio was captured |
| **leakage** | named speaking frames whose name is **not** the person actually talking / named speaking frames | the hard gate: a wrong name is a false statement the engine may act on, and it is worse than no name |
| **attribution** | precision = correct / named, reported beside the unknown rate it was bought with | naming more by guessing is not progress |

Vexa's leakage checks whether a segment's *content* self-identifies a different speaker ("Boris here…"). Tara's is stronger and
simpler: it compares the label with the truth directly, so it does not depend on the speaker saying their own name. The cost is
that it needs authored ground truth, which is exactly what a fixture is.

Two diagnostics ride alongside, because a threshold change usually moves them:

- **repaints** — a channel's name changing *within* one continuous turn (the reader sees the same person renamed).
- **dropouts** — a name withdrawn mid-turn (the reader sees a named person become unknown).
- **silent_named** — frames where nobody was speaking and the channel still carried its last name. This is the decay tail, **not a
  leak**: the audio is silence and voice detection discards it. It is counted so the cost of a long `tau` stays visible.

## The replay

A fixture is a `Scenario`: `turns` (who really spoke, on which channel, over which interval), `glow` (the edges Meet's call screen
actually showed), optional `self_name` / `self_name_at_ms` (the local tile's name marker rendering late), `resets` (a replaced page)
and `channels` that exist but never speak (a muted participant).

`replay()` drives the **real** `ChannelBinder` on a virtual clock:

1. at instant `t`, a reset scheduled at or before `t` is applied (the page was replaced; channel numbers restart);
2. glow edges with `ts <= t` are applied, offsets before onsets at the same instant, so a handover at `t` is `{new speaker}`
   rather than a one-frame overlap of both;
3. the late self name is pinned when its instant arrives (the binder purges what leaked before it);
4. for each channel, energy is `LOUD` if a true turn covers `t` and `QUIET` otherwise, and `name_for_channel(channel, t, energy)`
   is recorded with the truth.

No `time.monotonic`, no randomness, no I/O: the same fixture always produces byte-identical frames, and the test asserts that.
A malformed fixture (two people on one transport channel, an empty turn, a channel outside the bounded table, a late name with no
name) raises instead of scoring nonsense.

## The ladder

`tests/meet/attribution_fixtures.py` is the committed fixture set, in ladder order: ideal capture first, then one realistic
degradation at a time, then the cases where the premise itself breaks. Numbers are from the run recorded in `CHANGELOG.md`.

| Fixture | What it models | Result |
|---|---|---|
| `solo` | one speaker, truthful glow | 1/1 turns, 28/30 frames named, precision 1.000, leakage 0 |
| `clean_turns` | three speakers, clean handovers | 3/3 turns, 54/60 named, precision 1.000, leakage 0, no repaints |
| `boundary_overlap` | two people audible across each handover | 3/3 turns, 55/70 named, precision 1.000, leakage 0; 15 unknown frames are the overlap itself |
| `rapid_switches` | 700 ms turns, glow seen 150 ms late (a DOM poll) | 6/6 turns correct, 31/42 named, leakage 0, 11 unknown, 2 dropouts |
| `glow_lag` | onset seen 300 ms after speech | 1/1 turns, 25/30 named, leakage 0; first name at 500 ms |
| `flicker_glow` | the glow class drops for 100 ms mid-turn | 28/30 named, leakage 0, **0 dropouts** (decay carries it) |
| `duplicate_stream` | one voice on two elements (an echo) | one channel named, the copy stays unknown, leakage 0 |
| `reconnect` | page replaced between two turns, channels renumbered | 2/2 turns, 35/39 named, leakage 0; two unknown frames after the reset |
| `silent_channel` | a participant joined, muted, never spoke | never named, no tail to measure |
| `channel_reuse` | Meet hands the same channel to a different person after a 1 s gap | 2/2 turns correct, 36/40 named, precision 1.000, leakage 0; the previous owner's name stops being claimable once their tile has been dark longer than `recent_ms`, at a cost of 4 unknown frames |
| `late_self_name` | the bot's own name marker renders 600 ms late | **4 wrong frames** (the bot's name on a remote channel), then purged and correct |
| `stuck_glow` | the glow never leaves one tile while another person talks | **20 wrong frames** — the whole of the other person's turn |
| `partial_glow` | one tile lit while two people talk, wrong channel fed first | **28 wrong frames**, leakage 1.000 — the lit name lands on the wrong channel |

`duplicate_stream` reports completeness 1/2 by design: it contains two true turns because one voice arrived on two channels, and
the copy staying unknown is the intended outcome (one tile, one channel), not a miss.

**The gate.** For every fixture with an honest hint — truthful, late or flickering, and including a channel Meet has since handed to
a different person — `wrong_frames == 0`. The three `PINNED` fixtures are excluded from that gate and their damage is asserted
exactly, so a change that helps or harms them is visible in the diff rather than hidden in an average. `channel_reuse` moved out of
`PINNED` into the gate when the binder's recency rule made it honest; the 5 wrong frames it used to pin are kept in the suite as a
config-level regression (`BinderConfig(recent_ms=None)`, the reference binder's behaviour).

## Findings (measured)

1. **A reused channel is released by the recency rule, and only after its window.** Evidence is a decayed *count*, so after 2 s of
   speech the previous person's score is still around 13 — numerically the strongest on that channel. What stops it being claimed is
   `binding.py`'s recency requirement: a name is claimable only while its tile is lit or was dark for at most `recent_ms` (800 ms).
   After the fixture's 1 s gap the previous owner is stale, so the channel goes unknown for the new person's first 400 ms and then
   names them: 4 unknown frames, 0 wrong. **The guarantee is bounded by that window, not absolute.** Holding the 2 s previous turn
   fixed and shrinking the gap, the same scenario still carries the old name for 1 frame at 800 ms, 2 at 700 ms, 5 at 400 ms and 7 at
   0–200 ms; at 900 ms and beyond it is silent instead of wrong. Disabling the rule (`BinderConfig(recent_ms=None)`) restores the 5
   wrong frames this fixture used to pin, which is why
   `test_the_reused_channel_leak_returns_when_the_recency_rule_is_disabled` keeps that comparison in the suite. A saturating score cap
   or counting loud-without-that-name frames as negative evidence would make sub-window handovers safe too; both change `binding.py`'s
   contract, so they remain recorded for the owner rather than applied inside an eval change.
2. **A glow that sticks to one tile cannot be detected from inside.** The premise is that the lit tile tracks the channel's own
   speaker; when it does not, the binder confidently publishes the lit name for the whole interval (`stuck_glow`).
3. **A glow that misses one of two simultaneous voices is order-dependent.** Whichever channel the event loop feeds first claims the
   lit name; `partial_glow` puts the wrong person first, so the pinned 100 % leakage is the honest worst case, not the lucky one.
4. **A late self name leaks for as long as it is late.** The purge on arrival is correct, but the frames before it carry the bot's
   name on a remote channel (400 ms in the fixture).
5. **Honest hints cost unknown frames, not wrong ones.** Overlap costs 15/70 frames, 700 ms turns 11/42, a 300 ms glow lag 5/30, a
   reused channel 4/40 once the previous owner is stale. A 100 ms flicker costs nothing.

## How to run it

```bash
.venv/bin/python -m pytest tests/meet/test_attribution_eval.py -q
```

To print the ladder (useful before and after a threshold change):

```bash
.venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, "tests/meet")
import attribution_fixtures as fx
from tara.meet.eval import replay, score
for scenario in fx.ALL:
    print(score(scenario, replay(scenario)).line)
EOF
```

## What is not verified

- **No captured signal tape.** Every fixture is authored from what Meet is documented and observed to do; none is a recording of a
  real call. A real tape would need per-channel energy and glow edges, which the bot does not yet log in a replayable form.
- **The frame model is a simplification.** 100 ms frames, binary loud/quiet energy and a `LOUD` constant stand in for real peak
  levels and a real chunk cadence. It exercises the binder's decision rules, not its response to noisy energy.
- **The tile selectors and glow detection are still unverified on real Meet** (see [SPEAKER_BINDING](SPEAKER_BINDING.md)). A
  scanner that matches nothing produces no glow, and the ladder's `no glow` behaviour is only covered indirectly: with an empty
  glow set nothing is ever named.
- **No latency, CPU or audio-quality claim.** The ladder is a correctness harness.

## Where this comes from

| Vexa file | What was taken |
|---|---|
| `core/meetings/eval/src/judge.py` | the three-metric shape (completeness / leakage / attribution), counts beside percentages, a grep-friendly machine line |
| `core/meetings/eval/COUNTING-FIXTURES.md` | the counting-oracle idea: a deterministic, self-verifying meeting that attributes loss to a stage |
| `core/meetings/modules/gmeet-pipeline/src/count-channelswitch.test.ts` | turn boundaries as the surface to exercise (gap onset, glow rotate, overlap) and the no-drop / no-dup / in-order oracle |
| `core/meetings/eval/namer-corpus/README.md` | why a naming change is proven against a corpus, and why `wrong` is never summed with `unnamed` |

No Vexa code is imported or vendored, and no Vexa service, runtime or dependency is required. The harness is Tara's own, written
against Tara's binder.

## Files

| File | Role |
|---|---|
| `src/tara/meet/eval.py` | `Turn`, `GlowEdge`, `Scenario`, `Frame`, `Score`, `replay()`, `score()` — pure logic, no I/O |
| `tests/meet/attribution_fixtures.py` | the committed ladder, including `glow_from_turns()` for the ideal hint |
| `tests/meet/test_attribution_eval.py` | the gate, the per-fixture assertions, the pinned damage and the scorer's own unit tests |
