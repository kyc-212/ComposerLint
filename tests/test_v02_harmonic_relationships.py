"""H1 controls with independently authored pitches, attacks and tie semantics."""
from copy import deepcopy
import xml.etree.ElementTree as ET
import pytest,jsonschema
from composerlint.engine.evaluation import compute
from composerlint.evaluation_models import EvaluationArguments,EvaluationFailure
from composerlint.evaluation_review import EvaluationGraph,project_evaluation,validate_evaluation
from composerlint.review import digest
from composerlint.tool_contracts import TOOLS,input_schema,output_schema
from v02_advanced_helpers import write_score
from v02_evaluation_helpers import query_for
from test_v02_relationship_evaluation import source,query as stage1_query

POLICY='v02_relationship_evaluation_v2'


def material(tmp_path,variant='tied'):
    p=tmp_path/(variant+'.musicxml')
    upper=[['F4'],['Gb4']]
    if variant=='whole_tone':upper=[['F4'],['G4']]
    if variant=='overlap':upper=[['F4','Ab4'],['Gb4','A4']]
    write_score(p,[{'bars':[[ (x,2)] for x in upper]},
                   {'bars':[[('Db3',2)],[('Eb3' if variant=='bass_move' else 'Db3',2)]]},
                   {'bars':[[('Ab3',2)],[('Ab4' if variant=='octave' else 'Ab3',2)]]}],meter=(2,4))
    root=ET.fromstring(p.read_bytes())
    bass=root.findall('part')[1].findall('measure/note')
    for n in bass:ET.SubElement(n,'staff').text='2'
    if variant not in ('reattack','octave','bass_move'):
        ET.SubElement(bass[0],'tie',type='start');ET.SubElement(bass[1],'tie',type='stop')
    if variant=='unknown':
        bass[0].find('tie').set('type','stop')
    if variant=='transpose':
        tr=ET.SubElement(root.find('part/measure/attributes'),'transpose')
        ET.SubElement(tr,'diatonic').text='-1';ET.SubElement(tr,'chromatic').text='-2'
    if variant=='grace':
        measure=root.find('part/measure');note=deepcopy(measure.find('note'))
        note.remove(note.find('duration'));ET.SubElement(note,'grace')
        measure.append(note)
    p.write_bytes(ET.tostring(root))
    return p


def evaluate(p,**extra):
    return compute(p,query=query_for(p,(1,2),evaluation_policy=POLICY,assessment_focus=['harmonic_direction'],
                                    analysis_options={'neighbor_measures':0,'pattern_min_attacks':3,'pattern_max_attacks':3},**extra))


def row(b):return next(r for r in b.snapshot.relationships if r.criterion=='sonority_continuity')


def events(b):return {e.id:e for p in b.advanced.report.score.parts for e in p.events}


def test_held_tie_rearticulation_chromatic_voice_and_complete_collections(tmp_path):
    p=material(tmp_path);before=p.read_bytes();b=evaluate(p);r=row(b);f=r.facts;es=events(b)
    assert b.snapshot.versions['harmonic_relationships']=='v02_harmonic_relationships_h1_v1'
    assert f['full_context_verified'] and f['boundary']=={'numerator':2,'denominator':1}
    assert [[(m['pitch']['step'],m['pitch']['alteration'],m['pitch']['octave']) for m in s['members']]
            for s in f['sonorities']]==[[('F',0,4),('D',-1,3),('A',-1,3)],[('G',-1,4),('D',-1,3),('A',-1,3)]]
    assert len(f['held_event_ids'])==1
    held=es[f['held_event_ids'][0]]
    assert held.voice_id=='p0002:v0001' and held.duration.fraction()==4 and len(held.source_segment_ids)==2
    support=next(s for s in r.support if s.event_id==held.id)
    assert support.staff=='2' and support.measure_ids==['p0002:m0001','p0002:m0002']
    assert [es[e].voice_id for e in f['reattacked_event_ids']]==['p0003:v0001']
    assert not set(f['held_event_ids'])&set(f['reattacked_event_ids'])
    assert {es[e].voice_id for e in f['entering_event_ids']}=={'p0001:v0001','p0003:v0001'}
    assert sorted(m['interval']['semitones'] for m in f['voice_movements'])==[0,1]
    assert f['bass_movement']['semitones']==0 and f['bass_movement']['from_event_ids']==[held.id]
    assert f['sonorities'][1]['classification']=='unclassified'
    assert 'not evidence of nonfunctional' in r.interpretation and r.advice is None
    assert p.read_bytes()==before
    g=EvaluationGraph(b);closure=g.closure(r.id);refs={x.record_ref for x in closure}
    assert all(set(g.links[x.record_ref])<=refs for x in closure)
    assert {'event:'+e for e in es}<=refs
    assert {'transpose:'+e for e in es}<=refs


