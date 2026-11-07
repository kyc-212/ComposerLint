# Independently authored M2 sources

These synthetic scores are authored by this implementation, not extracted from a corpus or produced by the normalization/key engine. `generate.py` writes source bytes only; expected answers below were chosen from declared metadata, pitches and quarter-length sums before interpreting output. Original 17 M1 fixtures are untouched.

| Sources | Independent expected behavior |
| --- | --- |
| declared_major / declared_minor | Fifths 0, explicit major/minor at 0: C major / A minor; no estimator call |
| signature_only | Fifths 0 without mode: never implicit C major; one event is insufficient |
| conflicting_parts / key_change / trailing_key | C major versus G major across parts / time / at trailing offset 4: contradictory metadata; sparse unknown; override still wins |
| late_key | First key at source offset 4: no guessed opening key |
| unlocated_key | Declaration after backup: retain location IDs, null source time, no duplicate cursor parser |
| transposing_keys | Written D major with chromatic -2, diatonic -1 becomes concert C major, agreeing with other part |
| unknown_transposing_keys | Chromatic -2 without diatonic interpretation: unusable key; written D never substitutes for concert |
| conductor_key.mid | Conductor track 0, message 0, tick 0 explicitly C major; metadata has no fabricated part |
| conflicting_tracks.mid | Conductor C major versus G major on track 1: conflict in both quantization modes |
| estimate_major | Eight attacks C4/4 G4/2 E4/2 C5/4 F4/1 G4/2 D4/1 C5/4: 20 quarter lengths, weights C=12 D=1 E=2 F=1 G=4; C major expected |
| estimate_minor | Eight attacks A4/4 E4/2 C5/2 A4/4 D4/1 E4/2 B4/1 A4/4: 20 quarter lengths, weights A=12 B=1 C=2 D=1 E=4; A minor expected |
| sparse | C/1 G/1: insufficient event count, classes and activity |
| uniform_distribution | Twelve semitone classes each duration 1: constant distribution cannot justify a key; deterministic zero-score alternatives |
| weights_base / weights_ties_grace | C/4 G/2 E/2 versus tied C/2+2 plus grace D and ordinary zero B: same 3 logical positive events and histogram; annotations cannot multiply weight |
| inspection_multivoice | Part 1 pickup 0 [0,1], label 1 [1,5], duplicate label 1 zero extent [5,5]. C tie spans [0,5], grace E owned at pickup end 1, F at next start 1, D inspection chord member at 1 attached to tied C, B in final measure at 5. Part 2 pickup G [0,1] with A end point 1; second measure D [1,5], B opening 1, E endpoint 5. Cross-part endpoints obey owner membership, and tie/chord support remains complete. |

Variants in tests: repeated declaration, unsupported/invalid/nontraditional mode/signature, namespace-qualified XML, conductor minor, changed file at same path, insufficient and numerical thresholds, two-stage cursor movement. These byte transformations are authored explicitly in the tests; expected results come from their known source changes. No source fixture is regenerated during tests.
