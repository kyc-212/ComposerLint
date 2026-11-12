"""Focused authored rhythm timelines, actual-byte attacks and renderer gates."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from composerlint import service
from composerlint.models import rt
from composerlint.editing_rhythm import transform, index
from composerlint.editing_rhythm_verify import verify, verify_notation
from composerlint.viewer_source import ViewerFailure, source_index, aligned_range
from composerlint.renderers.verovio import render
from v02_advanced_helpers import write_score


def authored(path):
    write_score(path,[{'name':'Protected melody','bars':[[('C5',2),('D5',2)]]*3},
        {'name':'Accompaniment','bars':[[('C4',1),(None,1),('G4',1),('E4',1)]]*3},
        {'name':'Protected bass','bars':[[('C3',4)]]*3}],meter=(4,4))
    return path


def operation(report,kind='subdivide_note',ordinal=1,form='repeated_notes',ds=(Fraction(1,2),Fraction(1,2))):
    p=report.score.parts[1];segs={s.timing_provenance.source_note_ordinal:s for s in p.source_segments};events={e.id:e for e in p.events}
    ns=[ordinal] if kind=='subdivide_note' else [ordinal,ordinal+1]
    sources=[]
    for n in ns:
        s=segs[n];e=events[s.event_id]
        sources.append(dict(source_note_ordinal=n,expected_kind=e.kind,expected_written={k:getattr(e.written_pitch,k) for k in ('step','alteration','octave')} if e.kind=='pitched' else None,
            expected_onset=s.original_onset.model_dump(mode='json'),expected_duration=s.original_duration.model_dump(mode='json')))
    return dict(family='rhythm',kind=kind,sources=sources,durations=[rt(d).model_dump(mode='json') for d in ds],form=form if kind=='subdivide_note' else None)


def make(tmp_path,ops=None,form='repeated_notes'):
    path=authored(tmp_path/'original.musicxml');before=service.analyze_score(path);raw=path.read_bytes()
    sel=dict(reference_part_id='p0001',start_measure_index=1,end_measure_index=3);target=dict(part_id='p0002',lane_mode='unique')
    ops=ops or [operation(before,form=form)]
    candidate=transform(raw,before,sel,target,ops);cp=tmp_path/'candidate.musicxml';cp.write_bytes(candidate);after=service.analyze_score(cp)
    locks=[dict(kind='timing',wording='protect melody',part_id='p0001',voice_id=None,notes=None),dict(kind='pitches',wording='protect bass',part_id='p0003',voice_id=None,notes=None)]
    brief=dict(draft=dict(protected_material=locks))
    receipt=verify(raw,candidate,before,after,sel,target,ops,brief)
    assert path.read_bytes()==raw
    return raw,candidate,before,after,sel,target,ops,brief,receipt


@pytest.mark.parametrize('form',['repeated_notes','note_rest','rest_note'])
def test_subdivision_exact_timelines_and_later_correspondence(tmp_path,form):
    raw,candidate,before,after,*_,receipt=make(tmp_path,form=form)
    e=after.score.parts[1].events
    assert [(x.onset.fraction(),x.duration.fraction()) for x in e[:3]]==[(0,Fraction(1,2)),(Fraction(1,2),Fraction(1,2)),(1,1)]
    assert [x.kind for x in e[:2]]==({'repeated_notes':['pitched','pitched'],'note_rest':['pitched','rest'],'rest_note':['rest','pitched']}[form])
    assert next(r for r in receipt['correspondence'] if r['part_id']=='p0002' and r['original_ordinal']==12)['proposed_ordinals']==[13]
    assert all(receipt['gates'].values())


def test_redistribution_shifts_attack_and_silence(tmp_path):
    path=authored(tmp_path/'seed.musicxml');before=service.analyze_score(path)
    op=operation(before,'redistribute_durations',1,ds=(Fraction(3,2),Fraction(1,2)))
    data=make(tmp_path,[op]);events=data[3].score.parts[1].events
    assert [(x.onset.fraction(),x.duration.fraction()) for x in events[:3]]==[(0,Fraction(3,2)),(Fraction(3,2),Fraction(1,2)),(2,1)]


@pytest.mark.parametrize('mutate',[
    lambda x:x+b' ',lambda x:x.replace(b'Protected melody',b'Changed melody'),
    lambda x:x.replace(b'<duration>2</duration>',b'<duration>1</duration>',1),
    lambda x:x.replace(b'<type>eighth</type>',b'<type>quarter</type>',1),
    lambda x:x.replace(b'<voice>1</voice>',b'<voice>2</voice>',1),
    lambda x:x.replace(b'<beam number="1">begin</beam>',b'<beam number="1">end</beam>',1),
])
def test_tamper_refuses(tmp_path,mutate):
    raw,candidate,before,after,sel,target,ops,brief,receipt=make(tmp_path)
    bad=mutate(candidate);path=tmp_path/'bad.musicxml';path.write_bytes(bad)
    with pytest.raises((ViewerFailure,ValueError)):
        later=service.analyze_score(path);v=verify(raw,bad,before,later,sel,target,ops,brief)
        r,ms,ns=source_index(bad,later.score.model_dump(mode='json'));xml,_,required=aligned_range(r,ms,ns,[p.id for p in later.score.parts],'p0001',1,3,bounded=False)
        rendered=render(xml);verify_notation(bad,later.score,rendered['mei'],rendered['pages'],required,v)


@pytest.mark.parametrize('bad',['total','grid','stale','overlap','lock','meter','tie','tuplet','chord','attachment'])
def test_admission_and_locks_refuse(tmp_path,bad):
    p=authored(tmp_path/'source.musicxml');raw=p.read_bytes();before=service.analyze_score(p);op=operation(before)
    if bad=='total':op['durations'][0]=rt(1).model_dump(mode='json')
    if bad=='grid':op['durations']=[rt(Fraction(1,3)).model_dump(mode='json'),rt(Fraction(2,3)).model_dump(mode='json')]
    if bad=='stale':op['sources'][0]['expected_kind']='rest'
    if bad in ('meter','tie','tuplet','chord','attachment'):
        root=ET.fromstring(raw);n=root.findall('part')[1].find('measure/note')
        if bad=='meter':root.findall('part')[1].find('measure/attributes/time/beats').text='5'
        elif bad=='tie':ET.SubElement(n,'tie',type='start')
        elif bad=='tuplet':ET.SubElement(n,'time-modification')
        elif bad=='chord':ET.SubElement(n,'chord')
        else:ET.SubElement(ET.SubElement(n,'notations'),'fermata')
        raw=ET.tostring(root);p.write_bytes(raw)
    sel=dict(reference_part_id='p0001',start_measure_index=1,end_measure_index=3);target=dict(part_id='p0002',lane_mode='unique')
    with pytest.raises((ViewerFailure,ValueError)):
        before=service.analyze_score(p);ops=[op,op] if bad=='overlap' else [op]
        candidate=transform(raw,before,sel,target,ops)
        if bad=='lock':
            c=tmp_path/'candidate.musicxml';c.write_bytes(candidate)
            verify(raw,candidate,before,service.analyze_score(c),sel,target,ops,dict(draft=dict(protected_material=[dict(kind='timing',wording='locked',part_id='p0002',voice_id=None,notes=None)])))


@pytest.mark.parametrize('form',['repeated_notes','note_rest','rest_note'])
def test_actual_renderer_onsets_rests_beams(tmp_path,form):
    raw,data,before,after,sel,target,ops,brief,v=make(tmp_path,form=form)
    root,ms,ns=source_index(data,after.score.model_dump(mode='json'));xml,_,required=aligned_range(root,ms,ns,[p.id for p in after.score.parts],'p0001',1,3,bounded=False)
    rendered=render(xml)
    receipt=verify_notation(data,after.score,rendered['mei'],rendered['pages'],required,v)
    assert receipt['checked_rhythm_segments']==5


def bound_workspace(path,monkeypatch):
    from test_v02_m6_brief import prepare,draft
    from test_v02_editing_runtime import open_workspace,arguments
    from composerlint.passage_session import Store,worker as passage_worker
    from composerlint.expressive_brief import context
    from composerlint.tool_contracts import dispatch
    store=Store();query,snapshot,_=prepare(path,store);w,_=open_workspace(path)
    args=arguments(w,end=3,part='p0002');d=draft(query,snapshot)
    d.update(rhythm_policy='v02_m6_rhythmic_edit_v1',composer_request='Make the accompaniment more restless through rhythm; preserve melody, bass and meter.',allowed_families=['rhythm'],
        protected_material=[dict(kind='timing',wording='melody timing',part_id='p0001'),dict(kind='pitches',wording='melody notes',part_id='p0001'),dict(kind='timing',wording='bass timing',part_id='p0003')])
    direction=d['directions'][0];direction.update(title='Displace accompaniment returns',current_character='Quarter-note attacks and a quarter rest beneath sustained upper notes.',
        musical_reasoning='Shorten one opening attack into a rest, delay the second-measure return, and rearticulate a third-measure return beneath held melody notes.',
        possible_effect='Silence and displaced attacks could unsettle the accompaniment.',tradeoff='Less continuous support and an extra return may distract from the melody; sounding collections change during rests.',
        preserved_material='Melody, bass, pitch order/spelling, meter and untargeted voices; only listed accompaniment rhythm spans change.')
    owned=next(iter(store.sources.values()));bundle=next(iter(owned.passages.values())).bundle
    direction['evidence_refs']=[next(o.id for o in bundle.snapshot.observations if o.feature=='ensemble' and any(s.part_id=='p0002' for s in o.support))]
    report=service.analyze_score(path)
    direction['operations']=[operation(report,ordinal=1,form='note_rest'),operation(report,'redistribute_durations',6,ds=(Fraction(3,2),Fraction(1,2))),operation(report,ordinal=11)]
    ba={k:v for k,v in args.items() if k!='operation'};ba['draft']=d
    monkeypatch.setattr(passage_worker,'request',lambda r,**kw:context(store,r['arguments'],r['path'],r['options']))
    result,error=dispatch('prepare_expressive_edit_brief',ba);assert not error,result
    args.update(expected_workspace_revision=w['revision'],client_request_id='rhythm-candidate',operation=dict(kind='expressive_edit_batch',policy='v02_m6_rhythmic_edit_v1',brief_sha256=result.brief_sha256,direction_id=direction['direction_id']))
    return w,args,result,ba


def test_actual_bound_worker_preview_export_and_revalidation(tmp_path,monkeypatch):
    from composerlint.editing_runtime import editing
    from composerlint.viewer_runtime import runtime
    from composerlint.tool_contracts import dispatch
    from composerlint import editing_export as ex
    try:
        path=authored(tmp_path/'source.musicxml');raw=path.read_bytes();w,args,brief,ba=bound_workspace(path,monkeypatch)
        result,error=dispatch('prepare_score_edit',args);assert not error,result
        assert result.edit_policy_id=='v02_m6_rhythmic_edit_v1'
        p=w['proposal'];assert all(p['verification']['gates'].values())
        assert p['verification']['musical_comparison']['status']=='available'
        assert p['verification']['notation']['rhythm_onset_voice_rest_beams']
        assert len(p['manifest']['changed_note_ids'])==6
        (tmp_path/'output').mkdir()
        monkeypatch.setattr(ex,'EXPORT_ROOT',tmp_path/'output'/'exports')
        monkeypatch.setattr(ex,'confirm',lambda message:False)
        with pytest.raises(ViewerFailure):ex.approve_export(editing,w['id'],ex.binding(w))
        assert not list(ex.EXPORT_ROOT.glob('*.musicxml'))
        monkeypatch.setattr(ex,'confirm',lambda message:True) # engineering control, never human acceptance
        receipt=ex.approve_export(editing,w['id'],ex.binding(w))
        assert Path(receipt['output_path']).read_bytes()==Path(w['candidate_path']).read_bytes()
        assert path.read_bytes()==raw
        assert ex.approve_export(editing,w['id'],ex.binding(w))==receipt
        assert dispatch('prepare_score_edit',{**args,'client_request_id':'wrong-policy','expected_workspace_revision':w['revision'],'operation':{**args['operation'],'policy':'v02_m6_expressive_edit_v1'}})[1]
        args['expected_workspace_revision']=w['revision'];args['client_request_id']='new-rhythm'
        assert not dispatch('prepare_score_edit',args)[1]
    finally:editing.close();runtime.close()


@pytest.mark.parametrize('meter',[(2,4),(3,4),(4,4)])
def test_supported_meters_and_sixteenth_beams(tmp_path,meter):
    p=tmp_path/'source.musicxml'
    write_score(p,[{'name':'Protected','bars':[[('C5',meter[0])]]},
        {'name':'Lane','bars':[[('C4',Fraction(1,2)),('D4',Fraction(1,2))]+[('E4',1)]*(meter[0]-1)]}],meter=meter)
    before=service.analyze_score(p);raw=p.read_bytes();op=operation(before,ds=(Fraction(1,4),Fraction(1,4)))
    sel=dict(reference_part_id='p0001',start_measure_index=1,end_measure_index=1);target=dict(part_id='p0002',lane_mode='unique')
    data=transform(raw,before,sel,target,[op]);cp=tmp_path/'result.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    v=verify(raw,data,before,after,sel,target,[op],dict(draft=dict(protected_material=[])))
    root,ms,ns=source_index(data,after.score.model_dump(mode='json'));xml,_,required=aligned_range(root,ms,ns,['p0001','p0002'],'p0001',1,1,bounded=False)
    rendered=render(xml);assert verify_notation(data,after.score,rendered['mei'],rendered['pages'],required,v)['rhythm_onset_voice_rest_beams']
    assert data.count(b'<beam number="2">')==2


@pytest.mark.parametrize('tamper',['onset','voice','staff','rest','beam','svg'])
def test_rendered_receipt_tamper_refuses(tmp_path,tamper):
    _,data,_,after,_,_,_,_,v=make(tmp_path)
    root,ms,ns=source_index(data,after.score.model_dump(mode='json'));xml,_,required=aligned_range(root,ms,ns,[p.id for p in after.score.parts],'p0001',1,3,bounded=False)
    rendered=render(xml);mei=ET.fromstring(rendered['mei']);pages=rendered['pages'][:]
    ids={e.get('{http://www.w3.org/XML/1998/namespace}id'):e for e in mei.iter()};ident=next(k for k,r in required.items() if r['part_id']=='p0002' and r['source_note_ordinal']==2);n=ids[ident];parents={c:p for p in mei.iter() for c in p}
    if tamper=='onset':ids[next(k for k,r in required.items() if r['part_id']=='p0002' and r['source_note_ordinal']==1)].set('dur','16')
    if tamper=='voice':
        a=n
        while not a.tag.endswith('}layer'):a=parents[a]
        a.set('n','99')
    if tamper=='staff':
        a=n
        while not a.tag.endswith('}staff'):a=parents[a]
        a.set('n','99')
    if tamper=='rest':n.tag=n.tag.replace('note','rest')
    if tamper=='beam':parents[n].tag=parents[n].tag.replace('beam','bTrem')
    if tamper=='svg':pages=[p.replace('id="'+ident+'"','id="missing"') for p in pages]
    with pytest.raises(ViewerFailure):verify_notation(data,after.score,ET.tostring(mei,encoding='unicode'),pages,required,v)


def test_other_voice_and_backup_forward_preserved(tmp_path):
    p=authored(tmp_path/'source.musicxml');r=ET.parse(p).getroot();bar=r.findall('part')[1].find('measure')
    ET.SubElement(ET.SubElement(bar,'backup'),'duration').text='16'
    n=deepcopy(bar.find('note'));n.find('voice').text='2';n.find('duration').text='12';n.find('type').text='half';ET.SubElement(n,'dot');bar.append(n)
    f=ET.SubElement(bar,'forward');ET.SubElement(f,'duration').text='4';ET.SubElement(f,'voice').text='2'
    p.write_bytes(ET.tostring(r));before=service.analyze_score(p);raw=p.read_bytes();op=operation(before)
    sel=dict(reference_part_id='p0001',start_measure_index=1,end_measure_index=1);target=dict(part_id='p0002',lane_mode='lane',voice_id='p0002:v0001')
    data=transform(raw,before,sel,target,[op]);cp=tmp_path/'result.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    v=verify(raw,data,before,after,sel,target,[op],dict(draft=dict(protected_material=[dict(kind='timing',wording='other voice',part_id='p0002',voice_id='p0002:v0002',notes=None)])))
    assert ET.tostring(n) in data and ET.tostring(f) in data and b'<backup><duration>16</duration></backup>' in data
    assert v['constraints'][0]['checked_segments']==1
