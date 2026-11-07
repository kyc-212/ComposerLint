# Synthetic M1 fixtures and authored expectations

All fixtures were created locally using source XML strings and standard-library
MIDI byte construction. `generate.py` regenerates the original fixtures plus
`inspection_tie_stop.musicxml`; the two multipart audit fixtures are authored
committed sources and are not rewritten by that generator. No fixture is a Mozart composition
or downloaded corpus item. Expectations below were authored from pitches, source
units and ticks before running ComposerLint; tests assert concrete values and IDs.
Regeneration is optional. Existing score fixtures were not regenerated during the audit; their pre-audit hashes are unchanged.

| Fixture | Intended facts and boundary controls |
| --- | --- |
| `basic.musicxml` | Divisions 12, 4/4: C4 at 0, D4 at 1, rest at 2, E4 at 3; all durations 1. First explicit lane is voice 1/staff 1. Source beats 1–4. Rest blocks comparison from D4 to E4. |
| `basic.mid` | PPQN 480, explicit 4/4: C4 0–480, D4 480–960, E4 960–1440 ticks. Exact quarters with inferred notation spelling. |
| `piano_multivoice.musicxml` | One piano staff, explicit voices 1 and 2. Upper C5 0–3, D5 3–5 tied across measure 1/2, E5 5–8. Lower source chord C4/E4 0–1, rest 1–2, G3 2–4, A3 4–8. Upper logical events e1/e2/e3; D5 segments s2/s3 and locations beat 4/1. Lower chord event order C4 then E4; no pitch-derived voices. |
| `ugly_ambiguous.mid` | One track/channel, PPQN 480, authored `(on, off, note)` = `(13,493,60)`, `(611,1331,62)`, `(823,1553,64)`, `(1937,2417,65)`, `(2423,2903,67)`. Independent D4/E4 attacks overlap with unequal releases; one lane remains inferred. No approximate chord grouping. |
| `aligned_chord.mid` | Source G4 then C4 exactly 0–480 ticks, D4 480–960. One inferred simultaneity group preserves descending source order; later D4 is locally monophonic. |
| `transposing.musicxml` | Explicit B-flat transposition: written C4, concert B-flat3, chromatic 58. Duration 4. Source spelling and source transposition stay separate. |
| `timing_grace.musicxml` | Pickup 0: G4 0–1, meter 4/4, numeric beat 4. Measure 7a at time 1 changes to 3/4: D4/E4/F4 at 1, 4/3, 5/3, each duration 1/3, then rest 2–3, G4 3–4. Grace C4 at 1 has original duration 1/3 but performed duration 0; ordinary zero A4 at 2; grace B4 at 5/2 lies inside the rest. Repeats remain literal; total duration 4. |
| `unknown_pitch.musicxml` | Unpitched event 0–1 occupies/blocks without sounding evidence; D4 1–2; E4 2–3 overlaps separately attacked F4 5/2–7/2. Reliable overlap pitches remain harmony/range evidence and melodic blockers. |
| `microtone.musicxml` | C4 plus alteration 1/2 at 0–1 retained as unsupported inspection/provenance, no reliable sounding slot; D4 1–2 is locally eligible. The unsupported positive-duration event still occupies and blocks. |
| `unpitched.mid` | MIDI channel 10, notes 38 and 40 at 0–480 and 480–960: inspectable percussion note numbers, no fabricated concert pitch. |
| `invalid_ties.musicxml` | C4 start 0–1, rest 1–2, disconnected C4 stop 2–3, D4 3–4. No merge; invalid chain reasons block both C4 events. |
| `all_rest.musicxml` | One four-quarter explicit rest, no sounding spans or invented key. |
| `empty.musicxml` | Valid part-list/part/empty measure; zero explicit timed content, no invented events/voices/key. |
| `unknown_meter.musicxml` | Exact divisions-12 C4 0–1 without meter; exact offset and null beat/provenance unknown. |

## Additional committed audit fixtures

| Fixture | Independently authored source and expected behavior |
| --- | --- |
| `inspection_tie_stop.musicxml` | Divisions 12. C4 start 0–4; grace C4 stop and ordinary zero C4 stop at 4; positive C4 stop 4–5; D4 5–6; rest 6–8. Principal C4 is one e1 with s1/s4. Inspection e2/e3 retain stops and location m2 offset0; D4 is e4. Regions are 0–5 mono, 5–6 mono, 6–8 silent; only principal C4→D4 transition. |
| `multi_part.musicxml` | Header declares P1 then P2, source traverses P2 then P1. P2 implicit staff1: divisions6, 6/8, C4 0–1, forward gap1–2, D4 2–3; divisions10, 2/4, E4 3–7/2, F4 7/2–5. Source beats1,7/3,1,3/2. P1 explicit voice9: C4 start0–4, continue4–8, stop8–9; D4 9–10, rest10–12. Three C4 segments at labels8z,8,42a use one event. Total window12; shorter part does not sustain past5. |
| `multi_track.mid` | Format1/PPQN480. Track1 is conductor only: 6/8 at tick0, 2/4 at1440. Track2 `Channels`: raw channels5 then1 (public6 then2), note encounter order67,62,60,69,65; ticks (0,480),(0,480),(0,480),(480,960),(1440,1920). Channel6 program5 at0, program10 at240; attack-time programs5,unknown,5,10,unknown. G4/C4 form one source-order group; D4 in another channel remains mono. Track3 `Held`: public channel1 C5 0–2400, one event/segment across generated bars. Part windows4 and5; measure change at3. |

## Ugly MIDI expected local regions

Default `(4,3)` quantization yields source-event onsets `0, 5/4, 7/4, 4, 5`
and durations `1, 3/2, 3/2, 1, 1`. Ambiguity is exactly `[7/4, 11/4)`:
D4/E4 are both occupied, sounding, harmony/range eligible, and melodic blockers.
E4 alone regains local eligibility on `[11/4,13/4)`. A real gap `[13/4,4)` has
empty event lists and true blocking. F4/G4 form a later eligible transition;
no comparison bridges the overlap or the gap. The original source-track endpoint
2903/480 remains visible as a short silent tail after quantized time 6.

Opt-out keeps every onset/release as its tick fraction. Ambiguity is exactly
`[823/480,1331/480)`, followed by clean E4 and the true gap
`[1553/480,1937/480)`. The small gap `[2417/480,2423/480)` also blocks F4→G4.
The custom `[2]` grid collides D4/E4 at 3/2, duration 3/2 each: it remains an
ambiguous independent-attack region, not a source simultaneity chord. `[3]`
onsets/durations are documented in the API verification record.

## Additional generated test variants

Tests independently create: measure-label-only renumbering, unknown transposition,
a grace chord member with a trill ornament, a MIDI note held across generated
barlines, invalid public-schema mutations, corrupt XML/MIDI, SMPTE MIDI and
unreadable-path simulation. Temporary fixtures do not replace committed source
files. SHA-256 checks cover the relevant success/failure paths and every committed
successful fixture in both requested MIDI modes. Every committed fixture receives
two independent parses and full canonical-JSON comparisons. Five subprocess pairs
cover piano, default/opt-out ambiguous MIDI and both multipart audit sources.
Audit variants additionally cover inspection tie starts/continues/stops, a positive
disconnected chain beside inspection stops, malformed/unfinished/duplicate ties,
dotted grace, program changes, repeated-pitch identity ambiguity, pitch bend, zero
MIDI notes, namespaces/aliases, unsupported structures, source-window schema mutations and valid measure-end
inspection controls.
