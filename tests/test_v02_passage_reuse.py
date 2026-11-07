"""Owned passage lifecycle, truthful shared proof delivery and legacy controls."""
from copy import deepcopy
from pathlib import Path
import base64
import json

import pytest
from pydantic import ValidationError

from composerlint import passage
from composerlint.advanced_models import PassageArguments, SharedPassagePage, AdvancedFailure
from composerlint.passage_session import Store, Worker
from composerlint.review import canonical,digest
from passage_trial_observer import audited_events,SharedProofObserver
from v02_advanced_helpers import write_score

F=Path(__file__).parent/'fixtures/v02_m3/trio.musicxml'


def source(store,path=F,shared=False):
    return store.run(PassageArguments(path=str(path),execution_policy='source_bound_passage_v1',
        delivery_policy='shared_evidence_v2' if shared else 'record_pages_v1'))


def overview(store,s,path=F,shared=False,**kw):
    args=dict(path=str(path),execution_policy='source_bound_passage_v1',section='overview',
        expected_source_snapshot=s.source_snapshot_id,scope=dict(reference_part_id='p0001',start_measure=1,end_measure=2),
        delivery_policy='shared_evidence_v2' if shared else 'record_pages_v1')|kw
    return args,store.run(PassageArguments(**args))


def test_source_and_passage_prepare_validate_once(monkeypatch):
    store=Store();s=source(store);args,p=overview(store,s)
    first=p.model_dump(mode='json');calls=[]
    def forbidden(*a,**k):calls.append(1);raise AssertionError('repeated musical work')
    for name in ('prepare','compute','validate'):monkeypatch.setattr(passage,name,forbidden)
    again=store.run(PassageArguments(**args))
    assert not calls
    assert s.timings['source_loads']==1 and p.timings['source_loads']==0
    assert p.timings['scoped_predicate_passes']==2 and p.timings['validation_seconds']>0
    assert again.timings['passage_reuse_hits']==1 and again.timings['validation_seconds']==0
    assert again.model_dump(exclude={'timings'})==p.model_dump(exclude={'timings'})


def test_legacy_record_pages_match_fresh_validated_result():
    store=Store();s=source(store);args,p=overview(store,s)
    fresh=passage.run(PassageArguments(**args))
    assert p.model_dump(exclude={'timings'})==fresh.model_dump(exclude={'timings'})


@pytest.mark.parametrize('change',['bytes','options','policy','path'])
def test_source_freshness_ownership_and_mismatches(tmp_path,monkeypatch,change):
    path=tmp_path/'source.musicxml';path.write_bytes(F.read_bytes())
    store=Store();s=source(store,path);args,p=overview(store,s,path)
    if change=='bytes':path.write_bytes(path.read_bytes()+b'\n')
    elif change=='options':args['options']={'target_style':'classical'}
    elif change=='policy':monkeypatch.setattr('composerlint.report_reuse.code_identity',lambda:'f'*64)
    else:
        other=tmp_path/'other.musicxml';other.write_bytes(path.read_bytes());args['path']=str(other)
    with pytest.raises(AdvancedFailure) as exc:store.run(PassageArguments(**args))
    assert exc.value.code=='stale_snapshot'


def test_expiry_does_not_extend_on_hits():
    clock=[0.0];store=Store(clock=lambda:clock[0],ttl=10)
    s=source(store);args,p=overview(store,s)
    clock[0]=9;store.run(PassageArguments(**args))
    clock[0]=10
    with pytest.raises(AdvancedFailure,match='prepare'):store.run(PassageArguments(**args))
    assert not store.sources


@pytest.mark.parametrize('change',['scope','features','analysis_options','snapshot'])
def test_passage_bound_to_exact_query(change):
    store=Store();s=source(store);args,p=overview(store,s)
    args['expected_passage_snapshot']=p.passage_snapshot_id
    if change=='scope':args['scope']['end_measure']=1
    elif change=='features':args['features']=['notes']
    elif change=='analysis_options':args['analysis_options']={'neighbor_measures':0}
    else:args['expected_passage_snapshot']='f'*64
    with pytest.raises(AdvancedFailure) as exc:store.run(PassageArguments(**args))
    assert exc.value.code=='stale_advanced_snapshot'


