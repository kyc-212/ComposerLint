"""Independent bounded creation timelines, preservation and actual workspace/export controls."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from composerlint import service
from composerlint.models import rt
from composerlint.editing_creation import transform, admit, POLICY
from composerlint.editing_creation_verify import verify
from composerlint.editing_rhythm_verify import verify_notation
from composerlint.viewer_source import ViewerFailure, source_index, aligned_range
from composerlint.editing_verify import verify_mei
from composerlint.renderers.verovio import render
from v02_advanced_helpers import write_score


def authored(path,bars=6):
    write_score(path,[{'name':'Melody','bars':[[('C4',1),('D4',1),('E4',2)]]+[[ (None,4) ]]*(bars-1)},
        {'name':'Bass','bars':[[('C3',4)]]*bars}],meter=(4,4))
    return path


def operations(report,start=2,end=2):
    p=report.score.parts[0];ops=[]
    for s in p.source_segments:
        if start<=s.location.measure_index<=end:
            ops.append(dict(family='note_creation',kind='replace_measure_rest',source=dict(source_note_ordinal=s.timing_provenance.source_note_ordinal,
                expected_kind='rest',expected_written=None,expected_onset=s.original_onset.model_dump(mode='json'),expected_duration=s.original_duration.model_dump(mode='json')),
                results=[dict(kind='pitched',written=dict(step=pitch,alteration=alter,octave=4),duration=rt(d).model_dump(mode='json'))
                    for pitch,alter,d in [('F',1,Fraction(1,2)),('G',0,Fraction(1,2)),('E',-1,1),('D',0,2)]]))
    return ops


def make(tmp_path,count=1,raw_mutator=None):
    path=authored(tmp_path/'source.musicxml')
    if raw_mutator:path.write_bytes(raw_mutator(path.read_bytes()))
    raw=path.read_bytes();before=service.analyze_score(path);sel=dict(reference_part_id='p0001',start_measure_index=2,end_measure_index=count+1)
    target=dict(part_id='p0001',lane_mode='unique');ops=operations(before,2,count+1)
    data=transform(raw,before,sel,target,ops);cp=tmp_path/'candidate.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    brief=dict(draft=dict(protected_material=[dict(kind='timing',wording='accompaniment',part_id='p0002')]))
    v=verify(raw,data,before,after,sel,target,ops,brief)
    assert path.read_bytes()==raw
    return raw,data,before,after,sel,target,ops,brief,v


@pytest.mark.parametrize('count',[1,2,4])
def test_exact_new_pitches_timelines_and_later_mapping(tmp_path,count):
    raw,data,before,after,sel,target,ops,brief,v=make(tmp_path,count)
    events=[e for e in after.score.parts[0].events if 4<=e.onset.fraction()<4*(count+1)]
    assert [(e.written_pitch.step,e.written_pitch.alteration,e.duration.fraction()) for e in events]==[('F',1,Fraction(1,2)),('G',0,Fraction(1,2)),('E',-1,1),('D',0,2)]*count
    assert [e.onset.fraction() for e in events]==[4*m+t for m in range(1,count+1) for t in (0,Fraction(1,2),1,2)]
    assert v['affected_measures']==list(range(2,count+2)) and all(v['gates'].values())
    later=next(r for r in v['correspondence'] if r['part_id']=='p0001' and r['original_ordinal']==8)
    assert later['proposed_ordinals']==[8+3*count]


def test_measure_rest_without_type_and_positioning(tmp_path):
    def mutate(raw):
        root=ET.fromstring(raw)
        for m in root.find('part').findall('measure')[1:]:
            n=m.find('note');n.set('default-x','42');n.set('default-y','-10');n.find('rest').set('measure','yes')
            if n.find('type') is not None:n.remove(n.find('type'))
        return ET.tostring(root)
    make(tmp_path,4,mutate)


@pytest.mark.parametrize('bad',['five','total','grid','nonempty','partial','attachment','meter','id','stale','duplicate','all','only_rests'])
def test_admission_refuses(tmp_path,bad):
    path=authored(tmp_path/'source.musicxml');raw=path.read_bytes();root=ET.fromstring(raw);n=root.find('part').findall('measure')[1].find('note')
    if bad=='nonempty':n.remove(n.find('rest'));pitch=ET.SubElement(n,'pitch');ET.SubElement(pitch,'step').text='C';ET.SubElement(pitch,'octave').text='4'
    if bad=='partial':n.find('duration').text='8';n.find('type').text='half'
    if bad=='attachment':ET.SubElement(n,'lyric')
    if bad=='id':n.set('id','linked-rest')
    if bad=='meter':root.find('part').find('measure/attributes/time/beats').text='5'
    path.write_bytes(ET.tostring(root));before=service.analyze_score(path);raw=path.read_bytes()
    end=6 if bad=='five' else 2;sel=dict(reference_part_id='p0001',start_measure_index=2,end_measure_index=end);target=dict(part_id='p0001',lane_mode='all' if bad=='all' else 'unique')
    ops=operations(before,2,end)
    if bad=='total':ops[0]['results'][0]['duration']=rt(1).model_dump(mode='json')
    if bad=='grid':ops[0]['results'][0]['duration']=rt(Fraction(1,3)).model_dump(mode='json')
    if bad=='stale':ops[0]['source']['expected_duration']=rt(3).model_dump(mode='json')
    if bad=='duplicate':ops*=2
    if bad=='only_rests':
        for r in ops[0]['results']:r.update(kind='rest',written=None)
    with pytest.raises((ViewerFailure,ValueError)):transform(raw,before,sel,target,ops)


@pytest.mark.parametrize('bad',['pitch','duration','voice','unrelated','extra','accidental'])
def test_independent_actual_byte_tamper_refuses(tmp_path,bad):
    raw,data,before,_,sel,target,ops,brief,_=make(tmp_path)
    if bad=='pitch':data=data.replace(b'<alter>1</alter>',b'<alter>0</alter>',1)
    if bad=='duration':data=data.replace(b'<type>eighth</type>',b'<type>quarter</type>',1)
    if bad=='voice':data=data.replace(b'<voice>1</voice>',b'<voice>2</voice>',1)
    if bad=='unrelated':data+=b' '
    if bad=='extra':data=data.replace(b'<accidental>sharp</accidental>',b'<accidental>sharp</accidental><lyric><text>changed</text></lyric>',1)
    if bad=='accidental':data=data.replace(b'<accidental>sharp</accidental>',b'<accidental>natural</accidental>',1)
    cp=tmp_path/'tampered.musicxml';cp.write_bytes(data)
    with pytest.raises((ViewerFailure,ValueError)):
        after=service.analyze_score(cp);verify(raw,data,before,after,sel,target,ops,brief)


@pytest.mark.parametrize('kind',['pitches','timing','register_bounds'])
def test_explicit_locks_refuse(tmp_path,kind):
    raw,data,before,after,sel,target,ops,brief,_=make(tmp_path)
    lock=dict(kind=kind,wording='locked',part_id='p0001')
    if kind=='register_bounds':lock.update(minimum_written_midi=0,maximum_written_midi=60)
    brief['draft']['protected_material']=[lock]
    with pytest.raises(ViewerFailure):verify(raw,data,before,after,sel,target,ops,brief)


def test_other_lane_and_backup_forward_bytes_preserved(tmp_path):
    def mutate(raw):
        root=ET.fromstring(raw);bar=root.find('part').findall('measure')[1]
        ET.SubElement(ET.SubElement(bar,'backup'),'duration').text='16'
        n=ET.SubElement(bar,'note');p=ET.SubElement(n,'pitch');ET.SubElement(p,'step').text='C';ET.SubElement(p,'octave').text='3'
        ET.SubElement(n,'duration').text='12';ET.SubElement(n,'voice').text='2';ET.SubElement(n,'type').text='half';ET.SubElement(n,'dot')
        f=ET.SubElement(bar,'forward');ET.SubElement(f,'duration').text='4';ET.SubElement(f,'voice').text='2'
        return ET.tostring(root)
    path=authored(tmp_path/'source.musicxml');path.write_bytes(mutate(path.read_bytes()));raw=path.read_bytes();before=service.analyze_score(path)
    sel=dict(reference_part_id='p0001',start_measure_index=2,end_measure_index=2);target=dict(part_id='p0001',lane_mode='lane',voice_id='p0001:v0001')
    ops=[op for op in operations(before) if op['source']['expected_kind']=='rest' and op['source']['expected_duration']==rt(4).model_dump(mode='json')]
    data=transform(raw,before,sel,target,ops);cp=tmp_path/'candidate.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    v=verify(raw,data,before,after,sel,target,ops,dict(draft=dict(protected_material=[dict(kind='timing',wording='other lane',part_id='p0001',voice_id='p0001:v0002')])))
    assert all(v['gates'].values()) and b'<backup><duration>16</duration></backup>' in data


def test_four_measures_sixty_four_new_nodes_and_sixteenth_beams(tmp_path):
    path=authored(tmp_path/'source.musicxml');before=service.analyze_score(path);raw=path.read_bytes()
    sel=dict(reference_part_id='p0001',start_measure_index=2,end_measure_index=5);target=dict(part_id='p0001',lane_mode='unique');ops=operations(before,2,5)
    for op in ops:op['results']=[dict(kind='pitched',written=dict(step='C' if i%2 else 'D',alteration=0,octave=4),duration=rt(Fraction(1,4)).model_dump(mode='json')) for i in range(16)]
    data=transform(raw,before,sel,target,ops);cp=tmp_path/'candidate.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    v=verify(raw,data,before,after,sel,target,ops,dict(draft=dict(protected_material=[])))
    assert sum(len(c['proposed_ordinals']) for c in v['changes'])==64 and data.count(b'<beam number="2">')==64


@pytest.mark.parametrize('beats',[2,3])
def test_creation_in_supported_shorter_meters(tmp_path,beats):
    path=tmp_path/'source.musicxml';write_score(path,[{'name':'Line','bars':[[('C4',1)]*beats,[(None,beats)]]}],meter=(beats,4))
    before=service.analyze_score(path);raw=path.read_bytes();sel=dict(reference_part_id='p0001',start_measure_index=2,end_measure_index=2);target=dict(part_id='p0001',lane_mode='unique');ops=operations(before)
    ops[0]['results']=[dict(kind='pitched',written=dict(step='D',alteration=0,octave=4),duration=rt(1).model_dump(mode='json')) for _ in range(beats)]
    data=transform(raw,before,sel,target,ops);cp=tmp_path/'candidate.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    assert verify(raw,data,before,after,sel,target,ops,dict(draft=dict(protected_material=[])))['gates']['measure_occupancy']


def test_actual_renderer_transposition_and_rest_result(tmp_path):
    def transpose(raw):
        root=ET.fromstring(raw);t=ET.SubElement(root.find('part').find('measure/attributes'),'transpose')
        ET.SubElement(t,'diatonic').text='-4';ET.SubElement(t,'chromatic').text='-7'
        return ET.tostring(root)
    raw,data,before,after,sel,target,ops,brief,v=make(tmp_path,1,transpose)
    ops[0]['results'][1].update(kind='rest',written=None)
    data=transform(raw,before,sel,target,ops);cp=tmp_path/'rests.musicxml';cp.write_bytes(data);after=service.analyze_score(cp)
    v=verify(raw,data,before,after,sel,target,ops,brief)
    root,ms,ns=source_index(data,after.score.model_dump(mode='json'));xml,_,required=aligned_range(root,ms,ns,['p0001','p0002'],'p0001',1,6,bounded=False)
    rendered=render(xml,width=1800);verify_mei(data,rendered['mei'],required,after.score)
    assert verify_notation(data,after.score,rendered['mei'],rendered['pages'],required,v)['checked_rhythm_segments']==4
    assert after.score.parts[0].events[3].sounding_pitch.chromatic_number==59 # written F-sharp4 sounds B3


def bound_workspace(path,monkeypatch=None,start=2,end=5):
    from test_v02_m6_brief import draft
    from test_v02_editing_runtime import open_workspace,arguments
    from composerlint.passage_session import Store,worker as passage_worker
    from composerlint.advanced_models import PassageArguments
    from composerlint.expressive_brief import context
    from composerlint.tool_contracts import dispatch
    store=Store();common=dict(path=str(path),execution_policy='source_bound_passage_v1')
    source=store.run(PassageArguments(**common));query=dict(execution_policy='source_bound_passage_v1',expected_source_snapshot=source.source_snapshot_id,
        expression_policy='v02_m6_expression_v1',features=['notes','sonorities','melody','ensemble'],
        scope=dict(kind='measure_range',reference_part_id='p0001',start_measure=max(1,start-3),end_measure=end))
    overview=store.run(PassageArguments(**common,**{k:v for k,v in query.items() if k!='execution_policy'},section='overview'))
    w,_=open_workspace(path);args=arguments(w,end=end,part='p0001');args['selection']['start_measure_index']=start
    d=draft(query,overview.passage_snapshot_id);d.update(creation_policy=POLICY,allowed_families=['note_creation'],protected_material=[],
        composer_request='Compose a melodic continuation into the selected completely resting measures.')
    bundle=next(iter(next(iter(store.sources.values())).passages.values())).bundle
    rel=next(o for o in bundle.snapshot.observations if o.feature=='melody' and o.fact.kind!='note')
    direction=d['directions'][0];direction.update(direction_id='continue',title='Continue the melodic line',evidence_refs=[rel.id],
        current_character='The preceding line rises by step.',musical_reasoning='Continue the contour, then settle with longer notes.',possible_effect='May suggest a short answering phrase.',
        tradeoff='A more settled response reduces contrast.',preserved_material='All original pitched material and accompaniment.',
        operations=operations(service.analyze_score(path),start,end))
    ba={k:v for k,v in args.items() if k!='operation'};ba['draft']=d
    if monkeypatch:
        monkeypatch.setattr(passage_worker,'request',lambda r,**kw:context(store,r['arguments'],r['path'],r['options']))
    result,error=dispatch('prepare_expressive_edit_brief',ba);assert not error,result
    args.update(expected_workspace_revision=w['revision'],client_request_id='create-candidate',operation=dict(kind='expressive_edit_batch',policy=POLICY,brief_sha256=result.brief_sha256,direction_id='continue'))
    return w,args,result,ba


def test_four_measure_actual_worker_export_and_staged_checks(tmp_path,monkeypatch):
    from composerlint.editing_runtime import editing
    from composerlint.viewer_runtime import runtime
    from composerlint.tool_contracts import dispatch
    from composerlint import editing_export as ex
    try:
        path=authored(tmp_path/'source.musicxml');raw=path.read_bytes();w,args,brief,ba=bound_workspace(path,monkeypatch)
        assert brief.brief.brief_schema_version==3
        result,error=dispatch('prepare_score_edit',args);assert not error,result
        p=w['proposal'];assert all(p['verification']['gates'].values())
        assert len(p['manifest']['changed_note_ids'])==16 and len(p['manifest']['original_changed_note_ids'])==4
        assert p['verification']['musical_comparison']['status']=='available'
        monkeypatch.setattr(ex,'EXPORT_ROOT',tmp_path/'output'/'exports');monkeypatch.setattr(ex,'confirm',lambda _:False)
        with pytest.raises(ViewerFailure):ex.approve_export(editing,w['id'],ex.binding(w))
        monkeypatch.setattr(ex,'confirm',lambda _:True) # engineering control only
        receipt=ex.approve_export(editing,w['id'],ex.binding(w))
        assert Path(receipt['output_path']).read_bytes()==Path(w['candidate_path']).read_bytes() and path.read_bytes()==raw
        assert ex.approve_export(editing,w['id'],ex.binding(w))==receipt
        assert dispatch('prepare_score_edit',{**args,'expected_workspace_revision':w['revision'],'client_request_id':'policy-mismatch',
            'operation':{**args['operation'],'policy':'v02_m6_rhythmic_edit_v1'}})[1]
        # New policy uses the same binding invalidation as existing human export.
        binding=ex.binding(w);w['entry']['revision']+=1
        with pytest.raises(ViewerFailure):ex.approve_export(editing,w['id'],binding)
    finally:editing.close();runtime.close()
