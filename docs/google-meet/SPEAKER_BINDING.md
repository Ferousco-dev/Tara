# Who said what in Meet: the energy ↔ glow channel binder

**Status: implemented and tested offline (golden rules) and in a real Chrome against the local stand-in for Meet. Not verified on the real Google Meet page.** The tile selectors and speaking class names below are a guess carried over from a project that probed Meet's DOM; whether today's Meet still matches them is unknown. Read [What is not verified](#what-is-not-verified) before trusting a name.

This is the first half of M3 ("who said what", item 16) in [GOOGLE_MEET_INTEGRATION](GOOGLE_MEET_INTEGRATION.md): it turns Meet's single anonymous mix into one stream per person and attaches a name only when the evidence earns it.

## The problem
Meet has no structural link between audio and a participant. Three facts make this hard:

1. **Every remote participant is rendered as their own media element with their own live `MediaStream`.** The audio is already separated by person; what is missing is which person.
2. **The elements are an anonymous, rotating pool.** An element is created, reused and destroyed as tiles are laid out; its index is not a participant id, and it can change during the call.
3. **The speaking glow is global.** Meet lights the tile of whoever is speaking, and that signal is available to any script on the page — but it is not attached to any audio channel.

The naive fix is to name a chunk from whoever is lit at that instant. It leaks: when two people overlap, a channel that is loud at a glow onset can be the wrong person, and the bot then answers as if the wrong participant spoke. A wrong name is worse than no name, because identity-gated rules (answer only the candidate, obey only the organizer) act on it.

## The idea
A channel's **audio energy** and its speaker's **glow** are driven by the same audio: Meet lights tile X exactly when X is loud, and X's audio is what is loud on X's channel. So instead of matching a single onset, integrate the agreement over a short window:

> channel C belongs to tile X when C is loud **exactly while** X glows, and not otherwise.

Under constant overlap each channel still tracks its own speaker's glow, because the brief solo moments pull the right tile ahead. The scores separate over a few seconds; no clean turn start is needed.

Three rules keep it honest:
- **Decay.** Each agreement is worth `exp(-elapsed / tau_ms)`, so the window slides and a channel that stops being that person goes back to unknown instead of keeping a stale name forever.
- **Floor.** Below `min_score` decayed agreement, the channel is unknown. One or two loud frames are not evidence.
- **One tile, one channel.** A tile may be claimed by only one channel; the rest stay unknown rather than sharing a name.

## What Tara does differently from the reference
Tara's implementation is derived from Vexa's `GmeetChannelBinder` (see [Where this comes from](#where-this-comes-from)), with three deliberate, stricter rules. All three are about refusing to answer:

| Rule | Vexa | Tara | Why |
|---|---|---|---|
| Near-tie between two names | The highest score wins | **Margin**: the best name must lead the runner-up by `margin` (default 1.5×), otherwise unknown | Two names fitting a channel about equally is exactly the leak case; a small lead is not evidence |
| Two channels correlate with the same tile equally | Only a *strictly greater* other channel blocks the claim, so an exact tie lets the first caller win | An **equal** score also blocks it (`>=`), so an exact tie claims nobody | Deterministic, and it never breaks a tie by calling order |
| A name whose tile is no longer lit | Decayed agreement stays claimable, so a channel Meet hands to somebody else keeps the previous owner | **Recency**: a name is claimable only while its tile is lit, or was dark for at most `recent_ms` (default 800 ms) | A channel's old owner is not evidence about its new one; `recent_ms=None` restores the reference behaviour |

Everything else follows the reference: `tau_ms` 2500, `loud_threshold` 0.02 peak amplitude, `min_score` 2.5, the sticky self name, and purging agreements when the self name arrives late.

**The bot's own tile is pinned, never bound.** Meet names the local participant's tile (`data-self-name`), and that marker can render late or vanish. The binder therefore treats the self name as sticky: once known it is dropped from the glowing set, its agreements are purged (so a name that leaked before the marker appeared collapses back to unknown), and it can never win a remote channel. Tara's own audio is the microphone, never a remote element, so this is a backstop rather than the primary check.

## How it runs
```
Meet page                              bridge.js                     Python
---------                              ---------                     ------
each remote media element  ──tap──▶  channel N, 16 kHz PCM16   ──▶  adapter._on_channel_audio
  (own AudioContext per element,       + that frame's peak           │
   AudioWorklet, ScriptProcessor       + page clock                 ├─ binder.name_for_channel(N, ts, peak)
   fallback)                                                       │     → name or unknown
speaking tiles             ──scan──▶  glow onset/offset edges    ──▶  adapter._on_glow → binder.record_glow
local tile marker          ──once──▶  self name                 ──▶  adapter._on_self_name → binder.set_self_name
```

- **Capture.** One `AudioContext` per element, asked for 16 kHz (resampled if the context refuses), an `AudioWorklet` with a `ScriptProcessor` fallback. The worklet matters: a `ScriptProcessor` runs on the main thread and, on a busy Meet page, duplicates or drops buffers — the stutter that makes transcription garbled. A silent gain node keeps the graph pulled without producing output.
- **Mixed tap handover.** The existing mixed tap keeps running until the first per-channel frame arrives, then stops for good in the page (`CH.active`). One-way, so nobody is ever heard twice and the engine never sees the same speech on two streams. If the page never delivers a channel frame, the mixed path stays exactly as it was.
- **Naming.** A bound channel is stamped with the participant id the engine already knows for that display name (`a` for the first other person in an interview, `p-<name>` otherwise). An unbound channel is `ch-<n>`, which the engine treats as an unknown speaker. Audio is still heard either way; only the identity is withheld.
- **Reconnect.** A replacement page numbers its channels from one again, so the adapter resets the binder (keeping the sticky self name) on `RECONNECTING`.

### Files
| File | Job |
|---|---|
| `src/tara/meet/binding.py` | the binder: pure logic, no DOM and no audio, golden-tested |
| `src/tara/meet/bridge.js` | per-element capture, the glow scanner, the self name, `window.__taraBind()` diagnostics |
| `src/tara/meet/bridge.py` | the Python side: `__taraChannelAudio` / `__taraGlow` / `__taraSelf`, counters, `AudioBridge.bind()` |
| `src/tara/meet/adapter.py` | wires the callbacks, resolves participant ids, resets on reconnect, reports channels in the run log |
| `src/tara/meet/run.py` | `--per-participant` |
| `src/tara/voice/engine.py` | the engine now learns participant ids from join/leave/rename so a bound stream id resolves to a name |

## Running it
```bash
./dev.sh meet join <link> --per-participant            # normal meeting
./dev.sh meet join <link> --interview <code> --per-participant
```
It is **off by default** and needs the page audio route (`--audio page`, the default); with `--audio devices` the flag is refused, because per-element taps do not exist there. The mixed path is the default every other Meet behaviour was built and tested against, and per-participant mode changes how many `AudioContext`s the page creates; that is a real risk on a page as heavy as Meet, so it is opt-in until it has been run in a real call. The run log then carries `per_participant=True` and lines such as:

```
per-participant audio is active: each participant's own channel is heard separately
channel 1 is Alex Kim
channel 2 is Sam
```

If no participant audio element is found within the diagnostic interval, the run log says so and tells you to drop the flag. `AudioBridge.bind(page)` (used by the diagnostics and the test) returns the page's own view: channels connected, frames, who is lit, the self name, and the last few channel errors.

With `--record`, each channel is written to its own WAV (`...-room-ch1.wav`, `...-room-ch2.wav`, ...) instead of one room track: several people at once cannot share a single sequential timeline without producing a file nobody spoke. Tara's own track is unchanged.

## What was verified
**Offline, no browser** — `tests/meet/test_binding.py`, 13 golden cases:
- one lit tile is the only name a chunk may carry; zero or two lit tiles mean unknown;
- a name is attached only after enough agreeing evidence, and a quiet channel never earns one;
- two people lit together leave the channel unknown (the margin refuses to choose);
- the speaker with more solo moments wins the channel, and the loser goes back to unknown;
- a tile is claimed by one channel only, and an exact tie claims nobody;
- the bot's own tile is never bound, and learning the bot's name late collapses a leak that already happened;
- evidence decays, so a channel that stops being that person becomes unknown;
- a clock that steps backwards never amplifies old evidence;
- impossible inputs (negative or oversized channel, NaN/infinite energy, blank name, bad thresholds) are refused;
- a reconnect forgets channels but not who the bot is.

**Offline, through the adapter** — `tests/meet/test_meet_unit.py::test_per_participant_channels_are_named_only_when_the_glow_earns_it`:
- an unbound channel is emitted as `ch-1`, never with a guessed name;
- once the glow agrees with that channel's energy, the same frames carry the participant id the engine already knows (`a`);
- a channel that is loud while only the bot's own tile is lit stays `ch-2`;
- a reconnect forgets the channel evidence (back to `ch-1`) but keeps the bot's own name pinned.

**Real Chrome, local stand-in for Meet** — `tests/meet/test_meet_browser.py::test_per_participant_audio_names_each_speaker_from_the_glow`:
- two participants each get their own channel, and each is heard with energy on its own stream;
- the candidate is `a` and the second person is `p-sam`, both resolved by name from the glow;
- the mixed tap stops permanently once a channel carries the meeting (its frame counter stops moving);
- the bot's own preview element is appended to the page and does **not** become a channel (no echo of Tara);
- the page reports the self name `Tara` and at least one connected channel.

## How it is scored
The binder is measured against hand-authored ground truth by the attribution ladder in [ATTRIBUTION_EVAL](ATTRIBUTION_EVAL.md): 13 replay fixtures (solo, clean turns, overlap, rapid switches, glow lag, glow flicker, a duplicated stream, a reconnect, a muted channel, a late self name, a reused channel, a stuck glow, a glow that misses a voice) driven through the real `ChannelBinder` on a virtual clock, scored for completeness, leakage (a wrong name) and attribution precision. For every fixture with an honest hint — including a channel Meet reuses for a different person after the recency window — leakage is **zero**: a late, flickering or re-owned channel costs unknown frames, never a wrong name.

## Known limits (measured)
- **A channel reused by a different person is released only after the recency window, and not at all inside it.** Once the previous owner's tile has been dark for longer than `recent_ms` (800 ms) their decayed evidence can no longer be claimed, so the ladder's 1 s gap costs 4 unknown frames and zero wrong names. Inside the window it still leaks, because the old score remains the strongest signal: holding the same 2 s previous turn, a 800 ms handover gap carries the old name for 1 frame, 700 ms for 2, 400 ms for 5 and 0–200 ms for 7, and `BinderConfig(recent_ms=None)` restores the 5 wrong frames the fixture used to pin. Making sub-window handovers safe needs a saturating score cap or negative evidence for loud-without-that-name frames, which would change this module's contract; the ladder records the numbers instead of hiding them.
- **A glow that sticks to one tile mislabels the other speaker for the whole interval**, and a glow that misses one of two simultaneous voices lands on whichever channel the event loop feeds first. Both are pinned in the ladder; neither can be detected from inside the binder, because both break its premise rather than its arithmetic.
- **A late self name leaks for as long as it is late** (400 ms in the fixture) before the purge on arrival collapses it.

## What is not verified
- **The page audio route itself is not proven on real Meet.** The bot's real runs (2026-10-21) used the virtual-cable route (`--audio devices`); on the page route the standard WebRTC tap heard silence in four runs, and the media-element tap this mode depends on is untested there. Per-participant capture *is* a page-side tap, so it inherits that: until the page route is proven in a real call, `--per-participant` cannot be expected to hear anything there, and the run log will show channels connected with no useful level. The flag is refused with `--audio devices`, because per-element taps do not exist on that route.
- **That the real Meet page matches the scanner.** `TILE_SELECTORS` (`[data-participant-id]`), `span.notranslate`, `[data-self-name]` and the speaking class names (`Oaajhc`, `HX2H7`, `wEsLMd`, `OgVli`, plus generic fallbacks) are a guess. They come from a project that probed Meet's DOM and may be stale. If the scanner matches nothing, nobody is ever lit, no name is ever attached, and every channel stays `ch-<n>` — degraded to "unknown speaker", never wrong.
- **That real Meet gives one media element per participant with a live `MediaStream`.** If it mixes into one element, there is one channel and the behaviour is the old mixed path. If the elements exist but their tracks are silent or paused while the real audio arrives by another route, the bot goes deaf once the mixed tap hands over; the per-channel frame and level lines in the run log are how you would see that, and dropping `--per-participant` restores the proven path.
- **Cost and stability in a real call.** One `AudioContext` per participant is untested at Meet's scale and on a long call; Chrome caps concurrent contexts. Nothing here has been run in a real Google Meet, and no latency, CPU or audio-quality number has been measured.
- **Echo behaviour.** Per-channel taps feed the same echo gate and adaptive gain as the mixed tap, but a real call with speakers (not headphones) is unverified; see [GOOGLE_MEET_BOT](GOOGLE_MEET_BOT.md) on half-duplex.
- **Interviews.** The interview keeps its own candidate stream id for the first other person, so early frames may briefly carry a name-derived id before `PARTICIPANT_JOINED` lands. The binder never guesses, but the ordering of the two paths is only exercised in the stand-in.

## Where this comes from
The algorithm and the page-side technique are derived from **Vexa** (`Vexa-ai/vexa`), Apache-2.0, read 2026-10-23:

| Vexa file | What was taken |
|---|---|
| `core/meetings/modules/gmeet-capture/src/gmeet-channel-binder.ts` | the energy ↔ glow correlation, decay, floor, 1:1 tile claim, sticky self name |
| `core/meetings/modules/gmeet-capture/src/gmeet-capture.ts` | per-element 16 kHz capture with a silent sink |
| `core/meetings/modules/gmeet-capture/src/pcm-capture.ts` | why an `AudioWorklet` replaces `ScriptProcessorNode` |
| `core/meetings/modules/gmeet-capture/src/gmeet-speakers.ts` | tile name/marker reading, speaking classes, the self marker |

What was **not** taken: no Vexa code is imported or vendored, no Vexa service, Redis, queue, container or Python/TypeScript runtime is required, and no new dependency was added. The algorithm is re-expressed in Python (`binding.py`) and plain browser JavaScript (`bridge.js`) for Tara's adapter. Tara also does not use Vexa's transcript stream: it consumes none of Vexa's APIs, and Vexa exposes no raw audio, so per-speaker audio cannot come from there in any case.