@pytest.mark.parametrize('domain',['source','snapshot','graph','proof'])
def test_mutation_and_rehashed_forgery_are_rejected(domain):
    store=Store();s=source(store,shared=True);args,p=overview(store,s,shared=True)
    owned=next(iter(store.sources.values()));entry=next(iter(owned.passages.values()))
    refs=[x['record_ref'] for x in p.finding_previews][:2]
    proof={**args,'section':'evidence','expected_passage_snapshot':p.passage_snapshot_id,'record_refs':refs}
    store.run(PassageArguments(**proof))
    if domain=='source':owned.source.score.parts[0].source_label='Forged source'
    elif domain=='snapshot':
        entry.bundle.snapshot.observations[0].limitations.append('forged')
        entry.bundle.snapshot.passage_snapshot_id=digest(entry.bundle.snapshot.model_dump(mode='json',exclude={'passage_snapshot_id'}))
    elif domain=='graph':entry.graph.raw['metadata:analysis']['global_musical_report_constructed']=True
    else:
        k=next(iter(entry.proofs));entry.proofs[k]=tuple(['{"record_ref":"forged","data":{}}'])
    with pytest.raises(AdvancedFailure) as exc:store.run(PassageArguments(**proof))
    assert exc.value.code=='unavailable_evidence'


def test_shared_union_is_exact_lossless_complete_and_deduplicated():
    store=Store();s=source(store,shared=True);args,p=overview(store,s,shared=True)
    assert p.finding_previews and not p.evidence_complete_in_this_response
    assert all(x['internally_verified'] and not x['complete_proof_delivered'] for x in p.finding_previews)
    roots=[x['record_ref'] for x in p.finding_previews][:2]
    entry=next(iter(next(iter(store.sources.values())).passages.values()))
    expected={};individual=[]
    for ref in roots:
        rows=entry.graph.evidence(ref);individual.append(len(rows))
        for row in rows:expected.setdefault(row.record_ref,row.model_dump(mode='json'))
    # The graph above was materialized by this test only; restore its private seal.
    entry.seal=store.sign(store.passage_state(entry))
    request={**args,'section':'evidence','record_refs':roots,'expected_passage_snapshot':p.passage_snapshot_id,'max_bytes':8192}
    observer=SharedProofObserver();pages=[]
    while True:
        page=store.run(PassageArguments(**request));pages.append(page)
        assert len(canonical(page.model_dump(mode='json')).encode())<=8192
        assert len(page.items)<=50 and page.timings['source_loads']==0 and page.timings['scoped_predicate_passes']==0
        observer.add(page)
        if not page.next_cursor:break
        request['cursor']=page.next_cursor
    assert observer.complete and observer.rows==list(expected.values())
    assert len(observer.rows)<sum(individual)
    assert not pages[-1].evidence_complete_in_this_response if len(pages)>1 else pages[0].evidence_complete_in_this_response
    records={r['record_ref']:r['data'] for r in observer.rows}
    assert audited_events(records)
    for row in audited_events(records):assert row['part']['id']==row['voice']['part_id']
    bad=deepcopy(pages[-1]);bad.items[0].data['encoded_chunk']='A'+bad.items[0].data['encoded_chunk'][1:]
    check=SharedProofObserver()
    for page in pages[:-1]:check.add(page)
    with pytest.raises(ValueError,match='forged'):check.add(bad)


def test_shared_cursor_cannot_be_forged_or_moved_to_another_proof():
    store=Store();s=source(store,shared=True);args,p=overview(store,s,shared=True)
    roots=[x['record_ref'] for x in p.finding_previews][:2]
    request={**args,'section':'evidence','record_refs':roots,'expected_passage_snapshot':p.passage_snapshot_id,'page_size':1}
    first=store.run(PassageArguments(**request));assert first.next_cursor
    token=json.loads(base64.urlsafe_b64decode(first.next_cursor));token['offset']+=1
    forged=base64.urlsafe_b64encode(canonical(token).encode()).decode()
    for update in ({'cursor':forged},{'cursor':first.next_cursor,'record_refs':roots[:1]}):
        with pytest.raises(AdvancedFailure) as exc:store.run(PassageArguments(**(request|update)))
        assert exc.value.code=='invalid_cursor'


