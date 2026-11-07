"""Focused existing-fact projection, ownership, counterevidence and bounded transport."""
from copy import deepcopy
from pathlib import Path

import pytest

from composerlint.engine.evaluation import compute
from composerlint.evaluation_models import EvaluationArguments,EvaluationFailure
from composerlint.evaluation_review import project_evaluation,EvaluationGraph
from composerlint.review import canonical,digest
from composerlint.service import analyze_score

F=Path(__file__).parent/'fixtures/v02_m4'


def query(path,split=False):
    first={'kind':'measure_range','reference_part_id':'p0001','start_measure':1,'end_measure':4}
    second={'kind':'measure_range','reference_part_id':'p0001','start_measure':5,'end_measure':8}
    if split:
        first['voice_ids']=['p0001:v0001'];second['voice_ids']=['p0002:v0001']
    return {'expected_base_snapshot':digest(analyze_score(path).model_dump(mode='json')),
        'comparison':{'first':first,'second':second}}


@pytest.fixture(scope='module')
def control():
    path=F/'classical_positive.musicxml'
    return path,compute(path,query=query(path))


def arguments(path,bundle,feature='E04',**changes):
    return EvaluationArguments(path=str(path),**bundle.snapshot.requested_query.model_dump(mode='json'),
        section='claim',expected_evaluation_snapshot=bundle.snapshot.evaluation_snapshot_id,
        record_ref=next(o.id for o in bundle.snapshot.indicators if o.feature==feature),
        page_size=50,max_bytes=32768,**changes)


def retrieve(bundle,args):
    rows=[];pages=[]
    for _ in range(12):
        page=project_evaluation(bundle,args);pages.append(page)
        assert page.start_index==len(rows) and page.returned_count==len(page.items)
        assert len(canonical(page.model_dump(mode='json')).encode())<=args.max_bytes
        assert not page.evidence_complete_in_this_response
        rows.extend(page.items)
        if page.section_exhausted:
            assert len(rows)==page.total_items and page.next_cursor is None
            return rows,pages
        args=args.model_copy(update={'cursor':page.next_cursor})
    pytest.fail('small control exceeded projection page cap')


@pytest.mark.parametrize('feature',['E04','E06'])
def test_projection_preserves_complete_claim_source_facts_and_legacy_outputs(control,feature):
    path,bundle=control;args=arguments(path,bundle,feature)
    before=(bundle.advanced.report.canonical_json(),bundle.advanced.canonical_json(),bundle.canonical_json())
    graph=EvaluationGraph(bundle)
    legacy=[r.model_dump(mode='json') for r in graph.closure(args.record_ref)]
    rows,pages=retrieve(bundle,args);data={r.record_ref:r.data for r in rows}
    manifest=data['claim:manifest'];claim=next(o for o in bundle.snapshot.indicators if o.id==args.record_ref)
    assert manifest['analysis_support_state']=='complete' and manifest['verification']['full_closure_delivered'] is False
    assert data[claim.id]['values']==claim.values.model_dump(mode='json')
    assert data[claim.id]['limitations']==claim.limitations and data[claim.id]['premise_ids']==claim.premise_ids
    source=[r for r in rows if r.record_ref.startswith('claim_source:')]
    assert len(source)==manifest['source_event_count']
    assert {r.data['event_ref'] for r in source}>={'event:'+s.event_id for s in claim.support}
    assert digest([r.model_dump(mode='json') for r in source])==manifest['source_facts_sha256']
    events={e.id:e for p in bundle.advanced.report.score.parts for e in p.events}
    segments={s.id:s for p in bundle.advanced.report.score.parts for s in p.source_segments}
    for row in source:
        value=row.data;event=events[value['event_ref'][6:]]
        assert (value['onset'],value['duration'],value['end'])==tuple(x.model_dump(mode='json') for x in (event.onset,event.duration,event.end))
        assert value['written_pitch']==event.model_dump(mode='json').get('written_pitch')
        assert value['sounding_pitch']==event.model_dump(mode='json').get('sounding_pitch')
        for location in value['source_segments']:
            segment=segments[location['segment_ref'][8:]]
            assert location['location']==segment.location.model_dump(mode='json')
            assert location['source_note_ordinal']==segment.timing_provenance.source_note_ordinal
            assert location['tie_role']==segment.tie_role
    assert [r.model_dump(mode='json') for r in EvaluationGraph(bundle).closure(args.record_ref)]==legacy
    data['claim:manifest']['source_assumptions'].append('caller mutation')
    assert before==(bundle.advanced.report.canonical_json(),bundle.advanced.canonical_json(),bundle.canonical_json())
    assert len(pages)<=3


