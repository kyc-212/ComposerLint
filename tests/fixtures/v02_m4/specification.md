# Frozen authored engineering specifications — 2026-10-18

Written before M4 producer execution. All pitches are concert; quarters are exact units. These are original engineering music, not personal compositions or historical repertoire. XML construction is mechanical; expectations below are independent of producer output.

All bars 4/4 except B? (3/4). An entry `C4:2` means a half note; omitted duration is one quarter. Final barlines explicit unless a control specifies otherwise.

B+: A = [C4 D4 E4 G4] / [F4:2 E4 D4]; B = [rest:2 G3 A3] / [B3 D4 C4:2]. Opening C D E G maps to G A B D by -5 semitones, -3 steps, delay 2; overlap 2 quarters. At 4→5 A holds F while B moves; 5→6 both descend. Expected Baroque supported.
B-: both lanes = A above; exact synchronous unison, no delayed exchange. Baroque no bundle.
B?: C4 D4 E4 / D4 E4 F#4 / E4 F#4 G#4 (3/4). Three +2/+1 adjacent windows, no outside rhythmic corroboration. Partial Baroque only.
C+: A bars = [C5 D5 E5:2], [E5 D5 C5:2], [G5 E5 D5 C5], [D5:2 C5:2], then first two repeated, [G5:2 E5 D5], [D5:2 C5:2]. B repeats [C3 G3 E4 G3] each bar. Compare 1–4 and 5–8. Opening 3 attacks identical; nonopening rhythm changes at 3/7; repeated distinct support lane. Classical supported, no period or cadence label.
C-: second half A = [F#5 A5 B5:2], [B5 A5 F#5:2], [A5 F#5 B5 A5], [B5:2 A5:2]; B second half repeats [D3 A3 F4 A3]. Equal duration alone no bundle.
C?: C+ A alone. Related openings/contrast, no corroborating support/ending. Partial Classical.
R+: [C4 D4 E4 G4], [C#4 D4 E4 G4], [C5 D5 E5 G5], [C#5 D5 E5 G5]. C-major reference, compare 1–2 to 3–4. 2/16 outside attacks, 1/8 each span. Duration-weighted register 507/8→603/8, +12; rates 1→1. Romantic supported. Equal chromatic fractions cannot support increased chromaticism or two-axis tension.
R-: [C4 E4 G4 C5] four times. No chromatic material/register change/development; no Romantic bundle.
R?: C4 E4 G4 A4 simultaneous whole note; no reference/comparison. Both C6 and Am7/C; Romantic insufficient.
Mixed BC: C+ 8 bars followed by B+ two bars in the two lanes. Compare 1–4 and 5–10; both Classical and Baroque independent domains.
Mixed BR: B+ first two bars then R+ four bars; second lane rests throughout R+. Compare 1–4 and 5–6. Baroque lead and chromatic/development units disjoint; the unaltered R windows map +12; full first-span mean 127/2, second 603/8, difference 95/8.
Mixed CR: C+ eight bars plus a third lane: R+ low bars at 1/2, rests at 3/4, R+ high bars at 5/6, rests at 7/8. Compare 1–4 and 5–8. The C+ line retains related openings/contrast/support; the third lane supplies chromatic locations, mapped +12 development and register rise. Each bundle retains distinct domain premises.

Shared controls (four):
RETURN: 12-bar solo, motif [C4 D4 E4 G4] at 1/5/9. Other bars specified in generate.py. NO-RETURN is a temporary mutation of bar 9 to [F#4 A4 B4 F#5]. Explicit first-four-attack exact return at 9 supports versus checked absence; deliberate nonreturn reverses goal judgment. No historical bundle required.
ENDING: G3 B3 D4 half-note chord then C3 E3 G3 half-note chord in m1, complete rest m2. C-major user reference, m1 selection: unique root-position V→I, each 2≥one beat, actual following full rest; E08 supported. Temporary inversions/unknown/continuing/context filters/missing reference/minor do not establish E08. Broad closure remains unassessed on failed narrow check.
EXPRESSION: R+ but second-span C#5 in bar 4 becomes F#5 and bar3 D5 becomes D#5. Outside fractions 1/8→2/8, register increases; possible tension with these two dimensions. Original R+ does not meet two-dimensional build.
SOURCE: grand-staff/unknown/ties/labels and ensemble/MIDI cases reuse only existing authored sources with explicit temporary variants.

Rubric anchor matrix: each of six categories is checked on one supports_goal, one mixed (one support + one tension), and one goal_tension operational criterion set. The exact criterion/scope and expected outcomes are in tests/test_v02_evaluation_rubric.py before evaluation runs. Unknown essential evidence withholds the entire category; descriptive advisory evidence never compensates. Empty styles do not penalize other categories. Solo interaction N/A. Style-neutral coherence N/A. Unrequested category not_checked.

Arithmetic/source clarification before M4.1 closure: (60+62+64+67+61+62+64+67)/8 = 507/8; high span = 603/8. Earlier 63.5/75.5 expectation was mistaken. B+ also maps the fifth F4 half to C4 half, so canonical maximal entry windows have duration 6 and overlap 4; the specified four-attack submapping still has overlap 2. No producer/source change was made to force these expected answers.

Style-control correction before second style execution: original C- accompaniment C G E → D A F# was a genuine +2-semitone related opening, contradicting the negative specification. Retain original source under first-fixture-attempt; revised F natural breaks that relation independently. BR comparison must include bars 1–2 containing the imitation; comparing only 3–4 to 5–6 could not evidence a mixed claim. Corrected comparison includes 1–4 and 5–6 in one snapshot.