def test_rest_preview_retains_source_and_complete_shared_proof(tmp_path):
    path=tmp_path/'rest_support.musicxml'
    write_score(path,[{'bars':[[('C4',1),(None,1),('D4',2)]]}])
    before=path.read_bytes();store=Store();s=source(store,path,shared=True)
    args,p=overview(store,s,path,shared=True,
        scope=dict(reference_part_id='p0001',start_measure=1,end_measure=1),
        features=['notes','melody','ensemble'])
    preview=next(x for x in p.finding_previews if x['fact']['kind']=='continuity_break')
    assert preview['internally_verified'] and not preview['complete_proof_delivered']
    assert preview['support_preview_complete'] and not p.evidence_complete_in_this_response
    facts={x['event_id']:x for x in preview['source_facts']}
    rest=facts['p0001:v0001:e000002']
    assert rest['written_pitch'] is rest['sounding_pitch'] is None
    assert rest['onset']=={'numerator':1,'denominator':1}
    assert rest['duration']=={'numerator':1,'denominator':1}
    assert rest['part_id']=='p0001' and rest['voice_id']=='p0001:v0001'
    assert rest['source_segment_ids']==['p0001:v0001:s000002']
    assert rest['transpose_status']=='not_applicable'
    for eid,step,number in [('p0001:v0001:e000001','C',60),('p0001:v0001:e000003','D',62)]:
        assert facts[eid]['written_pitch']['step']==facts[eid]['sounding_pitch']['step']==step
        assert facts[eid]['written_pitch']['octave']==facts[eid]['sounding_pitch']['octave']==4
        assert facts[eid]['sounding_pitch']['chromatic_number']==number
        assert facts[eid]['transpose_status']=='verified'

    root=preview['record_ref'];observer=SharedProofObserver();pages=[]
    request={**args,'section':'evidence','record_refs':[root],
        'expected_passage_snapshot':p.passage_snapshot_id,'page_size':1,'max_bytes':8192}
    while True:
        page=store.run(PassageArguments(**request));pages.append(page)
        assert page.source_sha256==s.source_sha256
        assert page.source_snapshot_id==s.source_snapshot_id
        assert page.passage_snapshot_id==p.passage_snapshot_id
        assert page.options_sha256==p.options_sha256 and page.query_sha256==p.query_sha256
        observer.add(page)
        if not page.next_cursor:break
        request['cursor']=page.next_cursor
    assert observer.complete and len(pages)>1
    assert len(observer.rows)==pages[-1].logical_total_records
    records={row['record_ref']:row['data'] for row in observer.rows}
    audited=audited_events(records)
    assert {row['event']['id'] for row in audited}==set(facts)
    assert records['event:'+rest['event_id']]['kind']=='rest'
    assert records['segment:'+rest['source_segment_ids'][0]]['location']['measure_index']==1

    ordinary=[]
    request={**args,'delivery_policy':'record_pages_v1','section':'evidence','record_ref':root,
        'expected_passage_snapshot':p.passage_snapshot_id,'page_size':50,'max_bytes':32768}
    while True:
        page=store.run(PassageArguments(**request));ordinary.extend(i.model_dump(mode='json') for i in page.items)
        if not page.next_cursor:break
        request['cursor']=page.next_cursor
    assert observer.rows==ordinary
    assert path.read_bytes()==before


def test_oversized_shared_passage_is_typed_refusal_before_worker(monkeypatch):
    from composerlint.tool_contracts import dispatch
    def forbidden(*a,**kw):raise AssertionError('invalid range reached worker')
    monkeypatch.setattr('composerlint.advanced_runtime.request_worker',forbidden)
    result,error=dispatch('get_advanced_analysis',dict(path='unused.musicxml',
        execution_policy='source_bound_passage_v1',delivery_policy='shared_evidence_v2',
        section='overview',expected_source_snapshot='a'*64,
        scope=dict(kind='measure_range',reference_part_id='p0001',start_measure=11,end_measure=28)))
    assert error and result.error.code=='invalid_analysis_scope'
    assert 'passage must contain 1–8 traversal measures' in result.error.reason


def test_public_prevalidated_inputs_are_not_accepted():
    for field in ('already_validated','source_state','verified_passage','cache_entry','owned_source','validated_source','skip_source_validation','_graph'):
        with pytest.raises(ValidationError):PassageArguments(path='x.musicxml',execution_policy='source_bound_passage_v1',**{field:{}})


def test_state_budget_fails_without_publishing_an_oversized_source():
    store=Store(max_bytes=1)
    with pytest.raises(AdvancedFailure) as exc:source(store)
    assert exc.value.code=='analysis_limit_exceeded' and not store.sources


def test_actual_owned_worker_reuses_pid_and_is_reaped_on_timeout():
    worker=Worker()
    try:
        base={'operation':'passage','arguments':{'path':str(F),'execution_policy':'source_bound_passage_v1'}}
        first=worker.request(base);pid=worker.process.pid
        again=worker.request(base)
        assert worker.process.pid==pid and again['timings']['source_reuse_hits']==1
        process=worker.process
        with pytest.raises(AdvancedFailure) as exc:worker.request(base,timeout=0)
        assert exc.value.code=='analysis_timeout' and process.poll() is not None and worker.process is None
    finally:worker.close()