@pytest.mark.parametrize('variant',['reattack','octave'])
def test_pitch_class_or_repeated_pitch_does_not_prove_sustain(tmp_path,variant):
    b=evaluate(material(tmp_path,variant));f=row(b).facts;es=events(b)
    assert not f['held_event_ids']
    assert 8 in f['common_chromatic_classes']  # Ab remains a class even at a new octave.
    repeated={es[e].voice_id for e in f['reattacked_event_ids']}
    assert ('p0003:v0001' in repeated)==(variant=='reattack')
    assert 'p0002:v0001' in repeated
    if variant=='octave':assert any(m['interval']['semitones']==12 for m in f['voice_movements'])


def test_chordal_source_lane_withholds_invented_semitone_continuations(tmp_path):
    b=evaluate(material(tmp_path,'overlap'));r=row(b);f=r.facts
    assert f['full_context_verified'] and len(f['sonorities'][0]['members'])==4
    assert not any(m['voice_id']=='p0001:v0001' for m in f['voice_movements'])
    assert any(v['voice_id']=='p0001:v0001' and 'ambiguous_overlap_or_chord' in v['reasons'] for v in f['unresolved_voices'])
    assert f['bass_movement']['semitones']==0
    assert any(d.cause=='musical_ambiguity' and 'ambiguous_source_voice_continuity' in d.reasons for d in b.snapshot.diagnostics)
    assert r.state=='qualified_reading'


def test_whole_tone_is_not_semitone_and_bass_motion_is_separate(tmp_path):
    b=evaluate(material(tmp_path,'whole_tone'));r=row(b)
    assert sorted(m['interval']['semitones'] for m in r.facts['voice_movements'])==[0,2]
    assert '(+1 semitone)' not in r.interpretation
    b=evaluate(material(tmp_path,'bass_move'));f=row(b).facts
    assert f['bass_movement']['semitones']==2
    assert sorted(m['interval']['semitones'] for m in f['voice_movements'])==[0,1,2]


@pytest.mark.parametrize('selected',['full','upper_only'])
def test_unknown_lower_occupant_blocks_bass_even_outside_selected_voices(tmp_path,selected):
    p=material(tmp_path,'unknown');q=query_for(p,(1,2),evaluation_policy=POLICY,assessment_focus=['harmonic_direction'])
    if selected=='upper_only':q['scope']['part_ids']=['p0001']
    b=compute(p,query=q);r=row(b);f=r.facts;es=events(b)
    lower={e.id for e in es.values() if e.voice_id=='p0002:v0001'}
    assert f['bass_movement'] is None and not f['full_context_verified']
    assert set(f['unknown_event_ids'])&lower
    assert lower<={s.event_id for s in r.support}
    assert any(d.cause=='source_evidence_incomplete' and 'unknown_pitch_or_invalid_tie' in d.reasons for d in b.snapshot.diagnostics)
    if selected=='upper_only':
        assert lower<=set(f['omitted_event_ids'])
        assert all(s.role=='context' for s in r.support if s.event_id in lower)
        assert f['sonorities'][0]['classification']=='selected_subset_only'
    assert all(e in EvaluationGraph(b).raw for e in ['event:'+eid for eid in lower])


def test_written_sounding_domains_and_grace_guard(tmp_path):
    b=evaluate(material(tmp_path,'transpose'));f=row(b).facts
    assert f['sonorities'][0]['members'][0]['pitch']['step']=='E'
    assert f['sonorities'][0]['members'][0]['pitch']['alteration']==-1
    upper=next(o.fact for o in b.advanced.snapshot.observations if o.fact.kind=='note' and o.fact.event_kind=='pitched' and o.fact.written_pitch.step=='F')
    assert upper.written_pitch.step=='F' and upper.sounding_pitch.step=='E'
    grace=evaluate(material(tmp_path,'grace'));f=row(grace).facts
    assert not any(m['voice_id']=='p0001:v0001' for m in f['voice_movements'])
    assert any('inspection_or_grace_outside_continuity_criterion' in v['reasons'] for v in f['unresolved_voices'])


