"""Captured passages preserve identity and requested roles without changing live selection."""
from pathlib import Path
import copy
import hashlib
import pytest
from composerlint import service
from composerlint.viewer_runtime import Runtime
from composerlint.viewer_models import Selection
from composerlint.viewer_source import ViewerFailure
from composerlint.tool_contracts import dispatch

SOURCE=Path(__file__).parent/'fixtures/v02_m3/variants/solo_horn.musicxml'

def setup(source=SOURCE):
    overview=service.get_review_page(source);scope=dict(viewer_schema_version=1,source_sha256=overview.source_sha256,snapshot_id=overview.snapshot_id,options_sha256=overview.options_sha256,reference_part_id='p0001',part_ids=['p0001'],start_measure_index=1,end_measure_index=1)
    entry=dict(instance='view-a',source_path=str(source.resolve()),manifest={'scope':scope},selected=Selection(**scope),revision=1,size=100)
    runtime=Runtime();runtime.entries[entry['instance']]=entry
    return runtime,entry,scope

def capture(runtime,entry,slot,revision):
    return runtime.comparison_value(entry,dict(slot=slot,selection_revision=entry['revision'],pair_revision=revision))

def test_pair_direction_revision_overlap_and_unchanged_selection():
    runtime,entry,scope=setup()
    # A deliberately occurs after B.
    entry['selected']=Selection(**{**scope,'start_measure_index':2,'end_measure_index':2})
    capture(runtime,entry,'A',0)
    entry['selected']=Selection(**scope);entry['revision']=2
    pair=capture(runtime,entry,'B',1)
    assert pair['A']['selection']['start_measure_index']==2 and pair['B']['selection']['start_measure_index']==1
    assert entry['selected'].start_measure_index==1 and entry['revision']==2
    with pytest.raises(ViewerFailure,match='changed'):capture(runtime,entry,'A',1)
    with pytest.raises(ViewerFailure,match='nonoverlapping'):capture(runtime,entry,'A',2)
    assert runtime.comparison_value(entry)['revision']==2
    comparison={}
    for key,slot in [('first','A'),('second','B')]:
        s=pair[slot]['selection'];comparison[key]=dict(kind='measure_range',reference_part_id=s['reference_part_id'],part_ids=s['part_ids'],start_measure=s['start_measure_index'],end_measure=s['end_measure_index'])
    result,error=dispatch('get_advanced_analysis',dict(path=str(SOURCE),expected_base_snapshot=scope['snapshot_id'],comparison=comparison,features=['passages']))
    assert not error,result
    clear=capture(runtime,entry,'clear',2)
    assert clear['A'] is None and clear['B'] is None and entry['selected'].start_measure_index==1

def test_pair_stale_source_and_capture_conflict(tmp_path):
    source=tmp_path/'copy.musicxml';source.write_bytes(SOURCE.read_bytes())
    runtime,entry,_=setup(source);capture(runtime,entry,'A',0)
    with pytest.raises(ViewerFailure):
        runtime.comparison_value(entry,dict(slot='B',selection_revision=0,pair_revision=1))
    source.write_bytes(source.read_bytes()+b' ')
    with pytest.raises(ViewerFailure,match='changed'):runtime.comparison_value(entry)

def test_exact_mcp_lifecycle_and_pair_http():
    import json
    import urllib.request
    from composerlint.editing_runtime import editing
    from composerlint.viewer_runtime import runtime
    original=SOURCE.read_bytes()
    try:
        snapshot=service.get_review_page(SOURCE).snapshot_id
        view,error=dispatch('get_score_view',dict(path=str(SOURCE),expected_snapshot=snapshot,presentation='interactive'))
        assert not error,view
        result,error=dispatch('get_edit_workspace',dict(path=str(SOURCE),expected_snapshot=snapshot,view_instance_id=view.view_instance_id))
        assert not error,result
        w=editing.workspaces[result.workspace_id];scope=w['entry']['manifest']['scope']
        args=dict(workspace_id=w['id'],expected_source_sha256=w['source_sha256'],expected_snapshot=snapshot,expected_options_sha256=w['options_sha256'],expected_workspace_revision=0,client_request_id='exact-mcp',selection={**scope,'start_measure_index':1,'end_measure_index':2},target={'part_id':'p0001'},operation={'kind':'exact_pitch_batch','changes':[{'kind':'replace','notes':[{'source_note_ordinal':1,'expected_written':{'step':'C','octave':5}}],'destination':{'step':'D','octave':5}}]})
        prepared,error=dispatch('prepare_score_edit',args)
        assert not error,prepared
        assert prepared.edit_policy_id=='v02_m5_exact_pitch_v1'
        pid=w['proposal']['proposal_id']
        refused,error=dispatch('prepare_score_edit',{**args,'client_request_id':'stale','expected_workspace_revision':0})
        assert error and w['proposal']['proposal_id']==pid
        url=view.viewer_url
        def post(route,body):
            req=urllib.request.Request(url+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as r:return json.load(r)
        post('selection',{'start':1,'end':1,'revision':1})
        pair=post('comparison',{'slot':'A','selection_revision':1,'pair_revision':0})
        assert pair['A']['selection']['start_measure_index']==1
        post('selection',{'start':2,'end':2,'revision':2})
        pair=post('comparison',{'slot':'B','selection_revision':2,'pair_revision':1})
        assert pair['revision']==2 and pair['B']['selection']['start_measure_index']==2
        discarded,error=dispatch('discard_edit_proposal',dict(workspace_id=w['id'],proposal_id=pid,expected_workspace_revision=w['revision']))
        assert not error and w['proposal'] is None and SOURCE.read_bytes()==original
    finally:
        editing.close();runtime.close()
