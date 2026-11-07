"""Authored M6.3 actual-byte, renderer and lifecycle controls; no repertoire campaign."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import pytest
from composerlint import service
from composerlint.editing_source import resolve
from composerlint.editing_expression import transform
from composerlint.editing_expression_verify import verify
from composerlint.viewer_source import ViewerFailure
from composerlint.review import digest
from v02_advanced_helpers import write_score


def authored(path, transpose=None):
    write_score(path,[{'name':'Protected line','bars':[[('C5',2),('D5',1)]]*4},
                      {'name':'Accompaniment','bars':[[('C4',1),(None,1),('G4',1)]]*4}],meter=(3,4))
    root=ET.parse(path).getroot();part=root.findall('part')[1]
    d=ET.Element('direction');t=ET.SubElement(d,'direction-type');ET.SubElement(ET.SubElement(t,'dynamics'),'p')
    part.find('measure').insert(1,d)
    if transpose:
        t=ET.SubElement(part.find('measure/attributes'),'transpose')
        ET.SubElement(t,'diatonic').text=str(transpose[0]);ET.SubElement(t,'chromatic').text=str(transpose[1])
    path.write_bytes(ET.tostring(root));return path


def note(i,step='G',octave=4):
    return dict(source_note_ordinal=i,expected_written=dict(step=step,alteration=0,octave=octave))


def pitch(i,step='A',octave=4,domain='written'):
    return dict(family='pitch_register',change=dict(kind='replace',notes=[note(i)],pitch_domain=domain,
        destination=dict(step=step,alteration=0,octave=octave),diatonic_steps=None,semitones=None))


def art(i=3,symbol='accent',action='set',replaces=None):
    return dict(family='articulation',action=action,symbol=symbol,notes=[note(i)],replaces=replaces)


def dynamic(i=3,symbol='f',action='set'):
    return dict(family='dynamics',action=action,symbol=symbol,anchor=note(i))


def candidate(path,tmp_path,ops,locks=None,start=1,end=3):
    raw=path.read_bytes();before=service.analyze_score(path)
    sel=dict(start_measure_index=start,end_measure_index=end,reference_part_id='p0001')
    spec=dict(part_id='p0002',lane_mode='unique');target=resolve(raw,before,sel,spec)
    actual=transform(raw,before,target,ops);cp=tmp_path/'candidate.musicxml';cp.write_bytes(actual)
    after=service.analyze_score(cp)
    brief=dict(draft=dict(protected_material=locks or []))
    receipt=verify(raw,actual,before,after,sel,spec,ops,brief)
    assert path.read_bytes()==raw
    return actual,receipt,(raw,before,after,sel,spec,ops,brief)


def test_coordinated_pitch_and_protected_occupants(tmp_path):
    path=authored(tmp_path/'source.musicxml')
    locks=[dict(kind='pitches',wording='preserve melody',part_id='p0001',voice_id=None,notes=None),
           dict(kind='timing',wording='preserve rhythm',part_id='p0002',voice_id=None,notes=None)]
    actual,v,_=candidate(path,tmp_path,[pitch(3),pitch(6,'B'),pitch(9,'F')],locks)
    assert [c['after_written']['step'] for c in v['changes']]==list('ABF')
    assert all(v['gates'].values()) and len(v['constraints'])==2
    assert actual.count(b'<step>G</step>')==1


@pytest.mark.parametrize('trans,expected',[((-1,-2),'B'),((-4,-7),'E')])
def test_audited_sounding_replacement(tmp_path,trans,expected):
    path=authored(tmp_path/'source.musicxml',trans)
    actual,v,_=candidate(path,tmp_path,[pitch(3,'A',4,'sounding')])
    c=v['changes'][0]
    assert c['after_sounding']['step']=='A' and c['after_written']['step']==expected


@pytest.mark.parametrize('symbol',['staccato','tenuto','accent'])
def test_articulation_insertion_preserves_duration(tmp_path,symbol):
    p=authored(tmp_path/'source.musicxml');_,v,_=candidate(p,tmp_path,[art(symbol=symbol)])
    assert v['changes']==[dict(v['changes'][0],after=symbol)]
    assert v['gates']['rhythm_structure']


def test_pitch_and_articulation_same_note(tmp_path):
    p=authored(tmp_path/'source.musicxml');_,v,_=candidate(p,tmp_path,[pitch(3),art()])
    assert len(v['changes'])==2


@pytest.mark.parametrize('action,new,replaces',[('set','tenuto','accent'),('remove','accent',None)])
def test_articulation_replace_remove_preserve_other_notation(tmp_path,action,new,replaces):
    p=authored(tmp_path/'source.musicxml');r=ET.parse(p).getroot();n=r.findall('part')[1].findall('measure/note')[2]
    ns=ET.SubElement(n,'notations');ET.SubElement(ns,'fermata');ET.SubElement(ET.SubElement(ns,'articulations'),'accent')
    p.write_bytes(ET.tostring(r));actual,v,_=candidate(p,tmp_path,[art(symbol=new,action=action,replaces=replaces)])
    assert b'<fermata />' in actual
    assert v['changes'][0]['after']==(new if action=='set' else None)


@pytest.mark.parametrize('symbol',['pp','p','mp','mf','f','ff'])
def test_dynamic_local_change_restores_effective_boundary(tmp_path,symbol):
    p=authored(tmp_path/'source.musicxml')
    if symbol=='p':p.write_bytes(p.read_bytes().replace(b'<p />',b'<mf />'))
    actual,v,_=candidate(p,tmp_path,[dynamic(symbol=symbol)])
    changes=[c for c in v['changes'] if c['kind'].startswith('dynamic')]
    assert changes[0]['after']==symbol and changes[1]['kind']=='dynamic_restoration'
    assert changes[1]['absolute_offset']=='9'


def test_remove_dynamic_and_end_of_score(tmp_path):
    p=authored(tmp_path/'source.musicxml');r=ET.parse(p).getroot();n=r.findall('part')[1].findall('measure')[1]
    d=ET.Element('direction');ET.SubElement(ET.SubElement(d,'direction-type'),'dynamics').append(ET.Element('f'))
    n.insert(0,d);p.write_bytes(ET.tostring(r))
    op=dynamic(4,'f','remove');op['anchor']=note(4,'C')
    _,v,_=candidate(p,tmp_path,[op],start=2,end=3)
    assert v['changes'][0]['after'] is None and v['changes'][1]['after']=='f'
    _,v,_=candidate(p,tmp_path,[dynamic(12,'ff')],start=4,end=4)
    assert not any(c.get('kind')=='dynamic_restoration' for c in v['changes'])


@pytest.mark.parametrize('mutate',[
    lambda x:x+b' ',lambda x:x.replace(b'Protected line',b'Altered line'),
    lambda x:x.replace(b'<duration>8</duration>',b'<duration>4</duration>',1),
    lambda x:x.replace(b'<accent/>',b'<tenuto/>'),
    lambda x:x.replace(b'<f/>',b'<ff/>'),
    lambda x:x.replace(b'<p/></dynamics>',b'<ff/></dynamics>'),
])
def test_independent_actual_candidate_tamper_refuses(tmp_path,mutate):
    p=authored(tmp_path/'source.musicxml');actual,_,args=candidate(p,tmp_path,[pitch(3),art(6),dynamic(9)])
    raw,before,_,sel,spec,ops,brief=args;bad=mutate(actual);cp=tmp_path/'bad.musicxml';cp.write_bytes(bad)
    with pytest.raises(ViewerFailure):verify(raw,bad,before,service.analyze_score(cp),sel,spec,ops,brief)


def test_unknown_dynamic_baseline_and_tied_articulation_refuse(tmp_path):
    p=authored(tmp_path/'source.musicxml');p.write_bytes(p.read_bytes().replace(b'<p />',b'<other-dynamics>unknown</other-dynamics>'))
    r=service.analyze_score(p);target=resolve(p.read_bytes(),r,dict(start_measure_index=1,end_measure_index=3),dict(part_id='p0002',lane_mode='unique'))
    with pytest.raises(ViewerFailure,match='simple'):transform(p.read_bytes(),r,target,[dynamic()])
    p=Path(__file__).parent/'fixtures/v02_m2/ties.musicxml';raw=p.read_bytes();r=service.analyze_score(p)
    target=resolve(raw,r,dict(start_measure_index=1,end_measure_index=2),dict(part_id='p0001',lane_mode='unique'))
    row=target['notes'][0];op=art(row['ordinal']);op['notes']=[dict(source_note_ordinal=row['ordinal'],expected_written={k:row['written'][k] for k in ('step','alteration','octave')})]
    with pytest.raises(ViewerFailure,match='untied'):transform(raw,r,target,[op])


def bound_workspace(path,monkeypatch):
    from test_v02_m6_brief import prepare,draft
    from test_v02_editing_runtime import open_workspace,arguments
    from composerlint.passage_session import Store,worker as passage_worker
    from composerlint.expressive_brief import context
    from composerlint.tool_contracts import dispatch
    store=Store();q,s,o=prepare(path,store);w,_=open_workspace(path)
    args=arguments(w,end=3,part='p0002');d=draft(q,s)
    owned=next(iter(store.sources.values()));bundle=next(iter(owned.passages.values())).bundle
    d['directions'][0]['evidence_refs']=[next(ob.id for ob in bundle.snapshot.observations if ob.feature=='ensemble')]
    d['allowed_families']=['pitch_register','dynamics','articulation']
    d['directions'][0]['operations']=[pitch(3),pitch(6,'B'),art(9),dynamic(9)]
    second=deepcopy(d['directions'][0]);second['direction_id']='alternative'
    second['operations']=[pitch(3,'F')];d['directions'].append(second)
    ba={k:v for k,v in args.items() if k!='operation'};ba['draft']=d
    monkeypatch.setattr(passage_worker,'request',lambda r,**kw:context(store,r['arguments'],r['path'],r['options']))
    result,error=dispatch('prepare_expressive_edit_brief',ba);assert not error,result
    args.update(expected_workspace_revision=w['revision'],client_request_id='candidate-1',operation=dict(
        kind='expressive_edit_batch',policy='v02_m6_expressive_edit_v1',brief_sha256=result.brief_sha256,direction_id='accent_frame'))
    return w,args,result,ba


def test_actual_candidate_worker_renderer_comparison_and_lifecycle(tmp_path,monkeypatch):
    from composerlint.editing_runtime import editing
    from composerlint.viewer_runtime import runtime
    from composerlint.tool_contracts import dispatch
    from composerlint.editing_models import PrepareArguments,ProposalArguments
    path=authored(tmp_path/'source.musicxml');raw=path.read_bytes()
    try:
        w,args,brief,ba=bound_workspace(path,monkeypatch)
        result,error=dispatch('prepare_score_edit',args);assert not error,result
        p=w['proposal'];assert result.edit_policy_id=='v02_m6_expressive_edit_v1'
        assert all(p['verification']['gates'].values()) and p['preview_status']=='ready'
        assert p['verification']['notation']['marking_correspondence']
        assert p['verification']['notation']['original_markings']['marking_correspondence']
        assert len(p['manifest']['changed_marking_ids'])==3
        assert len(p['manifest']['changed_note_ids'])==3
        assert len(p['manifest']['scope']['part_ids'])==2
        comparison=p['verification']['musical_comparison'];assert comparison['status']=='available',comparison
        assert comparison['identities']['original']['source_sha256']==w['source_sha256']
        assert comparison['identities']['proposed']['source_sha256']==p['candidate_sha256']
        assert p['candidate_sha256']!=w['source_sha256']
        assert dispatch('prepare_score_edit',args)[0].data==result.data
        import composerlint.editing_runtime as module
        with monkeypatch.context() as guard:
            guard.setattr(module,'worker',lambda *a,**kw:pytest.fail('Invalid binding reached the candidate worker'))
            for changed in [dict(expected_workspace_revision=0),dict(expected_source_sha256='0'*64),
                    dict(operation={**args['operation'],'brief_sha256':'0'*64}),
                    dict(operation={**args['operation'],'direction_id':'unknown'}),
                    dict(target=dict(part_id='p0001')),
                    dict(selection={**args['selection'],'end_measure_index':2})]:
                rejected,error=dispatch('prepare_score_edit',{**args,'expected_workspace_revision':w['revision'],
                    'client_request_id':'invalid-'+str(len(str(changed))),**changed})
                assert error,rejected
                assert w['proposal'] is p
        bound=dict(workspace_id=w['id'],proposal_id=p['proposal_id'],expected_source_sha256=w['source_sha256'],
            expected_candidate_sha256=p['candidate_sha256'],expected_verification_sha256=p['verification_sha256'],section='musical_comparison')
        rows=[];cursor=None
        while True:
            page=editing.get(ProposalArguments(**bound,cursor=cursor));rows+=page.data['items'];cursor=page.data['next_cursor']
            if cursor is None:break
        assert len(rows)==1+len(comparison['rows'])
        # A discussion change invalidates the old proposal, then materializes a
        # different direction from immutable Original, never from prior music.
        choose={k:v for k,v in ba.items() if k not in ('draft','client_request_id')}
        choose.update(action='choose',expected_workspace_revision=w['revision'],expected_brief_sha256=brief.brief_sha256,
            direction_id='alternative',client_request_id='choose-alternative')
        new,error=dispatch('prepare_expressive_edit_brief',choose);assert not error,new
        assert w['proposal'] is None
        assert dispatch('get_edit_proposal',bound)[1]
        args.update(expected_workspace_revision=w['revision'],client_request_id='candidate-2',operation={**args['operation'],
            'brief_sha256':new.brief_sha256,'direction_id':'alternative'})
        second,error=dispatch('prepare_score_edit',args);assert not error,second
        assert len(w['proposal']['changes'])==1 and w['proposal']['changes'][0]['before_written']['step']=='G'
        assert b'<accent/>' not in Path(w['candidate_path']).read_bytes()
        assert path.read_bytes()==raw
        Path(w['candidate_path']).write_bytes(Path(w['candidate_path']).read_bytes()+b' ')
        assert dispatch('prepare_score_edit',args)[1]  # Even an identical retry rechecks actual bytes.
    finally:editing.close();runtime.close()


def test_complete_logical_tie_and_fragment_refusal(tmp_path):
    p=Path(__file__).parent/'fixtures/v02_m2/ties.musicxml';raw=p.read_bytes();before=service.analyze_score(p)
    sel=dict(start_measure_index=1,end_measure_index=2,reference_part_id='p0001');spec=dict(part_id='p0001',lane_mode='unique')
    target=resolve(raw,before,sel,spec)
    refs=[dict(source_note_ordinal=r['ordinal'],expected_written={k:r['written'][k] for k in ('step','alteration','octave')}) for r in target['notes']]
    op=dict(family='pitch_register',change=dict(kind='transpose',notes=refs,pitch_domain='written',destination=None,diatonic_steps=1,semitones=2))
    actual=transform(raw,before,target,[op]);cp=tmp_path/'tied.musicxml';cp.write_bytes(actual)
    v=verify(raw,actual,before,service.analyze_score(cp),sel,spec,[op],dict(draft=dict(protected_material=[])))
    assert len(v['changes'])==2 and len({r['event_id'] for r in v['changes']})==1
    incomplete=deepcopy(op);incomplete['change']['notes']=refs[:1]
    with pytest.raises(ViewerFailure,match='every segment'):transform(raw,before,target,[incomplete])


@pytest.mark.parametrize('kind',['pitches','register_bounds','articulations','dynamics'])
def test_actual_candidate_hard_lock_conflict(tmp_path,kind):
    p=authored(tmp_path/'source.musicxml')
    lock=dict(kind=kind,wording='confirmed hard lock',part_id='p0002',voice_id=None,notes=None,
        minimum_written_midi=0,maximum_written_midi=60)
    ops=[art()] if kind=='articulations' else [dynamic()] if kind=='dynamics' else [pitch(3)]
    with pytest.raises(ViewerFailure,match='Protected'):candidate(p,tmp_path,ops,[lock])


def test_dynamic_unknown_baseline_multivoice_and_scope_ambiguity(tmp_path):
    p=authored(tmp_path/'source.musicxml');raw=p.read_bytes()
    for variant in [raw.replace(b'<direction><direction-type><dynamics><p /></dynamics></direction-type></direction>',b''),
                    raw.replace(b'<dynamics><p /></dynamics>',b'<dynamics><p /></dynamics><wedge type="crescendo"/>'),
                    raw.replace(b'<dynamics><p /></dynamics>',b'<dynamics><p /></dynamics><words>unrelated</words>')]:
        p.write_bytes(variant);before=service.analyze_score(p)
        target=resolve(variant,before,dict(start_measure_index=1,end_measure_index=3),dict(part_id='p0002',lane_mode='unique'))
        with pytest.raises(ViewerFailure):transform(variant,before,target,[dynamic()])
    from test_v02_m6_brief import source
    source(p,lanes=True);before=service.analyze_score(p)
    target=resolve(p.read_bytes(),before,dict(start_measure_index=1,end_measure_index=3),dict(part_id='p0002',lane_mode='lane',voice_id='p0002:v0001'))
    with pytest.raises(ViewerFailure,match='single-voice'):transform(p.read_bytes(),before,target,[dynamic()])


@pytest.fixture(scope='module')
def marking_render(tmp_path_factory):
    from composerlint.viewer_source import source_index,aligned_range
    from composerlint.renderers.verovio import render
    folder=tmp_path_factory.mktemp('m6-markings');p=authored(folder/'source.musicxml')
    ops=[art(3,'accent'),art(6,'tenuto'),art(9,'staccato')]
    for i,symbol in [(3,'pp'),(4,'mp'),(6,'mf'),(7,'f'),(9,'ff'),(10,'p')]:
        o=dynamic(i,symbol)
        if i in (4,7,10):o['anchor']=note(i,'C')
        ops.append(o)
    data,v,args=candidate(p,folder,ops,end=4);after=args[2]
    root,measures,notes=source_index(data,after.score.model_dump(mode='json'))
    xml,selected,required=aligned_range(root,measures,notes,['p0001','p0002'],'p0001',1,4,bounded=False)
    rendered=render(xml)
    return data,after.score,rendered,required,[{**o,'_part_id':'p0002'} for o in ops]


def test_all_supported_marking_symbols_have_actual_render_correspondence(marking_render):
    from composerlint.editing_expression_verify import verify_notation
    data,score,rendered,notes,ops=marking_render
    r=verify_notation(data,score,rendered['mei'],rendered['pages'],notes,ops)
    assert r['checked_markings']==10 and r['marking_correspondence']
    assert {x['symbol'] for x in r['correspondence'] if x['kind']=='dynamic'}=={'pp','p','mp','mf','f','ff'}


@pytest.mark.parametrize('tamper',['art_symbol','art_missing','dynamic_symbol','dynamic_staff','dynamic_beat','svg_group'])
def test_independent_marking_receipt_refuses_missing_or_incorrect_correspondence(marking_render,tamper):
    from composerlint.editing_expression_verify import verify_notation
    data,score,rendered,notes,ops=marking_render;root=ET.fromstring(rendered['mei']);pages=rendered['pages'][:]
    artnode=next(e for e in root.iter() if e.tag.endswith('}artic'))
    dynnode=next(e for e in root.iter() if e.tag.endswith('}dynam'))
    if tamper=='art_symbol':artnode.set('artic','ten')
    if tamper=='art_missing':artnode.tag='{http://www.music-encoding.org/ns/mei}removed'
    if tamper=='dynamic_symbol':dynnode.text='ff'
    if tamper=='dynamic_staff':dynnode.set('staff','99')
    if tamper=='dynamic_beat':dynnode.set('tstamp','2')
    if tamper=='svg_group':
        ident=artnode.get('{http://www.w3.org/XML/1998/namespace}id')
        pages=[s.replace('id="'+ident+'"','id="missing"') for s in pages]
    with pytest.raises(ViewerFailure):verify_notation(data,score,ET.tostring(root,encoding='unicode'),pages,notes,ops)


def test_incomplete_contextual_analysis_is_explicitly_unavailable(tmp_path,monkeypatch):
    import composerlint.passage as passage
    from composerlint.advanced_models import AdvancedFailure
    from composerlint.editing_expression_verify import musical_comparison
    def unavailable(*a,**kw):raise AdvancedFailure('analysis_limit_exceeded','Authored capacity refusal')
    monkeypatch.setattr(passage,'prepare',unavailable)
    result=musical_comparison(tmp_path/'original',tmp_path/'candidate',None,{})
    assert result['status']=='unavailable' and result['rows']==[] and result['identities']=={}


def test_malformed_xml_and_incompatible_articulations_refuse(tmp_path):
    p=authored(tmp_path/'source.musicxml');raw=p.read_bytes();r=service.analyze_score(p)
    sel=dict(start_measure_index=1,end_measure_index=3,reference_part_id='p0001');spec=dict(part_id='p0002',lane_mode='unique')
    with pytest.raises(ViewerFailure):verify(raw,b'<score-partwise>',r,r,sel,spec,[art()],dict(draft=dict(protected_material=[])))
    root=ET.fromstring(raw);n=root.findall('part')[1].findall('measure/note')[2]
    a=ET.SubElement(ET.SubElement(n,'notations'),'articulations');ET.SubElement(a,'tenuto');ET.SubElement(a,'staccato')
    p.write_bytes(ET.tostring(root));r=service.analyze_score(p);t=resolve(p.read_bytes(),r,sel,spec)
    with pytest.raises(ViewerFailure,match='combined'):transform(p.read_bytes(),r,t,[art()])


def test_candidate_preserves_untouched_grace_outside_edit_window(tmp_path):
    from composerlint.viewer_source import source_index,aligned_range
    from composerlint.renderers.verovio import render
    from composerlint.editing_verify import verify_mei
    p=authored(tmp_path/'source.musicxml');root=ET.parse(p).getroot();bar=root.findall('part')[1].findall('measure')[3]
    grace=ET.fromstring('<note><grace/><pitch><step>E</step><alter>-1</alter><octave>4</octave></pitch><voice>1</voice><type>16th</type></note>')
    bar.insert(0,grace);p.write_bytes(ET.tostring(root))
    data,receipt,args=candidate(p,tmp_path,[pitch(3)])
    assert ET.tostring(grace) in data and receipt['gates']['rhythm_structure']
    after=args[2];r,measures,notes=source_index(data,after.score.model_dump(mode='json'))
    xml,_,required=aligned_range(r,measures,notes,['p0001','p0002'],'p0001',1,4,bounded=False)
    rendered=render(xml);checked=verify_mei(data,rendered['mei'],required,after.score)
    assert checked['checked_grace_segments']==1