def test_existing_ambiguous_collection_and_stage3_remain_separate(tmp_path):
    p=tmp_path/'ambiguous.musicxml'
    write_score(p,[{'bars':[[(['C4','E4','G4','A4'],2)],[(['C4','E4','G4','A4'],2)]]}],meter=(2,4))
    b=evaluate(p);r=row(b)
    assert all(s['classification']=='ambiguous' and len(s['alternatives'])==2 for s in r.facts['sonorities'])
    assert any(d.cause=='musical_ambiguity' for d in b.snapshot.diagnostics)
    assert r.facts['reading_basis']=='descriptive_relationships_sufficient_no_nonfunctional_claim'
    later=compute(p,query={'evaluation_policy':POLICY,'assessment_focus':['arrival']})
    assert not later.snapshot.relationships and later.snapshot.local_contexts
    assert later.snapshot.versions['local_harmony']=='v02_local_harmony_h2_v1'
    split=compute(p,query=query_for(p,(1,1),(2,2),evaluation_policy=POLICY,assessment_focus=['harmonic_direction']))
    assert not split.snapshot.relationships and any('no_adjacent_sonority_cells' in d.reasons for d in split.snapshot.diagnostics)


def test_schema_complete_pagination_ownership_and_rehashed_tampering(tmp_path):
    p=material(tmp_path);b=evaluate(p);r=row(b);q=b.snapshot.requested_query.model_dump(mode='json')
    tool=next(t for t in TOOLS if t.name=='get_musical_evaluation')
    args=EvaluationArguments(path=str(p),**q,section='evidence',record_ref=r.id,
                             expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id,page_size=50,max_bytes=32768)
    jsonschema.validate(args.model_dump(mode='json'),input_schema(tool))
    seen=[]
    for _ in range(20):
        page=project_evaluation(b,args);jsonschema.validate(page.model_dump(mode='json'),output_schema(tool))
        seen.extend(i.record_ref for i in page.items)
        if not page.next_cursor:break
        args=args.model_copy(update={'cursor':page.next_cursor})
    else:pytest.fail('Small H1 fixture evidence failed to complete in 20 pages')
    assert len(seen)==len(set(seen))==len(EvaluationGraph(b).closure(r.id))
    assert not page.evidence_complete_in_this_response  # Complete sequence, not a complete single response.
    for field in ('held_event_ids','bass_movement','unknown_event_ids'):
        forged=deepcopy(b);rr=row(forged)
        rr.facts[field]=[] if field!='bass_movement' else {'semitones':99}
        if field=='unknown_event_ids':rr.facts[field]=['invented_unknown']
        rr.id='eval_relationship:'+digest({'source':forged.snapshot.source_sha256,**rr.model_dump(mode='json',exclude={'id'})})
        forged.snapshot.evaluation_snapshot_id=digest(forged.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
        with pytest.raises(EvaluationFailure,match='policies'):validate_evaluation(forged)
    bad=args.model_copy(update={'assessment_focus':['voice_interaction']})
    with pytest.raises(EvaluationFailure,match='query'):project_evaluation(b,bad)


def test_opt_in_voice_focus_and_silence_do_not_manufacture_harmony(tmp_path):
    p=material(tmp_path)
    legacy=compute(p);default_v2=compute(p,query={'evaluation_policy':POLICY})
    assert not legacy.snapshot.relationships and not default_v2.snapshot.relationships
    assert 'harmonic_relationships' not in default_v2.snapshot.versions
    b=compute(p,query={'evaluation_policy':POLICY,'assessment_focus':['voice_interaction']})
    assert row(b).category=='interaction'
    root=ET.fromstring(p.read_bytes())
    for n in root.findall('.//note'):
        n.remove(n.find('pitch'));ET.SubElement(n,'rest')
        for tie in n.findall('tie'):n.remove(tie)
    silent=tmp_path/'silent.musicxml';silent.write_bytes(ET.tostring(root))
    b=evaluate(silent)
    assert not b.snapshot.relationships
    assert any('no_adjacent_sonority_cells' in d.reasons for d in b.snapshot.diagnostics)


def test_combined_stage1_and_h1_focus_retains_the_actual_returned_count(source):
    q=stage1_query(source);q['assessment_focus']=['identity_and_setting','harmonic_direction']
    b=compute(source,query=q)
    h1=[r for r in b.snapshot.relationships if r.criterion=='sonority_continuity']
    assert len(b.snapshot.relationships)==3 and 1<=len(h1)<=2
    assert all(r.facts['selection']['returned_boundaries']==len(h1) for r in h1)
    assert any(r.criterion=='identity_with_changed_setting' for r in b.snapshot.relationships)
