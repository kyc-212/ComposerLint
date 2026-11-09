"""Focused export controls; mocked native decisions are NOT human acceptance."""
from pathlib import Path
import json
import os
import pytest
from composerlint.editing_runtime import editing
from composerlint.viewer_runtime import runtime
from composerlint.tool_contracts import dispatch
from composerlint.viewer_source import ViewerFailure
from composerlint import editing_export as ex
from test_v02_m6_candidate import authored, bound_workspace

@pytest.fixture
def candidate(tmp_path,monkeypatch):
    (tmp_path/'output').mkdir()
    monkeypatch.setattr(ex,'EXPORT_ROOT',tmp_path/'output'/'exports')
    p=authored(tmp_path/'source.musicxml');raw=p.read_bytes()
    w,args,_,_=bound_workspace(p,monkeypatch)
    result,error=dispatch('prepare_score_edit',args);assert not error,result
    monkeypatch.setattr(ex,'confirm',lambda message:True)
    monkeypatch.setattr(ex.host,'approval_method',lambda:'test_native_decision_mock')
    yield w,ex.binding(w),raw
    editing.close();runtime.close()


def test_export_reopen_retry_and_cleanup(candidate):
    w,b,raw=candidate
    r=ex.approve_export(editing,w['id'],b)
    p=Path(r['output_path']);assert p.read_bytes()==Path(w['candidate_path']).read_bytes()
    assert w['path'].read_bytes()==raw
    assert all(r['checks'].values())
    assert json.loads(Path(r['receipt_path']).read_text())==r
    assert ex.approve_export(editing,w['id'],b)==r
    editing.close();assert p.exists()

@pytest.mark.parametrize('case',['rejected','expired','source','candidate','revision','selection','verification','brief','unverified','absent','forged'])
def test_invalid_candidates_never_export(candidate,monkeypatch,case):
    w,b,raw=candidate
    if case=='rejected':monkeypatch.setattr(ex,'confirm',lambda message:False)
    if case=='expired':w['touched']-=2000
    if case=='source':w['path'].write_bytes(raw+b' ')
    if case=='candidate':Path(w['candidate_path']).write_bytes(b'bad')
    if case=='revision':w['revision']+=1
    if case=='selection':w['entry']['revision']+=1
    if case=='verification':w['proposal']['verification']['gates']['notation_correspondence']=False
    if case=='brief':w['proposal']['operation']['brief_sha256']='0'*64
    if case=='unverified':w['state']='failed'
    if case=='absent':w['proposal']=None
    if case=='forged':b={**b,'approved':True}
    with pytest.raises(ViewerFailure):ex.approve_export(editing,w['id'],b)
    assert not list(ex.EXPORT_ROOT.glob('*.musicxml'))

@pytest.mark.parametrize('case',['collision','symlink','parent','interruption','changed_during_confirmation'])
def test_filesystem_failures_and_confirmation_race(candidate,monkeypatch,case):
    w,b,raw=candidate
    name=f"source-M6-{w['proposal']['proposal_id']}.musicxml"
    ex.EXPORT_ROOT.mkdir(parents=True)
    if case=='collision':(ex.EXPORT_ROOT/name).write_bytes(b'existing')
    if case=='symlink':(ex.EXPORT_ROOT/name).symlink_to(w['path'])
    if case=='parent':
        ex.EXPORT_ROOT.rmdir();ex.EXPORT_ROOT.symlink_to(w['path'].parent,target_is_directory=True)
    if case=='interruption':
        import composerlint.editing_runtime as er
        monkeypatch.setattr(er,'worker',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('interrupted')))
    if case=='changed_during_confirmation':
        def confirm(_):w['entry']['revision']+=1;return True
        monkeypatch.setattr(ex,'confirm',confirm)
    with pytest.raises((ViewerFailure,OSError,RuntimeError)):ex.approve_export(editing,w['id'],b)
    assert w['path'].read_bytes()==raw
    if case=='collision':assert (ex.EXPORT_ROOT/name).read_bytes()==b'existing'
    assert not w.get('export_receipt')


def test_route_does_not_accept_approval_flag(candidate):
    from composerlint.editing import browser_route
    w,b,_=candidate;token=w['entry']['token']
    assert browser_route(token,'approve-export',False,None)[0]==405
    assert browser_route(token,'approve-export',True,{'nonce':w['csrf'],'binding':b,'approved':True})[0]==403
    assert browser_route(token,'approve-export',True,{'nonce':'bad','binding':b})[0]==403
    assert browser_route('other-session','approve-export',True,{'nonce':w['csrf'],'binding':b})[0]==410

@pytest.mark.parametrize('which',['output_path','receipt_path'])
def test_modified_export_retry_never_overwrites(candidate,which):
    w,b,_=candidate;r=ex.approve_export(editing,w['id'],b)
    path=Path(r[which]);path.write_bytes(b'modified by user')
    with pytest.raises((ViewerFailure,ValueError)):ex.approve_export(editing,w['id'],b)
    assert path.read_bytes()==b'modified by user'


def test_reject_then_discard_cannot_export(candidate,monkeypatch):
    from composerlint.editing_models import DiscardArguments
    w,b,_=candidate;monkeypatch.setattr(ex,'confirm',lambda _:False)
    with pytest.raises(ViewerFailure):ex.approve_export(editing,w['id'],b)
    editing.discard(DiscardArguments(workspace_id=w['id'],proposal_id=b['proposal_id'],expected_workspace_revision=w['revision']))
    monkeypatch.setattr(ex,'confirm',lambda _:True)
    with pytest.raises(ViewerFailure):ex.approve_export(editing,w['id'],b)
    assert not list(ex.EXPORT_ROOT.glob('*.musicxml'))
