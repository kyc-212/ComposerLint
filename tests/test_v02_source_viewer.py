"""Integrated source-only viewing and authenticated live passage retrieval."""
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

import jsonschema
import pytest
from composerlint import viewer_passage
from composerlint.review import canonical,digest
from composerlint.tool_contracts import dispatch,TOOL_BY_NAME,input_schema,output_schema
from composerlint.viewer_models import ViewRequest,ResolveRequest
from composerlint.viewer_runtime import runtime
from pydantic import ValidationError
from v02_advanced_helpers import write_score

F=Path(__file__).parent/'fixtures'


@pytest.fixture(autouse=True)
def cleanup():
    yield
    runtime.close()


def call(name,args):
    result,error=dispatch(name,args)
    assert not error,result
    jsonschema.validate(result.model_dump(mode='json'),output_schema(TOOL_BY_NAME[name]))
    return result


def open_score(path,**kwargs):return call('get_score_view',{'path':str(path),'presentation':'interactive',**kwargs})


def read(url):
    with urllib.request.urlopen(url) as response:return json.load(response)


def select(view,start=1,end=2,part='p0001'):
    revision=read(view.viewer_url+'selection')['revision']+1
    req=urllib.request.Request(view.viewer_url+'selection',data=canonical({'start':start,'end':end,'revision':revision,'reference_part_id':part}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req) as response:return json.load(response)


def resolve_args(path,live):
    return {'path':str(path),**{k:live[k] for k in ('expected_source_snapshot','view_instance_id','revision','selection_ref')}}


def proof(args,ref,snapshot):
    fields={**args,'section':'evidence','record_ref':ref,'expected_passage_snapshot':snapshot};rows=[];pages=0
    while True:
        page=call('get_advanced_analysis',fields);pages+=1
        assert page.start_index==len(rows)
        rows+=page.items
        assert page.viewer_selection_ref==args['viewer_selection_ref']
        if not page.next_cursor:break
        fields['cursor']=page.next_cursor
    assert len(rows)==page.total_items==len({r.record_ref for r in rows})
    return rows,pages


def test_open_to_complete_proof_forbids_all_m1_work(monkeypatch):
    from composerlint import service,report_reuse,advanced_runtime,advanced_worker
    from composerlint.analysis_models import AnalysisReport
    from composerlint.engine import key_analysis,voice_leading,harmony,harmonic_checks
    def forbidden(*a,**kw):raise AssertionError('source viewer entered whole-score M1 processing')
    for name in ('analyze_score','analyze_normalized','_build_analysis_fields','select_key'):monkeypatch.setattr(service,name,forbidden)
    for name in ('__init__','model_validate','model_construct','integrity'):monkeypatch.setattr(AnalysisReport,name,forbidden)
    for module,names in [(key_analysis,['select_key','estimate_candidates']),(voice_leading,['execute']),
        (harmony,['execute']),(harmonic_checks,['execute_sevenths','execute_cadences']),(report_reuse,['take','publish'])]:
        for name in names:monkeypatch.setattr(module,name,forbidden)
    # Run the real worker entry under the same guards; renderer isolation stays real.
    original=subprocess.run
    def instrumented(argv,**kwargs):
        if str(argv[-1]).endswith('advanced_worker.py'):
            output=io.StringIO()
            with monkeypatch.context() as scoped:
                scoped.setattr(sys,'stdin',io.StringIO(kwargs['input']));scoped.setattr(sys,'stdout',output)
                advanced_worker.main()
            return subprocess.CompletedProcess(argv,0,output.getvalue(),'')
        return original(argv,**kwargs)
    monkeypatch.setattr(advanced_runtime.subprocess,'run',instrumented)
    path=F/'v02_m3/trio.musicxml';view=open_score(path);live=select(view,1,1)
    resolved=call('resolve_score_selection',resolve_args(path,live))
    args=resolved.passage_request.model_dump(mode='json');overview=call('get_advanced_analysis',args)
    indexes=[i.data for i in overview.items if i.record_ref.startswith('advanced_index:')]
    for kind in ('interval.vertical','sonority_transition','ensemble.motion'):
        ref=next(i['first_observation_ref'] for i in indexes if i['kind']==kind)
        rows,pages=proof(args,ref,overview.passage_snapshot_id)
        assert pages>=1 and any(r.record_ref.startswith('transpose:') for r in rows)
    assert not hasattr(view,'snapshot_id') and not hasattr(resolved.selection,'snapshot_id')


@pytest.mark.parametrize('fixture', ['v02_m2/grandstaff.musicxml','v02_m2/voices.musicxml',
    'v02_m2/pickup.musicxml','v02_m2/duplicate.musicxml','v02_m2/ties.musicxml',
    'v02_m3/variants/solo_horn.musicxml','v02_m3/trio.musicxml'])
def test_all_source_notes_parts_staves_and_exact_pitches(fixture):
    path=F/fixture;before=path.read_bytes();view=open_score(path)
    entry=runtime.find(view.view_instance_id);manifest=entry['manifest']
    source=viewer_passage.passage.prepare(path,None,{})
    assert set(m['part_id'] for m in manifest['measures'])=={p.id for p in source.score.parts}
    assert sum(len(p['groups']) for p in manifest['pages'])>=1
    assert set(manifest['note_measure_ids'])==set(entry['render_context'][2])
    assert len(manifest['note_measure_ids'])==sum(len(p.source_segments) for p in source.score.parts)
    live=select(view);result=call('resolve_score_selection',resolve_args(path,live))
    page=call('get_advanced_analysis',result.passage_request.model_dump(mode='json'))
    assert page.source_snapshot_id==view.source_snapshot_id
    if 'grandstaff' in fixture:assert {n['staff'] for n in entry['render_context'][2].values()}=={'1','2'}
    if 'pickup' in fixture:assert manifest['measures'][0]['printed_label']=='0'
    if 'duplicate' in fixture:assert {m['printed_label'] for m in manifest['measures']}=={'7'}
    if 'solo_horn' in fixture:
        notes=call('get_advanced_analysis',{**result.passage_request.model_dump(mode='json'),'section':'observations','expected_passage_snapshot':page.passage_snapshot_id})
        first=next(i.data['fact'] for i in notes.items if i.data.get('fact',{}).get('kind')=='note')
        assert (first['written_pitch']['step'],first['written_pitch']['octave'])==('C',5)
        assert (first['sounding_pitch']['step'],first['sounding_pitch']['octave'])==('F',4)
    assert path.read_bytes()==before


def test_natural_pages_cross_page_range_and_existing_asset_reuse():
    path=F/'v02_m2/multipage.musicxml';view=open_score(path,page_size=1)
    e=runtime.find(view.view_instance_id);m=e['manifest']
    assert view.notation_page==1 and view.page_count>=3 and any(len(p['groups'])>8 for p in m['pages'])
    assert len({x for p in m['pages'] for x in p['measure_ids']})==len(m['measures'])==192
    boundary=max(x['ordinal'] for x in m['measures'] if x['part_id']=='p0001' and x['id'] in m['pages'][0]['measure_ids'])
    live=select(view,boundary-3,boundary+4)
    for number in (2,view.page_count,1):
        pending=e['pages'][number-1] is None
        page=call('get_score_view',{'path':str(path),'expected_source_snapshot':view.source_snapshot_id,'view_instance_id':view.view_instance_id,'notation_page':number,'presentation':'interactive'})
        assert page.view_instance_id==view.view_instance_id
        assert page.timings['render_mapping_seconds']>0 if pending else page.timings['render_mapping_seconds']==0
        assert read(view.viewer_url+'selection')==live
    with pytest.raises(urllib.error.HTTPError) as exc:select(view,boundary-3,boundary+5)
    assert exc.value.code==400;exc.value.close()
    assert read(view.viewer_url+'selection')==live
    resolved=call('resolve_score_selection',resolve_args(path,live))
    assert resolved.passage_request.scope.end_measure-resolved.passage_request.scope.start_measure==7


@pytest.mark.parametrize('damage',['signature','rehash','source','instance','revision','path','options','legacy_mix','legacy_token','scope','cursor'])
def test_forged_stale_and_mixed_references_rejected(tmp_path,damage):
    path=F/'v02_m3/trio.musicxml';view=open_score(path);live=select(view)
    args=resolve_args(path,live)
    if damage in ('signature','rehash'):
        value=json.loads(base64.urlsafe_b64decode(live['selection_ref'][5:]+'='*(-len(live['selection_ref'][5:])%4)))
        if damage=='signature':value['signature']='0'*64
        else:value['selection']['start_measure_index']=2;value['signature']=digest(value['selection'])
        args['selection_ref']='CLV2.'+base64.urlsafe_b64encode(canonical(value).encode()).decode().rstrip('=')
    elif damage=='source':args['expected_source_snapshot']='0'*64
    elif damage=='instance':args['view_instance_id']='wrong'
    elif damage=='revision':select(view,2,2)
    elif damage=='path':other=tmp_path/'same.musicxml';other.write_bytes(path.read_bytes());args['path']=str(other)
    elif damage=='options':args['options']={'key_override':{'tonic':'C','mode':'major'}}
    elif damage=='legacy_mix':args['expected_snapshot']='0'*64
    elif damage=='legacy_token':args['selection_ref']='CLV1.'+live['selection_ref'][5:]
    elif damage=='cursor':args['cursor']='forged'
    elif damage=='scope':
        r=call('resolve_score_selection',args);p=r.passage_request.model_dump(mode='json');p['scope']['start_measure']=2
        bad,error=dispatch('get_advanced_analysis',p);assert error and bad.error.code=='unavailable_evidence';return
    _,error=dispatch('resolve_score_selection',args);assert error


def test_changed_selection_during_computation_and_cross_view_cursor_rejected(monkeypatch):
    from composerlint import advanced_runtime
    path=F/'v02_m3/trio.musicxml';v=open_score(path);live=select(v)
    r=call('resolve_score_selection',resolve_args(path,live));args=r.passage_request.model_dump(mode='json');args['page_size']=1
    first=call('get_advanced_analysis',args);assert first.next_cursor
    # Same source/range in a distinct viewer cannot adopt this cursor.
    other=open_score(path,width=1200);second=select(other)
    r2=call('resolve_score_selection',resolve_args(path,second));a2=r2.passage_request.model_dump(mode='json')
    assert r2.view_key==other.view_key
    bad,error=dispatch('get_advanced_analysis',{**a2,'page_size':1,'cursor':first.next_cursor,'expected_passage_snapshot':first.passage_snapshot_id})
    assert error and bad.error.code=='invalid_cursor'
    original=advanced_runtime.request_worker
    def changed(*a,**kw):
        result=original(*a,**kw);select(other,2,2);return result
    monkeypatch.setattr(advanced_runtime,'request_worker',changed)
    bad,error=dispatch('get_advanced_analysis',a2)
    assert error and bad.error.code=='unavailable_evidence'


def test_different_part_boundaries_use_exact_time_and_focus_keeps_occupants(tmp_path):
    path=tmp_path/'unaligned.musicxml'
    write_score(path,[{'bars':[[('C5',4)],[('D5',4)],[('E5',4)]]},
                      {'bars':[[('C3',2)],[('D3',4)],[('E3',4)],[('F3',2)]]}])
    view=open_score(path)
    assert view.status=='mapped'
    live=select(view,2,2)
    result=call('resolve_score_selection',{**resolve_args(path,live),'focus_part_ids':['p0001']})
    scope=next(i.data for i in result.items if i.record_ref=='source:passage_scope')
    assert {'p0002:v0001:e000002','p0002:v0001:e000003'}<=set(scope['occupancy_event_ids'])
    assert all(e.startswith('p0001:') for e in scope['primary_event_ids'])
    assert {i.data['printed_label'] for i in result.items if i.record_ref.startswith('measure:')}=={'2','3'}
    # The longer second part can become the live reference, including its tail.
    live=select(view,4,4,part='p0002')
    result=call('resolve_score_selection',resolve_args(path,live))
    assert result.passage_request.scope.reference_part_id=='p0002'
    assert result.view_key==view.view_key
    scope=next(i.data for i in result.items if i.record_ref=='source:passage_scope')
    assert (scope['target_spans'][0]['start']['numerator'],scope['target_spans'][0]['end']['numerator'])==(10,12)


def test_strict_identity_contracts_and_source_view_cursor():
    for arguments in ({'path':'x','expected_snapshot':'0'*64,'expected_source_snapshot':'0'*64},
                      {'path':'x','execution_policy':'source_bound_passage_v1','part_ids':['p0001']},
                      {'path':'x','notation_page':2}):
        with pytest.raises(ValidationError):ViewRequest.model_validate(arguments)
    view=open_score(F/'v02_m3/trio.musicxml',page_size=1)
    args={'path':str(F/'v02_m3/trio.musicxml'),'expected_source_snapshot':view.source_snapshot_id,'view_instance_id':view.view_instance_id,
          'cursor':view.next_cursor,'page_size':1,'presentation':'interactive'}
    page=call('get_score_view',args);assert page.start_index==1
    bad,error=dispatch('get_score_view',{**args,'page_size':2});assert error and bad.error.code=='invalid_cursor'


def test_selected_tie_retains_external_segments_in_complete_proof():
    path=F/'v02_m2/ties.musicxml';view=open_score(path);live=select(view,2,2)
    r=call('resolve_score_selection',resolve_args(path,live));args=r.passage_request.model_dump(mode='json')
    overview=call('get_advanced_analysis',args)
    notes=call('get_advanced_analysis',{**args,'section':'observations','expected_passage_snapshot':overview.passage_snapshot_id})
    note=next(i for i in notes.items if i.data.get('fact',{}).get('kind')=='note' and len(i.data['support'][0]['source_segment_ids'])>1)
    assert not note.data['fact']['attack_in_target']
    rows,_=proof(args,note.record_ref,overview.passage_snapshot_id)
    segments={i.record_ref for i in rows if i.record_ref.startswith('segment:')}
    assert {'segment:'+s for s in note.data['support'][0]['source_segment_ids']}<=segments
    assert any(i.data.get('ordinal')==1 for i in rows if i.record_ref.startswith('measure:'))


def test_horn_and_grandstaff_piano_integrated_focus_keeps_every_lane(tmp_path):
    from copy import deepcopy
    horn=ET.fromstring((F/'v02_m3/variants/solo_horn.musicxml').read_bytes())
    piano=ET.fromstring((F/'v02_m2/grandstaff.musicxml').read_bytes())
    root=ET.Element('score-partwise',version='4.0');headers=ET.SubElement(root,'part-list')
    for source,ident in ((horn,'H'),(piano,'P')):
        header=deepcopy(source.find('part-list/score-part'));header.set('id',ident);headers.append(header)
        part=deepcopy(source.find('part'));part.set('id',ident);root.append(part)
    path=tmp_path/'authored_horn_piano.musicxml';path.write_bytes(ET.tostring(root))
    view=open_score(path);entry=runtime.find(view.view_instance_id)
    assert {(n['part_id'],n['staff']) for n in entry['render_context'][2].values()}=={('p0001',None),('p0002','1'),('p0002','2')}
    live=select(view);r=call('resolve_score_selection',{**resolve_args(path,live),'focus_part_ids':['p0001']})
    args=r.passage_request.model_dump(mode='json');overview=call('get_advanced_analysis',args)
    notes=call('get_advanced_analysis',{**args,'section':'observations','expected_passage_snapshot':overview.passage_snapshot_id})
    facts=[i.data for i in notes.items if i.data.get('fact',{}).get('kind')=='note']
    first=next(x['fact'] for x in facts if x['support'][0]['part_id']=='p0001')
    assert (first['written_pitch']['step'],first['written_pitch']['octave'],first['sounding_pitch']['step'],first['sounding_pitch']['octave'])==('C',5,'F',4)
    assert {s['voice_id'] for x in facts for s in x['support']}=={'p0001:v0001','p0002:v0001','p0002:v0002'}
    assert all(x['support'][0]['role']=='context' for x in facts if x['support'][0]['part_id']=='p0002')


@pytest.mark.parametrize('fixture',['v02_m2/grandstaff.musicxml','v02_m3/variants/solo_horn.musicxml','classical_intelligence/mozart_positive.musicxml'])
def test_source_viewing_leaves_complete_legacy_canonical_report_identical(fixture):
    from composerlint.service import analyze_score
    path=F/fixture;before=analyze_score(path).canonical_json()
    view=open_score(path);live=select(view,1,1)
    r=call('resolve_score_selection',resolve_args(path,live))
    call('get_advanced_analysis',r.passage_request.model_dump(mode='json'))
    assert analyze_score(path).canonical_json()==before


def test_live_envelope_counts_against_the_existing_tight_page_budget():
    path=F/'v02_m3/trio.musicxml';view=open_score(path);live=select(view)
    r=call('resolve_score_selection',resolve_args(path,live));args=r.passage_request.model_dump(mode='json')
    args['max_bytes']=8192
    overview=call('get_advanced_analysis',args)
    assert len(canonical(overview.model_dump(mode='json')).encode())<=8192
    ref=next(i.data['first_observation_ref'] for i in overview.items if i.record_ref.startswith('advanced_index:'))
    fields={**args,'section':'evidence','record_ref':ref,'expected_passage_snapshot':overview.passage_snapshot_id};rows=[]
    while True:
        page=call('get_advanced_analysis',fields)
        assert len(canonical(page.model_dump(mode='json')).encode())<=8192
        rows+=page.items
        if not page.next_cursor:break
        fields['cursor']=page.next_cursor
    assert len(rows)==page.total_items==len({r.record_ref for r in rows})
    _,error=dispatch('get_advanced_analysis',{**args,'response_overhead':0});assert error


def test_source_error_does_not_advertise_an_implicit_m1_fallback():
    error,failed=dispatch('get_score_view',{'path':'/missing_source_bound_fixture.musicxml'})
    assert failed and error.viewer_schema_version==2 and error.error.fallback=='get_score_view'
    jsonschema.validate(error.model_dump(mode='json'),output_schema(TOOL_BY_NAME['get_score_view']))


def test_legacy_error_keeps_its_original_namespace_and_fallback():
    error,failed=dispatch('get_score_view',{'path':'/missing_legacy_fixture.musicxml','expected_snapshot':'0'*64})
    assert failed and error.viewer_schema_version==1 and error.error.fallback=='get_review_page'
    jsonschema.validate(error.model_dump(mode='json'),output_schema(TOOL_BY_NAME['get_score_view']))
