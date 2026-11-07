import json
from pathlib import Path
import pytest
from composerlint.engine.parser import load_score
from composerlint.engine.source_context import read_source_context
from composerlint.engine.key_analysis import select_key
from composerlint.engine.harmony import execute
from composerlint.engine.harmonic_checks import execute_sevenths,execute_cadences
F=Path(__file__).parent/'fixtures/m3';MANIFEST=json.loads((F/'expectations.json').read_text())
def run(name,options=None):
    p=F/(name+'.musicxml');s=load_score(p,options);k=select_key(s,read_source_context(p,s));_,_,runs,_=execute(s,k)
    return s,k,runs
@pytest.mark.parametrize('c',[c for c in MANIFEST if 'seventh_flags' in c['expected']],ids=lambda c:c['name'])
def test_authored_sevenths(c):
    s,k,runs=run(c['name']);r=execute_sevenths(s,k,runs);coverage=r.ledgers['unresolved_seventh'].coverage()
    assert coverage.candidate_count==c['expected']['seventh_candidates']
    assert coverage.flagged_count==c['expected']['seventh_flags']
    for d in r.diagnostics:
        ev=d.evidence[0];assert ev.voice_id==s.parts[-1].voices[0].id or c['name']=='seventh_doubled'
        assert ev.outcome=='nonconventional' and ev.successor_event_id
        assert d.confidence.level in ('medium','low')
@pytest.mark.parametrize('c',[c for c in MANIFEST if 'cadences' in c['expected']],ids=lambda c:c['name'])
def test_authored_cadences(c):
    s,k,runs=run(c['name']);r=execute_cadences(s,k,runs);cov=r.ledgers['cadence_detection'].coverage()
    assert [x.kind for x in r.cadences]==c['expected']['cadences']
    if 'cadence_counts' in c['expected']:assert [cov.candidate_count,cov.eligible_count,cov.checked_count,cov.skipped_count,cov.flagged_count]==c['expected']['cadence_counts']
    for cad in r.cadences:
        ev=cad.evidence[0];assert ev.cues and ev.boundary==cad.scope.end
        assert cad.confidence.level in ('medium','low')

def test_unknown_key_does_not_find_cadence():
    s,k,runs=run('roman_missing_key');r=execute_cadences(s,k,runs)
    c=r.ledgers['cadence_detection'].coverage();assert (c.candidate_count,c.checked_count,c.skipped_count)==(2,0,2)
    assert not r.cadences

def test_delayed_seventh_never_rescued():
    s,k,runs=run('seventh_held_delayed');r=execute_sevenths(s,k,runs);u=r.ledgers['unresolved_seventh'].units[0]
    assert not u.checked and u.reason=='unrecognized_next_harmony'
    assert 'held_seventh' in u.evidence[0].reason_codes


@pytest.mark.parametrize('new_attack',[True,False],ids=['new-attack','held-notes-only'])
def test_seventh_successor_does_not_reuse_held_chord_tone(tmp_path,new_attack):
    from composerlint.service import analyze_score
    from composerlint.analysis_models import AnalysisReport,score_indexes
    from composerlint.musical_validation import validate_evidence
    # G3/B3/D4 continue across F4's release at beat 2. Only E4, when
    # present, is a new same-lane attack; the held G3 is never a successor.
    attacks='''<backup><duration>4</duration></backup>
      <note><pitch><step>E</step><octave>4</octave></pitch>
      <duration>4</duration><voice>1</voice></note>''' if new_attack else ''
    path=tmp_path/'held-chord-tones.musicxml'
    path.write_text('''<score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
      <part id="P1"><measure number="1"><attributes><divisions>4</divisions>
      <key><fifths>0</fifths><mode>major</mode></key>
      <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>G</step><octave>3</octave></pitch><duration>8</duration><voice>1</voice></note>
      <note><chord/><pitch><step>B</step><octave>3</octave></pitch><duration>8</duration><voice>1</voice></note>
      <note><chord/><pitch><step>D</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice></note>
      <note><chord/><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
      '''+attacks+'''</measure></part></score-partwise>''')
    source=path.read_bytes();report=analyze_score(path);part=report.score.parts[0]
    assert len(part.events)==(5 if new_attack else 4)
    assert [e.written_pitch.step for e in part.events]==list('GBDFE' if new_attack else 'GBDF')
    evidence=[ev for ex in report.coverage[9].exclusions for ev in ex.evidence if ev.kind=='seventh_resolution']
    ev=next(ev for ev in evidence if ev.obligation_event_id==part.events[3].id)
    assert ev.next_bounds.start.fraction()==1 and ev.next_bounds.end.fraction()==2
    assert ev.successor_event_id==(part.events[4].id if new_attack else None)
    assert ev.outcome=='unknown' and 'chordal_texture' in ev.reason_codes
    assert not any(d.rule_id=='unresolved_seventh' and d.category=='theory_issue' for d in report.diagnostics)
    harmony={h.id:h for h in report.harmony}
    assert all(any(e.id in harmony[hid].event_ids for hid in ev.next_harmony_ids) for e in part.events[:3])
    assert AnalysisReport.model_validate_json(report.canonical_json()).score==report.score
    # The independent validator must continue to reject the old held-note choice,
    # even when the result is explicitly unassessed rather than a musical warning.
    bad=ev.model_copy(update={'successor_event_id':part.events[0].id})
    with pytest.raises(ValueError,match='not first same-voice successor'):
        validate_evidence(bad,score_indexes(report.score),report.options,report.key_analysis,harmony)
    assert path.read_bytes()==source

def test_cue_bound_exact():
    s,k,runs=run('cadence_silence_exact');ev=execute_cadences(s,k,runs).cadences[0].evidence[0]
    assert ev.silence_duration.fraction()==1 and ev.silence.start.fraction()==2
    assert ev.references.region_ids and ev.references.event_ids