def test_bounded_codec_round_trip_corruption_truncation_and_expansion():
    import hashlib,zlib
    from composerlint.passage_codec import encode_proof,decode_proof
    rows=[{'record_ref':f'event:{i}','data':{'id':str(i),'literal':{'$template':'a literal source value'},'notes':['C4']*5}} for i in range(20)]
    encoded=encode_proof(rows);payload=encoded.pop('payload')
    assert decode_proof(payload,**encoded)==rows and len(payload)<len(canonical(rows))
    for value in (payload[:-4], 'A'+payload[1:]):
        with pytest.raises(ValueError):decode_proof(value,**encoded)
    compressed=base64.b64decode(payload)
    for invalid in (compressed[:-1],compressed+b'trailing'):
        text=base64.b64encode(invalid).decode()
        with pytest.raises(ValueError):decode_proof(text,**(encoded|{'encoded_sha256':hashlib.sha256(text.encode()).hexdigest()}))
    with pytest.raises(ValueError):decode_proof(payload,**(encoded|{'decoded_bytes':1}))
    with pytest.raises(ValueError):decode_proof(payload,**(encoded|{'decoded_bytes':32*1024*1024+1}))


def test_cached_pages_are_detached_and_cold_legacy_view_source_can_be_authenticated():
    prepared=passage.prepare(F,None,{})
    store=Store()
    args=dict(path=str(F),execution_policy='source_bound_passage_v1',section='overview',
        expected_source_snapshot=prepared.source_snapshot_id,scope=dict(reference_part_id='p0001',start_measure=1,end_measure=2))
    page=store.run(PassageArguments(**args));expected=page.model_dump(exclude={'timings'})
    page.items[0].data['notice']='client mutation'
    again=store.run(PassageArguments(**args))
    assert again.model_dump(exclude={'timings'})==expected


@pytest.mark.parametrize('change',['missing','oversized'])
def test_cached_source_checks_remain_read_bounded(tmp_path,change):
    path=tmp_path/'source.musicxml';path.write_bytes(F.read_bytes())
    store=Store();s=source(store,path)
    if change=='missing':path.unlink()
    else:
        with path.open('wb') as f:f.truncate(10*1024*1024+1)
    with pytest.raises(AdvancedFailure) as exc:overview(store,s,path)
    assert exc.value.code==('stale_snapshot' if change=='missing' else 'analysis_limit_exceeded')


def test_owned_route_cannot_construct_m1_or_use_report_reuse(monkeypatch):
    from composerlint import service,report_reuse
    from composerlint.analysis_models import AnalysisReport
    def forbidden(*a,**kw):raise AssertionError('source passage entered M1')
    for name in ('analyze_score','analyze_normalized','_build_analysis_fields'):
        monkeypatch.setattr(service,name,forbidden)
    for name in ('__init__','model_validate','model_construct','integrity'):
        monkeypatch.setattr(AnalysisReport,name,forbidden)
    for name in ('take','publish'):monkeypatch.setattr(report_reuse,name,forbidden)
    store=Store();s=source(store,shared=True);args,p=overview(store,s,shared=True)
    page=store.run(PassageArguments(**(args|{'section':'evidence','record_refs':[p.finding_previews[0]['record_ref']],
        'expected_passage_snapshot':p.passage_snapshot_id})))
    assert page.logical_total_records>0 and page.timings['scoped_predicate_passes']==0


def test_owned_source_is_validated_once_across_distinct_passages(monkeypatch):
    from composerlint.models import NormalizedScore
    from composerlint.engine import parser
    calls=[];original=parser.NormalizedScore
    # Count the existing validated source constructor; no alternative source
    # model is accepted by the store or supplied through a request.
    def counted(*args,**kw):calls.append(1);return original(*args,**kw)
    monkeypatch.setattr(parser,'NormalizedScore',counted)
    store=Store();s=source(store)
    def forbidden(*args,**kw):raise AssertionError('revalidated immutable owned source')
    monkeypatch.setattr(NormalizedScore,'model_validate_json',forbidden)
    for end in (1,2):
        _,page=overview(store,s,scope=dict(reference_part_id='p0001',start_measure=1,end_measure=end))
        assert page.timings['source_validation_passes']==0 and page.timings['normalized_source_revalidations']==0
        assert page.timings['scoped_predicate_passes']==2
    assert len(calls)==1 and s.timings['source_validation_passes']==1