def test_partial_comparison_preserves_missing_destination_and_withholds_absence():
    path=F/'classical_positive.musicxml';bundle=compute(path,query=query(path,split=True))
    rows,_=retrieve(bundle,arguments(path,bundle));data={r.record_ref:r.data for r in rows}
    assert data['claim:manifest']['analysis_support_state']=='partial'
    claim=next(o for o in bundle.snapshot.indicators if o.feature=='E04')
    assert data[claim.id]['values']==claim.values.model_dump(mode='json')
    assert not data[claim.id]['values']['complete'] and not data[claim.id]['values']['search_complete']
    assert not data['claim:coverage']['claim_unit']['checked']
    assert 'comparison_lane_incomplete_or_ambiguous' in data['claim:coverage']['claim_unit']['reasons']


def test_projection_preserves_counterevidence(control):
    path,bundle=control;args=arguments(path,bundle)
    rows,_=retrieve(bundle,args);value=next(r.data['values'] for r in rows if r.record_ref==args.record_ref)
    # Frozen C+ specification: exact opening return coexists with a changed continuation.
    assert value['later_rhythmic_contrast'] and value['exact_return_found']
    assert value==next(o.values.model_dump(mode='json') for o in bundle.snapshot.indicators if o.id==args.record_ref)
    activity_args=arguments(path,bundle,'E06')
    activity_rows,_=retrieve(bundle,activity_args)
    assert not next(r.data['values']['rhythm_equal'] for r in activity_rows if r.record_ref==activity_args.record_ref)


def test_projection_rejects_stale_tampered_unsupported_and_changed_cursor(control,monkeypatch):
    path,bundle=control;args=arguments(path,bundle)
    first=project_evaluation(bundle,args)
    assert first.next_cursor
    for change in ({'section':'evidence'},{'page_size':49},{'max_bytes':32000}):
        with pytest.raises(EvaluationFailure) as exc:project_evaluation(bundle,args.model_copy(update={'cursor':first.next_cursor,**change}))
        assert exc.value.code=='invalid_cursor'
    with pytest.raises(EvaluationFailure):project_evaluation(bundle,args.model_copy(update={'expected_evaluation_snapshot':'0'*64}))
    with pytest.raises(EvaluationFailure) as exc:project_evaluation(bundle,args.model_copy(update={'record_ref':bundle.snapshot.styles[0].id}))
    assert exc.value.code=='unsupported_claim'
    forged=deepcopy(bundle);forged.snapshot.indicators[0].limitations.append('forged')
    forged.snapshot.evaluation_snapshot_id=digest(forged.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    with pytest.raises(EvaluationFailure) as exc:project_evaluation(forged,args.model_copy(update={'expected_evaluation_snapshot':forged.snapshot.evaluation_snapshot_id}))
    assert exc.value.code=='unavailable_evidence'
    import composerlint.evaluation_projection as projection
    monkeypatch.setattr(projection,'MAX_SOURCE_EVENTS',1)
    with pytest.raises(EvaluationFailure) as exc:project_evaluation(bundle,args)
    assert exc.value.code=='analysis_limit_exceeded'


@pytest.mark.parametrize('kind',['advanced','evaluation'])
def test_25_second_budget_admits_work_after_20_and_keeps_remaining_worker_limit(monkeypatch,kind):
    from types import SimpleNamespace
    import composerlint.advanced_runtime as advanced
    import composerlint.evaluation_runtime as evaluation
    import composerlint.report_reuse as reuse
    runtime=advanced if kind=='advanced' else evaluation
    clock=[0.0];calls=[]
    def lookup(*a,**kw):clock[0]=24.0;return None
    def run(*a,**kw):
        calls.append(kw['timeout']);clock[0]=24.5
        return SimpleNamespace(returncode=0,stdout='{"ok":true,"result":{}}',stderr='')
    monkeypatch.setattr(runtime.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(reuse,'take',lookup);monkeypatch.setattr(runtime.subprocess,'run',run)
    assert runtime.request_worker({'operation':'page','arguments':{'path':'not-read.musicxml'}})=={}
    assert calls==[1.0] and runtime.LIMITS['deadline_seconds']==25
