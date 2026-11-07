# M2 authored notation controls

Original synthetic controls authored for ComposerLint M2, 2026-10-15, under the project MIT license. No repertoire or acquired score. Each has four literal measures; whole notes in 4/4, except the one-quarter pickup. Written C4 unless described below.

- duet: lower C4 D4 E4 F4, upper G4 A4 B4 C4; two parts, first three pairs are parallel fifths.
- grandstaff: treble C4 and bass G3, voice/staff 1 and 2.
- voices: C4 and G4 in two voices on staff 1.
- pickup: quarter C4, implicit printed 0, then full measures.
- ties: C4 whole start/stop in first two measures; one logical event, two source segments.
- duplicate: four printed 7 labels, distinct traversal identities.
- transpose: written C4, B-flat concert, chromatic -2 / diatonic -1.
- rests: whole rest each measure.
- repeat: backward repeat at fourth measure, no playback expansion.
- inherited: clef/key/time declared only at first measure; crop 2–3 must retain them.
- chord: C4/E4 whole-note chords.

Expectations are authored before renderer execution. Original fixtures are untouched.

2026-10-16: `multipage.musicxml` is a new 96-measure flute/piano grand-staff control, three staves, 4/4 whole notes, no authored system/page breaks. Its source facts are recorded before rendering in `multipage.expected.json`. It is an explicitly authored test score, not Kenneth’s composition or Morning Window.

Additional 2026-10-16 controls: `dense` (32 measures of sixteen sixteenths), `rhythm` (grace C4 and an exact D4/E4/F4 eighth-note triplet), `ending` (literal first/second endings), `empty_hidden` (empty measure and hidden note), `multirest` (explicit source collapse must retain four identities or fail closed). Each has a pre-render `.expected.json` with provenance, source hash and independent facts.
