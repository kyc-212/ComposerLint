import json
from pathlib import Path
import pytest
from composerlint.engine.parser import load_score
from composerlint.engine.voice_leading import execute
F=Path(__file__).parent/'fixtures/m3'
MANIFEST=json.loads((F/'expectations.json').read_text())
PAIRS=[c for c in MANIFEST if 'parallel_fifths' in c['expected']]
def counts(c):return [c.candidate_count,c.eligible_count,c.checked_count,c.skipped_count,c.flagged_count]
@pytest.mark.parametrize('case',PAIRS,ids=lambda c:c['name'])
def test_authored_pairs(case):
    expected=case['expected'];r=execute(load_score(F/case['source']))
    for rule in ('parallel_fifths','parallel_octaves'):
        c=r.ledgers[rule].coverage();want=expected['pair_counts'].copy();want[-1]=expected[rule]
        assert counts(c)==want
        assert len([d for d in r.diagnostics if d.rule_id==rule])==expected[rule]
    for diag in r.diagnostics:
        if diag.rule_id.startswith('parallel'):
            e=diag.evidence[0];assert e.same_direction
            assert e.before.end==e.after.start
            assert e.references.event_ids
@pytest.mark.parametrize('name,flags,diags',[('crossing_positive',2,1),('crossing_negative',0,0),('crossing_equal',0,0),('crossing_gap_recovery',2,2),('crossing_chordal',0,0)])
def test_order_cells(name,flags,diags):
    score=load_score(F/(name+'.musicxml'),{'voice_order':['p0001:v0001','p0002:v0001']})
    r=execute(score);c=r.ledgers['voice_crossing'].coverage()
    assert c.flagged_count==flags;assert sum(d.rule_id=='voice_crossing' for d in r.diagnostics)==diags
    if name=='crossing_positive':assert counts(c)==[2,2,2,0,2]
    no=execute(load_score(F/(name+'.musicxml'))).ledgers['voice_crossing'].coverage()
    assert no.checked_count==0 and no.skipped_count==no.candidate_count
@pytest.mark.parametrize('name,flags',[('range_positive',2),('range_bounds',0),('range_chord_tie',1)])
def test_range(name,flags):
    r=execute(load_score(F/(name+'.musicxml'),{'ranges':{'p0001':{'minimum':60,'maximum':84}}}))
    c=r.ledgers['range_issue'].coverage();assert c.flagged_count==flags
    assert sum(d.rule_id=='range_issue' for d in r.diagnostics)==flags
    for diag in r.diagnostics:
        if diag.rule_id=='range_issue':assert diag.evidence[0].config_owner_id=='p0001' and diag.evidence[0].amount==1

def test_unrelated_voice_does_not_split_pair():
    r=execute(load_score(F/'fifths_unrelated_voice.musicxml'))
    units=[u for u in r.ledgers['parallel_fifths'].units if json.loads(u.key)[1:3]==['p0001:v0001','p0002:v0001']]
    assert len(units)==1 and units[0].flagged

def test_range_voice_precedence_and_concert():
    score=load_score(Path(__file__).parent/'fixtures/transposing.musicxml')
    eid=next(e for e in score.parts[0].events if e.kind=='pitched')
    n=eid.sounding_pitch.chromatic_number
    options={'ranges':{'p0001':{'minimum':n+1,'maximum':n+1},'p0001:v0001':{'minimum':n,'maximum':n}}}
    r=execute(load_score(Path(__file__).parent/'fixtures/transposing.musicxml',options))
    assert not any(d.rule_id=='range_issue' and eid.id in d.scope.references.event_ids for d in r.diagnostics)

@pytest.mark.parametrize('quantize',[True,False])
@pytest.mark.parametrize('name,rule,flags',[('fifths_positive','parallel_fifths',1),('fifths_negative','parallel_fifths',0),('octaves_positive','parallel_octaves',1),('octaves_unison','parallel_octaves',0)])
def test_authored_midi(name,rule,flags,quantize):
    r=execute(load_score(F/(name+'.mid'),{'midi':{'quantize_post':quantize}}))
    assert r.ledgers[rule].coverage().flagged_count==flags
    assert all(d.confidence.level=='low' for d in r.diagnostics if d.rule_id==rule)
