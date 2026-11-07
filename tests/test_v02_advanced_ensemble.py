from pathlib import Path
import pytest
from composerlint.service import analyze_advanced,analyze_score
from composerlint.review import digest
from composerlint.errors import ComposerLintError
from v02_advanced_helpers import write_score
F=Path(__file__).parent/'fixtures';A=F/'v02_m3'


def facts(p,**query):return analyze_advanced(p,query={'features':['ensemble'],**query})
def motion(b):return [o for o in b.snapshot.observations if o.fact.kind=='ensemble' and o.fact.relation=='motion']


@pytest.mark.parametrize('upper,lower,expected',[
    (['G4','A4'],['C4','D4'],'parallel'),(['A4','G4'],['D4','E4'],'contrary'),
    (['G4','A4'],['E4','E4'],'oblique'),(['G4','G4'],['C4','C4'],'stationary')])
def test_pair_motion_independent(tmp_path,upper,lower,expected):
    p=tmp_path/'motion.musicxml';write_score(p,[{'bars':[[(x,2) for x in lower]]},{'bars':[[(x,2) for x in upper]]}])
    rows=motion(facts(p));assert len(rows)==1 and rows[0].fact.motion==expected
    assert rows[0].fact.attacks==[True,True]
    if expected=='parallel':assert rows[0].fact.interval_semitones==7


def test_holds_asynchronous_and_part_end(tmp_path):
    p=tmp_path/'held.musicxml';write_score(p,[{'bars':[[('E4',4)]]},{'bars':[[('G4',2),('A4',2)]]}])
    b=facts(p);rows=motion(b);assert len(rows)==1 and rows[0].fact.motion=='oblique' and rows[0].fact.attacks==[False,True]
    assert not rows[0].fact.synchronous_attacks
    write_score(p,[{'bars':[[('C4',2)]]},{'bars':[[('G4',2),('A4',2)]]}])
    b=facts(p);assert not motion(b)
    assert any('source_gap_or_part_end' in u.reasons for c in b.snapshot.coverage if c.feature=='ensemble' for u in c.units)


def test_orchestra_written_sounding_unison_and_complete_members():
    b=facts(A/'orchestra.musicxml');notes=[o.fact for o in b.snapshot.observations if o.fact.kind=='note' and o.fact.onset.fraction()==0]
    assert [n.sounding_pitch.chromatic_number for n in notes]==[72,72,60,72,76,55,48,36]
    assert [n.written_pitch.step+str(n.written_pitch.octave) for n in notes]==['C5','D5','G4','D5','E5','G3','C3','C3']
    assert all(n.sounding_status=='verified' for n in notes)
    first=next(o.fact for o in b.snapshot.observations if o.fact.kind=='sonority')
    assert first.complete and len(first.members)==8 and any(a.root=='C' and a.quality=='major' for a in first.alternatives)
    doubles=[o for o in b.snapshot.observations if o.fact.kind=='ensemble' and o.fact.relation=='doubling' and o.span.start.fraction()==0 and o.fact.doubling=='unison']
    pairs={tuple(o.fact.voice_ids) for o in doubles}
    assert {('p0001:v0001','p0002:v0001'),('p0001:v0001','p0004:v0001'),('p0002:v0001','p0004:v0001')}<=pairs
    assert any(o.fact.motion=='contrary' for o in motion(b))
    assert any(o.fact.kind=='ensemble' and o.fact.relation=='coordination' and o.fact.separate_attacks for o in b.snapshot.observations)


def test_declared_lower_melody_and_no_automatic_highest_role():
    p=A/'trio.musicxml';base=digest(analyze_score(p).model_dump(mode='json'))
    b=facts(p,expected_base_snapshot=base,analysis_options={'declared_roles':[{'part_id':'p0001','start_measure':1,'end_measure':1,'role':'melody'}]})
    declared=[o for o in b.snapshot.observations if o.fact.kind=='role' and o.fact.provenance=='user_declared']
    assert len(declared)==1 and declared[0].fact.voice_ids==['p0001:v0001'] and declared[0].span.end.fraction()==4
    assert not any(o.fact.kind=='role' and o.fact.role=='melody' and o.fact.voice_ids==['p0002:v0001'] for o in b.snapshot.observations)
    assert not any(o.fact.kind=='role' and o.fact.provenance=='user_declared' for o in facts(p).snapshot.observations)


