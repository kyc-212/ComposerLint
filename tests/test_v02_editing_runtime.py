"""Actual worker/render/MCP boundary and temporary lifecycle checks."""
from pathlib import Path
import copy
import json
import time
import threading
import urllib.request
import urllib.error
import pytest
from composerlint import service
from composerlint.review import digest
from composerlint.tool_contracts import dispatch,TOOLS,input_schema,output_schema
from composerlint.editing_runtime import editing,worker
from composerlint.viewer_runtime import runtime
from composerlint.viewer_source import ViewerFailure
from composerlint.editing_models import PrepareArguments,ProposalArguments,DiscardArguments

F=Path(__file__).parent/'fixtures'

@pytest.fixture(autouse=True)
def cleanup():
    yield
    editing.close();runtime.close()

def open_workspace(p):
    result,error=dispatch('get_edit_workspace',dict(path=str(p),expected_snapshot=service.get_review_page(p).snapshot_id))
    assert not error,result
    return editing.workspaces[result.workspace_id],result

def arguments(w,start=1,end=2,part='p0001',mode='unique',voice=None):
    scope=w['entry']['manifest']['scope']
    target={'part_id':part,'lane_mode':mode}
    if voice:target['voice_id']=voice
    return dict(workspace_id=w['id'],expected_source_sha256=w['source_sha256'],expected_snapshot=w['snapshot'],expected_options_sha256=w['options_sha256'],expected_workspace_revision=w['revision'],client_request_id='test-1',selection={**scope,'start_measure_index':start,'end_measure_index':end},target=target,operation={'direction':'up'})

@pytest.mark.parametrize('name,mode,voice',[
    ('v02_m3/variants/solo_horn','unique',None),('v02_m2/chord','unique',None),('v02_m2/grandstaff','all',None),('v02_m2/voices','lane','p0001:v0002'),('v02_m2/ties','unique',None),('v02_m5/preservation','unique',None),('v02_m3/orchestra','unique',None),
])
def test_verified_complete_preview_and_exact_identity(name,mode,voice):
    p=F/(name+'.musicxml');before=p.read_bytes();w,result=open_workspace(p)
    prepared,error=dispatch('prepare_score_edit',arguments(w,mode=mode,voice=voice))
    assert not error,prepared
    proposal=w['proposal'];assert proposal['verification_status']=='verified' and proposal['preview_status']=='ready'
    assert proposal['verification']['gates']['notation_correspondence']
    assert len(proposal['manifest']['changed_note_ids'])==proposal['target']['pitched_segment_count']
    assert {m['part_id'] for m in proposal['manifest']['measures']}==set(w['entry']['manifest']['scope']['part_ids'])
    assert 'notes' not in prepared.data['target']
    assert p.read_bytes()==before
    assert Path(w['candidate_path']).stat().st_mode&0o777==0o600
    assert w['original'].stat().st_mode&0o777==0o600


def test_revision_retry_failed_replacement_tamper_cursor_discard_and_cleanup():
    p=F/'v02_m3/variants/solo_horn.musicxml';w,result=open_workspace(p);args=arguments(w)
    first,error=dispatch('prepare_score_edit',args);assert not error
    again,error=dispatch('prepare_score_edit',args);assert not error and first.data==again.data
    forged={**args,'operation':{'direction':'down'}};assert dispatch('prepare_score_edit',forged)[1]
    proposal=w['proposal'];bad=arguments(w,start=3,end=3);bad['client_request_id']='all-rest'
    assert dispatch('prepare_score_edit',bad)[1];assert w['proposal'] is proposal and w['state']=='verified'
    bound=dict(workspace_id=w['id'],proposal_id=proposal['proposal_id'],expected_source_sha256=w['source_sha256'],expected_candidate_sha256=proposal['candidate_sha256'],expected_verification_sha256=proposal['verification_sha256'],section='changes',page_size=2)
    all_= [];page=editing.get(ProposalArguments(**bound))
    while True:
        all_.extend(page.data['items'])
        if page.data['next_cursor'] is None:break
        page=editing.get(ProposalArguments(**bound,cursor=page.data['next_cursor']))
    assert len(all_)==7 and all_[0]['after_written']['octave']==6
    with pytest.raises(ViewerFailure):editing.get(ProposalArguments(**{**bound,'expected_candidate_sha256':'0'*64}))
    with pytest.raises(ViewerFailure):editing.get(ProposalArguments(**bound,cursor='bad:1'))
    directory=w['directory'];candidate=Path(w['candidate_path'])
    args=DiscardArguments(workspace_id=w['id'],proposal_id=proposal['proposal_id'],expected_workspace_revision=w['revision'])
    editing.discard(args);editing.discard(args);assert not candidate.exists() and w['proposal'] is None
    editing.close();assert not directory.exists()


