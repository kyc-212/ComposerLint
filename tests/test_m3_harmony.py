import json
from pathlib import Path
import pytest
from composerlint.engine.parser import load_score
from composerlint.engine.source_context import read_source_context
from composerlint.engine.key_analysis import select_key
from composerlint.engine.harmony import execute
F=Path(__file__).parent/'fixtures/m3';MANIFEST=json.loads((F/'expectations.json').read_text())
CASES=[c for c in MANIFEST if any(c['expected'].get(x) is not None for x in ('labels','figures','functions','transient'))]
def run(name,options=None):
    p=F/(name+'.musicxml');s=load_score(p,options);k=select_key(s,read_source_context(p,s));return execute(s,k)
@pytest.mark.parametrize('case',CASES,ids=lambda c:c['name'])
def test_authored_harmony(case):
    result,slices,runs,stages=run(case['name']);e=case['expected']
    for field,attr in [('labels','chord_label'),('figures','roman_numeral'),('functions','function')]:
        if e.get(field) is not None:assert [getattr(h,attr) for h in result.harmony]==e[field]
    if 'transient' in e:
        assert [i for i,h in enumerate(result.harmony) if 'transient_surface_sonority' in h.evidence[0].surface_flags]==e['transient']
        for i in e['transient']:assert result.harmony[i].confidence.level=='low' and result.harmony[i].function=='unknown'
    assert not result.diagnostics
    assert all(c.flagged_count==0 for c in stages)
    for h in result.harmony:
        f=h.evidence[0];assert f.references.event_ids==h.event_ids
        if f.inversion is not None:assert f.bass_pitch==h.sounding_pitches[h.event_ids.index(h.bass_event_id)]

def test_missing_key_stages():
    r,ss,runs,stages=run('roman_missing_key')
    assert [(s.candidate_count,s.eligible_count,s.checked_count,s.skipped_count) for s in stages]==[(2,2,2,0),(2,0,0,2)]
    assert r.ledgers['harmony_classification'].coverage().status=='skipped'
    assert all(h.unknown_reason=='missing_key' for h in r.harmony)

def test_transient_function_deferred():
    r,*_=run('chords_neighbor');c=r.ledgers['harmonic_function'].coverage()
    assert (c.candidate_count,c.eligible_count,c.checked_count,c.skipped_count)==(3,3,2,1)

def test_unknown_shapes_checked():
    r,_,_,stages=run('chords_incomplete_cluster')
    assert stages[0].checked_count==4 and stages[1].checked_count==0
    assert all(h.chord_label is None and h.unknown_reason for h in r.harmony)

# Exact counts authored from complete source collections and explicit mode, independent of output.
@pytest.mark.parametrize('name,c,chord,roman,fe,fk',[
    ('chords_triads',4,4,4,4,4),('chords_sevenths',6,6,6,6,6),
    ('chords_inversions_doublings',2,2,2,2,2),('chords_incomplete_cluster',4,4,0,0,0),
    ('chords_spelling',3,3,1,1,1),('chords_neighbor',3,3,3,3,2),('chords_passing',3,3,3,3,2),
    ('roman_major',4,4,4,4,4),('function_major',4,4,4,4,4),('roman_minor',5,5,5,5,5),
    ('roman_inversions',5,5,5,5,5),('roman_missing_key',2,2,0,0,0),('roman_chromatic',1,1,1,1,1),
    ('function_minor',4,4,4,4,4),('function_leading_tone',1,1,1,1,1),('function_six_four',3,3,3,3,3)])
def test_authored_stage_and_function_arithmetic(name,c,chord,roman,fe,fk):
    result,_,_,stages=run(name)
    assert [(s.candidate_count,s.eligible_count,s.checked_count,s.skipped_count,s.flagged_count) for s in stages]==[(c,chord,chord,c-chord,0),(c,roman,roman,c-roman,0)]
    cv=result.ledgers['harmony_classification'].coverage()
    assert (cv.candidate_count,cv.eligible_count,cv.checked_count,cv.skipped_count,cv.flagged_count)==(c,roman,roman,c-roman,0)
    cv=result.ledgers['harmonic_function'].coverage()
    assert (cv.candidate_count,cv.eligible_count,cv.checked_count,cv.skipped_count,cv.flagged_count)==(c,fe,fk,c-fk,0)
