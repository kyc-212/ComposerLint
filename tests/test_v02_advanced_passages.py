from pathlib import Path
from fractions import Fraction
import pytest
from composerlint.service import analyze_advanced,analyze_score
from composerlint.review import digest
from composerlint.advanced_models import AdvancedFailure
from composerlint.viewer_models import Selection
from v02_advanced_helpers import write_score
F=Path(__file__).parent/'fixtures';A=F/'v02_m3'


def load(path,**q):return analyze_advanced(path,query={'features':['passages'],**q})
def scope(first,last,**kw):return {'kind':'measure_range','reference_part_id':'p0001','start_measure':first,'end_measure':last,**kw}


def test_twelve_measures_distant_recurrence_and_rhythm():
    p=A/'long.musicxml';base=digest(analyze_score(p).model_dump(mode='json'))
    b=load(p,scope=scope(1,12),expected_base_snapshot=base)
    assert b.snapshot.resolved_scope.target_spans[0].end.fraction()==48
    ix={s.id:s for part in b.report.score.parts for s in part.source_segments}
    motifs=[o for o in b.snapshot.observations if o.fact.kind=='pattern' and o.fact.relation=='exact_recurrence']
    def measures(o):return {ix[next(e for p in b.report.score.parts for e in p.events if e.id==occ.event_ids[0]).source_segment_ids[0]].location.measure_index for occ in o.fact.occurrences}
    assert any({1,5,9}<=measures(o) for o in motifs)
    e=[e for p in b.report.score.parts for e in p.events if any(ix[s].location.measure_index==10 for s in e.source_segment_ids)]
    assert [x.duration.fraction() for x in e]==[2,1,1]
    assert all(o.fact.boundary_status=='candidate_only' for o in b.snapshot.observations if o.fact.kind=='passage')


def test_explicit_comparison_retains_order_and_gap_not_searched():
    p=A/'long.musicxml';base=digest(analyze_score(p).model_dump(mode='json'))
    b=load(p,comparison={'first':scope(1,4),'second':scope(9,12)},expected_base_snapshot=base)
    comparison=next(o for o in b.snapshot.observations if o.fact.kind=='passage' and o.fact.relation=='span_comparison')
    assert comparison.fact.supporting_observation_ids
    assert comparison.fact.boundary_status=='user_designated_spans' and 'gap_not_searched' in comparison.fact.cue
    primary=b.snapshot.resolved_scope.primary_event_ids
    ix={e.id:e for p in b.report.score.parts for e in p.events}
    assert all(ix[e].onset.fraction()<16 or ix[e].onset.fraction()>=32 for e in primary)
    assert not any(16<=ix[e].onset.fraction()<32 for o in b.snapshot.observations if o.fact.kind=='pattern' for occ in o.fact.occurrences for e in occ.event_ids)


def test_scope_context_factual_agreement_and_m2_protection():
    p=A/'long.musicxml';whole=load(p);base=whole.snapshot.base_snapshot_id
    b=load(p,scope=scope(5,10),expected_base_snapshot=base)
    s=b.snapshot.resolved_scope;assert s.context_spans[0].start.fraction()==12 and s.context_spans[0].end.fraction()==44
    assert s.target_spans[0].start.fraction()==16 and s.target_spans[0].end.fraction()==40
    assert s.context_event_ids
    targets=set(s.primary_event_ids)
    wholefacts={o.fact.event_id:o.fact.model_dump() for o in whole.snapshot.observations if o.fact.kind=='note'}
    assert all(o.fact.model_dump(exclude={'attack_in_target'})=={k:v for k,v in wholefacts[o.fact.event_id].items() if k!='attack_in_target'} for o in b.snapshot.observations if o.fact.kind=='note' and o.fact.event_id in targets)
    # The original M2 source selector still rejects twelve measures; no model was relaxed.
    from composerlint.viewer_models import Selection
    from pydantic import ValidationError
    fields=dict(source_sha256=b.snapshot.source_sha256,snapshot_id=base,options_sha256=b.snapshot.options_sha256,reference_part_id='p0001',part_ids=['p0001'],start_measure_index=1)
    assert Selection(**fields,end_measure_index=8).end_measure_index==8
    with pytest.raises(ValidationError,match='one to eight'):Selection(**fields,end_measure_index=12)


def test_unequal_measure_labels_align_by_time(tmp_path):
    p=tmp_path/'unequal.musicxml';write_score(p,[{'bars':[[('C4',1)],[('D4',3)]]},{'bars':[[('G3',2)],[('A3',2)]]}])
    base=digest(analyze_score(p).model_dump(mode='json'))
    b=load(p,scope=scope(2,2),expected_base_snapshot=base)
    e={e.id:e for part in b.report.score.parts for e in part.events}
    assert b.snapshot.resolved_scope.target_spans[0].start.fraction()==1
    assert {e[x].voice_id for x in b.snapshot.resolved_scope.primary_event_ids}=={'p0001:v0001','p0002:v0001'}
    assert len(b.snapshot.resolved_scope.primary_event_ids)==3
    with pytest.raises(AdvancedFailure):load(p,scope=scope(1,3),expected_base_snapshot=base)


def test_comparison_part_filters_do_not_leak_between_spans(tmp_path):
    p=tmp_path/'filters.musicxml';write_score(p,[{'bars':[[('C4',4)],[('D4',4)]]},{'bars':[[('G3',4)],[('A3',4)]]}])
    base=digest(analyze_score(p).model_dump(mode='json'))
    b=load(p,comparison={'first':scope(1,1,part_ids=['p0001']),'second':scope(2,2,part_ids=['p0002'])},expected_base_snapshot=base)
    e={e.id:e for part in b.report.score.parts for e in part.events}
    assert [(e[x].voice_id,e[x].onset.fraction()) for x in b.snapshot.resolved_scope.primary_event_ids]==[('p0001:v0001',0),('p0002:v0001',4)]


def test_filtered_reference_part_keeps_scope_anchor_in_evidence(tmp_path):
    p=tmp_path/'anchor.musicxml';write_score(p,[{'bars':[[('C4',4)],[('D4',4)]]},{'bars':[[('G3',4)],[('A3',4)]]}])
    base=digest(analyze_score(p).model_dump(mode='json'))
    b=load(p,scope=scope(2,2,part_ids=['p0002']),expected_base_snapshot=base)
    from composerlint.advanced_review import AdvancedGraph
    row=next(o for o in b.snapshot.observations if o.fact.kind=='note' and o.support[0].role=='primary')
    refs={r.record_ref for r in AdvancedGraph(b).evidence(row.id)}
    assert {'part:p0001','measure:p0001:m0002','measure:p0001:m0001'}<=refs