def test_source_replacement_staleness_and_expiry(tmp_path):
    p=tmp_path/'copy.musicxml';p.write_bytes((F/'v02_m3/variants/solo_horn.musicxml').read_bytes());w,_=open_workspace(p)
    original=p.read_bytes();p.write_bytes(original.replace(b'<step>C</step>',b'<step>D</step>',1))
    result,error=dispatch('prepare_score_edit',arguments(w));assert error and result.error.code=='stale_source'
    assert p.read_bytes()!=original and w['proposal'] is None
    p.write_bytes(original);editing.close();runtime.close();w,_=open_workspace(p);folder=w['directory'];w['created']-=editing.absolute_ttl+1
    with pytest.raises(ViewerFailure):editing.find(w['id'])
    assert not folder.exists()


def test_http_nonce_no_xml_route_and_selection_race(monkeypatch):
    w,result=open_workspace(F/'v02_m3/variants/solo_horn.musicxml');url=result.data['workspace_url']
    r,error=dispatch('prepare_score_edit',arguments(w));assert not error
    p=w['proposal'];nonce=w['csrf']
    for path in ('original.musicxml','candidate.musicxml','p-'+p['proposal_id']+'-candidate.musicxml'):
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(url+path)
    data=json.dumps(dict(proposal_id=p['proposal_id'],expected_workspace_revision=w['revision'],nonce='forged')).encode()
    with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(url+'discard',data=data,headers={'Content-Type':'application/json'}))
    assert error.value.code==403
    import composerlint.editing_runtime as module
    actual=module.worker
    def raced(*args,**kwargs):
        result=actual(*args,**kwargs);w['entry']['revision']+=1;return result
    monkeypatch.setattr(module,'worker',raced)
    args=arguments(w);args['client_request_id']='raced'
    result,error=dispatch('prepare_score_edit',args);assert error and result.error.code=='revision_conflict'
    assert w['proposal'] is p and len(list(w['directory'].glob('*.musicxml')))==2


def test_whole_deadline_reaps_worker_and_child(monkeypatch):
    import composerlint.editing_runtime as module
    with pytest.raises(ViewerFailure,match='terminated and reaped'):
        module.worker({},timeout=.001)


def test_deferred_and_untyped_edit_requests_rejected_before_work():
    for operation in ({'kind':'replace_note','direction':'up'},{'kind':'rhythm','direction':'up'},{'kind':'octave_transpose','direction':'up','semitones':24}):
        result,error=dispatch('prepare_score_edit',dict(workspace_id='x',expected_source_sha256='0'*64,expected_snapshot='0'*64,expected_options_sha256='0'*64,expected_workspace_revision=0,client_request_id='x',live_selection_revision=1,target={'part_id':'p0001'},operation=operation))
        assert error and result.error.code=='invalid_edit'


def test_owned_candidate_and_asset_tamper_block_current_claims():
    w,_=open_workspace(F/'v02_m3/variants/solo_horn.musicxml');r,error=dispatch('prepare_score_edit',arguments(w));assert not error
    p=w['proposal'];args=ProposalArguments(workspace_id=w['id'],proposal_id=p['proposal_id'],expected_source_sha256=w['source_sha256'],expected_candidate_sha256=p['candidate_sha256'],expected_verification_sha256=p['verification_sha256'])
    original=Path(w['candidate_path']).read_bytes();Path(w['candidate_path']).write_bytes(original+b' ')
    with pytest.raises(ViewerFailure,match='changed'):editing.get(args)
    assert w['state']=='failed'


def test_actual_sdk_edit_schemas_and_canonical_delivery(tmp_path):
    import asyncio,sys,jsonschema
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        root=Path(__file__).parents[1];p=(F/'v02_m3/variants/solo_horn.musicxml').resolve();before=p.read_bytes()
        with (tmp_path/'stderr').open('w') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=root,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=55) as client:
                    await client.initialize();tools={t.name:t for t in (await client.list_tools()).tools}
                    assert len(tools)==17 and not tools['prepare_score_edit'].annotations.read_only_hint and not tools['discard_edit_proposal'].annotations.read_only_hint
                    assert not tools['present_workspace_analysis'].annotations.read_only_hint
                    assert tools['get_edit_proposal'].annotations.read_only_hint
                    async def call(name,args):
                        jsonschema.validate(args,tools[name].input_schema)
                        result=await client.call_tool(name,args);assert not result.is_error,result
                        assert json.loads(result.content[0].text)==result.structured_content
                        jsonschema.validate(result.structured_content,tools[name].output_schema)
                        assert len(result.content[0].text.encode())<=24576
                        return result.structured_content
                    overview=await call('get_review_page',{'path':str(p)})
                    result=await call('get_edit_workspace',{'path':str(p),'expected_snapshot':overview['snapshot_id']});state=result['data'];scope=state['selection']['selection']
                    # Named traversal uses canonical identity rather than an editing control.
                    import urllib.request
                    with urllib.request.urlopen(state['workspace_url']+'manifest.json') as response:manifest=json.load(response)
                    args=dict(workspace_id=result['workspace_id'],expected_source_sha256=state['source_sha256'],expected_snapshot=state['base_snapshot'],expected_options_sha256=state['options_sha256'],expected_workspace_revision=0,client_request_id='sdk-named-1',selection={**manifest['scope'],'start_measure_index':1,'end_measure_index':2},target={'part_id':'p0001'},operation={'direction':'down'})
                    prepared=await call('prepare_score_edit',args);proposal=prepared['data']
                    bound=dict(workspace_id=result['workspace_id'],proposal_id=proposal['proposal_id'],expected_source_sha256=proposal['source_sha256'],expected_candidate_sha256=proposal['candidate_sha256'],expected_verification_sha256=proposal['verification_sha256'],section='verification')
                    detail=await call('get_edit_proposal',bound);assert detail['data']['items']
                    await call('discard_edit_proposal',dict(workspace_id=result['workspace_id'],proposal_id=proposal['proposal_id'],expected_workspace_revision=prepared['workspace_revision']))
        assert p.read_bytes()==before
        assert 'Traceback' not in (tmp_path/'stderr').read_text()
    asyncio.run(check())


