from pathlib import Path
import pytest
from fractions import Fraction
from composerlint.service import analyze_advanced,analyze_score
from composerlint.review import digest
from v02_advanced_helpers import write_score

F=Path(__file__).parent/'fixtures';A=F/'v02_m3'


def analyze(path,**q):return analyze_advanced(path,query={'features':['melody','patterns'],**q})
def patterns(b,relation):return [o for o in b.snapshot.observations if o.fact.kind=='pattern' and o.fact.relation==relation]
def intervals(b):return [o for o in b.snapshot.observations if o.fact.kind=='interval' and o.fact.relation=='melodic']


def test_solo_steps_rest_and_domains():
    b=analyze(A/'solo.musicxml');iv=intervals(b)
    assert [o.fact.interval.semitones for o in iv[:3]]==[2,2,3]
    assert [o.fact.interval.motion for o in iv[:3]]==['step','step','leap']
    breaks=[o for o in b.snapshot.observations if o.fact.kind=='continuity_break']
    assert any('explicit_rest' in o.fact.reasons for o in breaks)
    assert not any(o.support[0].event_id==b.report.score.parts[0].events[6].id and o.fact.kind=='interval' for o in b.snapshot.observations)
    assert not any(o.span.start.fraction()<8 and o.span.end.fraction()>12 for o in iv)
    written=analyze(A/'solo.musicxml',analysis_options={'melodic_pitch_domain':'written'})
    assert [o.fact.interval.semitones for o in intervals(written)]==[o.fact.interval.semitones for o in iv]


def test_sequence_exact_transpose_and_mapping():
    b=analyze(A/'sequence.musicxml');seq=patterns(b,'sequence')
    assert any(len(o.fact.occurrences)==3 and [x.semitone_shift for x in o.fact.occurrences]==[0,2,4] and [x.diatonic_shift for x in o.fact.occurrences]==[0,1,2] for o in seq)
    for o in [*seq,*patterns(b,'transposed_recurrence')]:
        used=[]
        for occurrence in o.fact.occurrences:
            assert len(occurrence.event_ids)>=3
            assert not set(used)&set(occurrence.event_ids);used.extend(occurrence.event_ids)
    assert patterns(b,'transposed_recurrence')


def test_enharmonic_reattacks_and_ties():
    b=analyze(A/'enharmonic.musicxml');iv=intervals(b)
    assert len(iv)==1 and iv[0].fact.interval.semitones==0 and iv[0].fact.interval.diatonic_displacement==1 and iv[0].fact.interval.motion=='respelling'
    for name in ['inspection_tie_stop.musicxml','timing_grace.musicxml','invalid_ties.musicxml','unknown_pitch.musicxml','microtone.musicxml']:
        b=analyze(F/name)
        for o in intervals(b):
            assert o.fact.from_event_id!=o.fact.to_event_id
            e={e.id:e for p in b.report.score.parts for e in p.events}
            assert e[o.fact.from_event_id].end==e[o.fact.to_event_id].onset
            assert all('invalid_tie_chain' not in e[x].reason_codes for x in [o.fact.from_event_id,o.fact.to_event_id])


def comparison(path):
    base=digest(analyze_score(path).model_dump(mode='json'))
    r=lambda start,end:{'reference_part_id':'p0001','start_measure':start,'end_measure':end}
    return {'comparison':{'first':r(1,1),'second':r(2,2)},'expected_base_snapshot':base}


@pytest.mark.parametrize('second,relation',[
    ([('C4',1),('D4',1),('E4',2)],'exact_recurrence'),
    ([('D4',1),('E4',1),('F#4',2)],'transposed_recurrence'),
    ([('C4',2),('D4',2),('E4',4)],'scaled_rhythm'),
    ([('D4',1),('E4',1),('F4',2)],'diatonic_pattern'),
    ([('C4',1),('D4',1),('F4',2)],'pitch_variation'),
    ([('C4',1),('D4',2),('E4',2)],'duration_variation')])
def test_all_pattern_relations_independent_controls(tmp_path,second,relation):
    p=tmp_path/'pattern.musicxml';write_score(p,[{'bars':[[('C4',1),('D4',1),('E4',2)],second]}])
    b=analyze(p,**comparison(p))
    rows=patterns(b,relation);assert rows
    if relation in ('pitch_variation','duration_variation'):assert rows[0].fact.occurrences[1].differences
    if relation=='scaled_rhythm':assert rows[0].fact.occurrences[1].rhythm_scale.fraction()==2
    if relation=='diatonic_pattern':assert not patterns(b,'transposed_recurrence')


def test_near_miss_not_exact_and_overlapping_not_self_match(tmp_path):
    p=tmp_path/'near.musicxml';write_score(p,[{'bars':[[('C4',1),('D4',1),('E4',2)],[('D4',1),('E4',1),('F4',2)]]}])
    b=analyze(p);assert not patterns(b,'transposed_recurrence') and not patterns(b,'exact_recurrence')
    write_score(p,[{'bars':[[('C4',1),('C4',1),('C4',1),('C4',1)]]}])
    b=analyze(p);assert not any(o.fact.kind=='pattern' for o in b.snapshot.observations)
    write_score(p,[{'bars':[[('C4',1),('C4',1),('C4',1)],[('C4',1),('C4',1),('C4',1)]]}])
    b=analyze(p);assert all(o.fact.low_distinctiveness for o in patterns(b,'exact_recurrence'))


def test_coverage_enumeration_independent():
    b=analyze(A/'sequence.musicxml');c=next(c for c in b.snapshot.coverage if c.feature=='patterns')
    # One uninterrupted nine-attack run: lengths 3..8 => 7+6+5+4+3+2=27 windows.
    assert c.candidate==27 and c.checked==27 and c.skipped==0
    c=next(c for c in b.snapshot.coverage if c.feature=='melody');assert c.candidate==8 and c.checked==8


def test_weaker_diatonic_sequence_is_labeled_with_semitone_differences(tmp_path):
    p=tmp_path/'diatonic.musicxml';write_score(p,[{'bars':[[('C4',1),('D4',1),('E4',1)],[('D4',1),('E4',1),('F4',1)],[('E4',1),('F4',1),('G4',1)]]}],meter=(3,4))
    b=analyze(p);seq=patterns(b,'diatonic_sequence')
    assert seq and all(x.differences for x in seq[0].fact.occurrences)
    assert not patterns(b,'sequence')