def test_existing_polyphony_and_staff_identities():
    for name in ['piano_multivoice.musicxml','v02_m2/grandstaff.musicxml','v02_m2/voices.musicxml','multi_part.musicxml','v02_m2/duet.musicxml']:
        b=facts(F/name)
        old=analyze_score(F/name)
        assert b.report==old and {s.staff for o in b.snapshot.observations for s in o.support}=={v.source_staff for p in old.score.parts for v in p.voices}
    b=facts(F/'v02_m2/grandstaff.musicxml')
    assert {'1','2'}<={s.staff for o in b.snapshot.observations for s in o.support}


def test_metadata_changes_ties_staff_specific_and_name_only(tmp_path):
    p=tmp_path/'change.musicxml';write_score(p,[{'name':'Trumpet in B-flat','bars':[[('C5',4)],[('D5',4)]]}])
    b=facts(p);assert next(o.fact for o in b.snapshot.observations if o.fact.kind=='note').sounding_pitch.chromatic_number==72
    text=p.read_text().replace('<measure number="2">','<measure number="2"><attributes><transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>')
    p.write_text(text);ns=[o.fact for o in facts(p).snapshot.observations if o.fact.kind=='note'];assert [n.sounding_pitch.chromatic_number for n in ns]==[72,72]
    p.write_text(text.replace('<transpose>','<transpose number="1">'))
    with pytest.raises(ComposerLintError) as exc:facts(p)
    assert exc.value.code=='parse_failed'


def test_solo_pair_not_applicable():
    b=facts(A/'solo.musicxml');c=next(c for c in b.snapshot.coverage if c.feature=='ensemble')
    assert c.status=='not_applicable' and c.candidate==0 and 'solo_no_lane_pairs' in c.reasons


def test_all_minimum_role_candidates_have_positive_evidence(tmp_path):
    p=tmp_path/'roles.musicxml'
    write_score(p,[{'bars':[[('E4',1),('F4',1),('G4',2)],[('E4',1),('F4',1),('G4',2)]]},
                   {'bars':[[('G4',0.5),('A4',0.5),('G4',0.5),('A4',0.5),('G4',0.5),('A4',0.5),('G4',0.5),('A4',0.5)]]*2},
                   {'bars':[[('C3',8)]]}])
    base=digest(analyze_score(p).model_dump(mode='json'))
    b=facts(p,expected_base_snapshot=base,analysis_options={'declared_roles':[{'part_id':'p0001','start_measure':1,'end_measure':2,'role':'melody'}]})
    candidates=[o for o in b.snapshot.observations if o.fact.kind=='role' and o.fact.provenance=='inferred_candidate']
    assert {'motif_bearing','recurring_support','low_harmonic_support'}<={o.fact.role for o in candidates}
    assert all(o.fact.supporting_observation_ids and o.fact.counterevidence for o in candidates)


def test_transpose_change_through_tie_breaks_trust(tmp_path):
    p=tmp_path/'tie.musicxml'
    text=(F/'m3/fifths_ties.musicxml').read_text().replace('<measure number="2">','<measure number="2"><attributes><transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>',1)
    p.write_text(text);b=facts(p)
    invalid=[a for a in b.snapshot.transposition_audit if 'invalid_tie_chain' in a.reasons]
    assert len(invalid)==2 and all(a.status=='unknown' for a in invalid)
    assert not any(o.fact.kind=='interval' and o.fact.relation=='melodic' and any(s.event_id in {a.event_id for a in invalid} for s in o.support) for o in b.snapshot.observations)