def test_normal_view_review_then_edit_stays_in_same_workspace():
    p=F/'v02_m3/variants/solo_horn.musicxml';before=p.read_bytes()
    snapshot=service.get_review_page(p).snapshot_id
    view,error=dispatch('get_score_view',dict(path=str(p),expected_snapshot=snapshot))
    assert not error
    def context():
        with urllib.request.urlopen(view.viewer_url+'workspace.json') as response:return json.load(response)
    state=context()
    assert state['state']=='no_proposal' and state['workspace_id'] is None
    assert not editing.workspaces and editing.root is None
    assert state['target_choices'][0]['label']=='Authored F horn'
    entry=runtime.entries[view.view_instance_id]
    from composerlint.viewer_models import Selection
    entry['selected']=Selection(**{**entry['manifest']['scope'],'start_measure_index':1,'end_measure_index':2})
    entry['revision']=1
    w,opened=open_workspace(p)
    assert w['entry'] is entry and opened.data['workspace_url']==view.viewer_url
    assert context()['selection']['revision']==1
    prepared,error=dispatch('prepare_score_edit',arguments(w))
    assert not error and context()['proposal']['proposal_id']==prepared.data['proposal_id']
    repeated,error=dispatch('get_score_view',dict(path=str(p),expected_snapshot=snapshot))
    assert not error and repeated.viewer_url==view.viewer_url and len(runtime.entries)==1
    assert context()['selection']['selection']['end_measure_index']==2
    editing.discard(DiscardArguments(workspace_id=w['id'],proposal_id=w['proposal']['proposal_id'],expected_workspace_revision=w['revision']))
    assert context()['proposal'] is None and p.read_bytes()==before
    w['created']-=editing.absolute_ttl+1
    assert context()['workspace_id'] is None  # review survives temporary edit expiry
    assert context()['base_snapshot']==snapshot


def test_read_only_view_does_not_require_edit_support(tmp_path):
    p=tmp_path/'linked.musicxml';p.symlink_to((F/'v02_m3/variants/solo_horn.musicxml').resolve())
    snapshot=service.get_review_page(p).snapshot_id
    view,error=dispatch('get_score_view',dict(path=str(p),expected_snapshot=snapshot))
    assert not error
    with urllib.request.urlopen(view.viewer_url+'workspace.json') as response:
        assert json.load(response)['state']=='no_proposal'
    refused,error=dispatch('get_edit_workspace',dict(path=str(p),expected_snapshot=snapshot,view_instance_id=view.view_instance_id))
    assert error and refused.error.code=='unsupported_edit'
    assert not editing.workspaces
    with urllib.request.urlopen(view.svg_url) as response:assert response.status==200


def test_equal_bytes_different_sources_do_not_share_workspace(tmp_path):
    p=F/'v02_m3/variants/solo_horn.musicxml';other=tmp_path/'other.musicxml';other.write_bytes(p.read_bytes())
    snapshot=service.get_review_page(p).snapshot_id
    first,error=dispatch('get_score_view',dict(path=str(p),expected_snapshot=snapshot));assert not error
    second,error=dispatch('get_score_view',dict(path=str(other),expected_snapshot=snapshot));assert not error
    assert first.view_instance_id!=second.view_instance_id
    refused,error=dispatch('get_edit_workspace',dict(path=str(p),expected_snapshot=snapshot,view_instance_id=second.view_instance_id))
    assert error and refused.error.code=='stale_snapshot'
    w,opened=open_workspace(p)
    assert opened.data['workspace_url']==first.viewer_url
    with urllib.request.urlopen(second.viewer_url+'workspace.json') as response:assert json.load(response)['workspace_id'] is None


def test_edit_first_then_normal_open_reuses_view():
    p=F/'v02_m3/variants/solo_horn.musicxml';w,opened=open_workspace(p)
    view,error=dispatch('get_score_view',dict(path=str(p),expected_snapshot=w['snapshot']))
    assert not error and view.viewer_url==opened.data['workspace_url']
    assert len(runtime.entries)==1
