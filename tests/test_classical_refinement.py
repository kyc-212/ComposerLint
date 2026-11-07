"""Independent musical and contract expectations for the bounded C1–C4 refinement."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from composerlint.engine.evaluation import compute
from composerlint.evaluation_models import EvaluationQuery,EvaluationArguments
from composerlint.evaluation_review import EvaluationGraph
from v02_evaluation_helpers import F,query_for


def test_c1_lenses_preserve_facts_and_detected_style():
    p=F/'classical_positive.musicxml'
    q=query_for(p,(1,4),(5,8),intent={'wording':'I want related openings with later contrast.','goals':[{'category':'structural','criterion':'opening_contrast'}]})
    original=compute(p,query=q)
    for composer in ('bach','mozart','beethoven','romantic'):
        other=deepcopy(q);other['intent']['lens']={'composer':composer}
        b=compute(p,query=other)
        assert b.advanced.snapshot==original.advanced.snapshot
        assert b.snapshot.styles==original.snapshot.styles
        assert [x for x in b.snapshot.indicators if x.feature not in ('E09','E10','E11')]==original.snapshot.indicators
        assert b.snapshot.ratings==original.snapshot.ratings
        guide=EvaluationGraph(b).raw['evaluation:overview']['composer_criteria']
        assert guide['composer']==composer and len(guide['criteria'])==5


def test_c1_feedback_names_actual_notes_and_priority_without_edits():
    p=F/'return.musicxml'
    q=query_for(p,(1,1),(9,9),intent={'wording':'Keep the opening but contrast its rhythm.','lens':{'composer':'beethoven'},'goals':[
        {'category':'melodic','criterion':'exact_return'},{'category':'rhythmic','criterion':'rhythmic_contrast'}]})
    b=compute(p,query=q)
    assert b.snapshot.feedback[0].kind=='review_opportunity'
    for f in b.snapshot.feedback:
        assert 'm9 beat' in f.why and 'staff' in f.why and f.target_event_ids
        assert 'At ' in f.direction and f.alternative
        assert not any(k in f.model_dump() for k in ('edit','replacement_pitches'))
        assert EvaluationGraph(b).closure(f.id)


def test_c1_lens_suggests_criteria_without_manufacturing_essential_goals():
    p=F/'classical_ambiguous.musicxml'
    b=compute(p,query=query_for(p,(1,4),(5,8),intent={'wording':'Mozart-inspired clarity','lens':{'composer':'mozart'}}))
    assert not b.snapshot.criteria and all(r.label is None for r in b.snapshot.ratings)
    assert b.snapshot.feedback


def test_c1_optional_fields_preserve_legacy_query_shape_and_reject_wrong_emphasis():
    q=EvaluationQuery(intent={'wording':'personal'})
    assert 'lens' not in q.model_dump(mode='json')['intent']
    with pytest.raises(ValidationError):EvaluationQuery(intent={'wording':'x','lens':{'composer':'mozart','beethoven_emphasis':'late'}})
    with pytest.raises(ValidationError):EvaluationQuery(intent={'wording':'x','style_neutral':True,'lens':{'composer':'bach'}})


from pathlib import Path
from composerlint.service import analyze_score
from composerlint.review import digest
EXAMPLES=Path(__file__).parent/'fixtures/classical_intelligence'
REF={'tonic':'C','collection':'major','provenance':'user_declared','wording':'Use C major as a conditional reference.'}

def refined(name,first=None,second=None,options=None,**kw):
    p=EXAMPLES/(name+'.musicxml');base=digest(analyze_score(p,options).model_dump(mode='json'))
    def scope(ab):return {'kind':'measure_range','reference_part_id':'p0001','start_measure':ab[0],'end_measure':ab[1]}
    q={'refinement':'v02_classical_refinement_v1','expected_base_snapshot':base,**kw}
    if second:q['comparison']={'first':scope(first),'second':scope(second)}
    elif first:q['scope']=scope(first)
    return compute(p,options,query=q)


def test_c2_adapts_actual_half_and_authentic_endpoint_and_closure():
    b=refined('mozart_positive',(1,4),(5,8),analysis_context=REF,intent={'wording':'Open first, close second.','goals':[
        {'category':'harmonic','criterion':'contextual_ending','ending_kind':'half','span_index':0},
        {'category':'harmonic','criterion':'contextual_ending','ending_kind':'authentic','span_index':1}]})
    rows=[x for x in b.snapshot.indicators if x.feature=='E09']
    assert [x.values.candidate_kind for x in rows]==['half','authentic']
    assert all(x.values.eligible for x in rows)
    assert next(x for x in b.snapshot.ratings if x.category=='harmonic').label=='Supported'
    graph=EvaluationGraph(b)
    for row in rows:
        refs={x.record_ref for x in graph.closure(row.id)}
        assert row.values.legacy_ref in refs and all(x in refs for x in row.values.harmony_refs)


def test_c2_crop_and_reference_conflict_are_unassessed_not_bad_endings():
    b=refined('mozart_contrast',(1,4),(5,8),analysis_context=REF)
    assert all(not x.values.eligible for x in b.snapshot.indicators if x.feature=='E09')
    wrong={**REF,'tonic':'D'}
    b=refined('mozart_positive',(1,4),(5,8),analysis_context=wrong)
    assert all(not x.values.eligible for x in b.snapshot.indicators if x.feature=='E09')


def test_c2_explicit_goal_mismatch_preserves_requested_ab_roles():
    b=refined('mozart_positive',(5,8),(1,4),analysis_context=REF,intent={'wording':'I specifically want a closed first ending.','goals':[
        {'category':'harmonic','criterion':'contextual_ending','ending_kind':'half','span_index':0}]})
    rows=[x for x in b.snapshot.indicators if x.feature=='E09']
    assert [x.values.candidate_kind for x in rows]==['authentic','half']
    assert next(x for x in b.snapshot.ratings if x.category=='harmonic').label=='Review suggested'


def test_c2_minor_v7_adapter_reuses_existing_engine(tmp_path):
    from v02_advanced_helpers import write_score
    p=tmp_path/'minor.musicxml'
    write_score(p,[{'bars':[[(['G3','B3','D4','F4'],4)],[(['C3','Eb3','G3'],4)]]}])
    opts={'key_override':{'tonic':'C','mode':'minor'}}
    base=digest(analyze_score(p,opts).model_dump(mode='json'))
    b=compute(p,opts,query={'refinement':'v02_classical_refinement_v1','expected_base_snapshot':base,'analysis_context':{**REF,'collection':'harmonic_minor'}})
    row=next(x for x in b.snapshot.indicators if x.feature=='E09')
    assert row.values.eligible and row.values.candidate_kind=='authentic'


def test_c3_tied_preparation_dissonance_resolution_has_exact_source_arithmetic():
    b=refined('bach_positive',intent={'wording':'Identify a prepared suspension.','lens':{'composer':'bach'},'goals':[{'category':'harmonic','criterion':'prepared_suspension'}]})
    rows=[x for x in b.snapshot.indicators if x.feature=='E10' and x.values.relation=='suspension']
    assert len(rows)==1
    row=rows[0];v=row.values
    assert v.onset.fraction()==12 and v.resolution.fraction()==14
    assert v.compound_intervals==[17,18,17] and v.diatonic_steps==[-1] and v.semitones==[-1]
    events={e.id:e for p in b.advanced.report.score.parts for e in p.events}
    assert len(events[v.event_ids[0]].source_segment_ids)==2
    assert next(r for r in b.snapshot.ratings if r.category=='harmonic').label=='Supported'
    assert EvaluationGraph(b).closure(row.id)


def test_c3_reattack_is_not_a_prepared_suspension():
    b=refined('bach_contrast',intent={'wording':'Identify a prepared suspension.','goals':[{'category':'harmonic','criterion':'prepared_suspension'}]})
    assert not any(x.feature=='E10' and x.values.relation=='suspension' for x in b.snapshot.indicators)
    assert next(r for r in b.snapshot.ratings if r.category=='harmonic').label is None


def test_c3_chromatic_passing_and_neighbor_are_specific_relationships():
    b=refined('romantic_positive',analysis_context=REF,intent={'wording':'Identify an admitted chromatic connection.','lens':{'composer':'romantic'},'goals':[{'category':'melodic','criterion':'chromatic_line'}]})
    rows=[x for x in b.snapshot.indicators if x.feature=='E10']
    passing=next(x for x in rows if x.values.relation=='passing' and x.values.onset.fraction()==1)
    assert passing.values.diatonic_steps==[1,1] and passing.values.semitones==[2,1]
    assert len(passing.values.reference_outside_event_ids)==1
    neighbor=next(x for x in rows if x.values.relation=='neighbor' and x.values.onset.fraction()==5)
    assert neighbor.values.diatonic_steps==[1,-1] and neighbor.values.semitones==[1,-1]
    assert next(r for r in b.snapshot.ratings if r.category=='melodic').label=='Supported'


def test_c3_unknown_support_never_becomes_complete_harmony():
    b=refined('unknown',analysis_context=REF)
    assert not any(x.feature=='E10' and x.values.relation in ('passing','neighbor','suspension') for x in b.snapshot.indicators)
    assert all(not x.values.eligible for x in b.snapshot.indicators if x.feature=='E09')


def seed_query(name,**extra):
    p=EXAMPLES/(name+'.musicxml');report=analyze_score(p);part=report.score.parts[0]
    ids=[e.id for e in part.events if e.kind=='pitched' and e.onset.fraction()<4]
    return {'event_ids':ids,'destination_voice_id':part.voices[0].id,'include_fragments':True},extra


def test_c4_seed_maps_full_transposition_scaling_fragment_and_meter():
    seed,_=seed_query('beethoven_positive')
    b=refined('beethoven_positive',(1,4),(5,8),motif_seed=seed,intent={'wording':'Develop the chosen idea and show its displaced rhythm.','lens':{'composer':'beethoven'},'goals':[
        {'category':'melodic','criterion':'seed_development'},{'category':'rhythmic','criterion':'metrical_displacement'}]})
    rows=[x for x in b.snapshot.indicators if x.values.kind=='seed_mapping']
    full=next(x for x in rows if not x.values.fragment);v=full.values
    assert v.relation=='scaled_transposed' and v.diatonic_shift==1 and v.semitone_shift==2 and v.rhythm_scale.fraction()==1/2
    assert [x.fraction() for x in v.metrical_phases]==[1,2] and v.metrical_comparison_eligible
    fragments=[x for x in rows if x.values.fragment and len(x.values.seed_indices)==2]
    assert fragments and all(x.values.low_distinctiveness for x in fragments)
    assert all(r.label=='Supported' for r in b.snapshot.ratings if r.category in ('melodic','rhythmic'))
    assert any('m5 beat 2' in f.why for f in b.snapshot.feedback)
    assert EvaluationGraph(b).closure(full.id)


def test_c4_two_note_coincidence_does_not_establish_development():
    seed,_=seed_query('beethoven_contrast')
    b=refined('beethoven_contrast',(1,4),(5,8),motif_seed=seed,intent={'wording':'Develop the seed.','goals':[{'category':'melodic','criterion':'seed_development'}]})
    assert any(x.values.kind=='seed_mapping' and x.values.fragment for x in b.snapshot.indicators)
    assert not any(x.values.kind=='seed_mapping' and not x.values.fragment for x in b.snapshot.indicators)
    assert next(r for r in b.snapshot.ratings if r.category=='melodic').label is None


def test_c4_search_counts_and_rests_are_not_bridged():
    seed,_=seed_query('beethoven_positive')
    b=refined('beethoven_positive',(1,4),(5,8),motif_seed=seed)
    row=next(x for x in b.snapshot.indicators if x.values.kind=='seed_search')
    assert row.values.complete and row.values.candidate_windows==row.values.checked_windows
    assert row.values.mapped_windows==sum(x.values.kind=='seed_mapping' for x in b.snapshot.indicators)
    events={e.id:e for p in b.advanced.report.score.parts for e in p.events}
    for x in b.snapshot.indicators:
        if x.values.kind=='seed_mapping':
            es=[events[e] for e in x.values.destination_event_ids]
            assert all(a.end==b.onset for a,b in zip(es,es[1:]))


def test_c4_seed_requires_fresh_bound_identity_and_continuous_source():
    seed,_=seed_query('beethoven_positive')
    with pytest.raises(ValidationError):EvaluationQuery(motif_seed=seed)
    from composerlint.evaluation_models import EvaluationFailure
    seed['event_ids']=list(reversed(seed['event_ids']))
    with pytest.raises(EvaluationFailure):refined('beethoven_positive',(1,4),(5,8),motif_seed=seed)


def test_c3_diatonic_middle_with_chromatic_endpoint_is_not_a_chromatic_middle(tmp_path):
    from v02_advanced_helpers import write_score
    p=tmp_path/'diatonic_middle.musicxml'
    write_score(p,[{'bars':[[('Bb5',1),('A5',1),('G5',2)]]},{'bars':[[(['G3','Bb3','D4'],4)]]}])
    b=compute(p,query={'refinement':'v02_classical_refinement_v1','analysis_context':REF,'intent':{'wording':'Identify a chromatic middle-note connection.','goals':[{'category':'melodic','criterion':'chromatic_line'}]}})
    assert any(x.feature=='E10' and x.values.relation=='passing' for x in b.snapshot.indicators)
    assert next(r for r in b.snapshot.ratings if r.category=='melodic').label is None


def test_new_relations_reject_rehashed_tampered_musical_results():
    from composerlint.evaluation_review import validate_evaluation
    from composerlint.evaluation_models import EvaluationFailure
    b=refined('bach_positive')
    row=next(x for x in b.snapshot.indicators if x.feature=='E10' and x.values.relation=='suspension')
    row.values.semitones=[-2]
    b.snapshot.evaluation_snapshot_id=digest(b.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    with pytest.raises(EvaluationFailure):validate_evaluation(b)


def test_opt_in_query_and_lens_remain_bound_to_paginated_evidence():
    from composerlint.evaluation_review import project_evaluation
    from composerlint.evaluation_models import EvaluationFailure
    seed,_=seed_query('beethoven_positive')
    b=refined('beethoven_positive',(1,4),(5,8),motif_seed=seed,intent={'wording':'Beethoven development','lens':{'composer':'beethoven','beethoven_emphasis':'middle'}})
    q=b.snapshot.requested_query.model_dump(mode='json');p=EXAMPLES/'beethoven_positive.musicxml'
    args=EvaluationArguments(path=str(p),**q,section='evidence',record_ref=next(x.id for x in b.snapshot.indicators if x.values.kind=='seed_mapping'),expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id,page_size=10)
    refs=[]
    while True:
        page=project_evaluation(b,args);assert page.start_index==len(refs)
        refs.extend(x.record_ref for x in page.items)
        if not page.next_cursor:break
        args=args.model_copy(update={'cursor':page.next_cursor})
    assert len(refs)==len(set(refs))==page.total_items
    changed=deepcopy(q);changed['intent']['lens']['beethoven_emphasis']='late'
    with pytest.raises(EvaluationFailure):project_evaluation(b,EvaluationArguments(path=str(p),**changed,expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id))


def test_no_refinement_preserves_legacy_serialized_contract_and_no_automatic_edit():
    q=EvaluationQuery(intent={'wording':'personal','goals':[{'category':'melodic','criterion':'exact_return'}]})
    data=q.model_dump(mode='json')
    assert 'motif_seed' not in data and 'refinement' not in data and 'lens' not in data['intent']
    assert 'ending_kind' not in data['intent']['goals'][0] and 'span_index' not in data['intent']['goals'][0]
    b=compute(EXAMPLES/'ambiguous.musicxml',query=q)
    assert all(x.feature not in ('E09','E10','E11') for x in b.snapshot.indicators)
    assert 'refinement' not in b.snapshot.versions
    assert all(x.state!='supported_under_policy' for x in b.snapshot.styles)


def test_actual_stdio_refinement_schema_and_source_closed_feedback(tmp_path):
    import asyncio,sys,json
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def run():
        root=Path(__file__).parents[1]
        async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=root),errlog=sys.stderr) as (r,w):
            async with ClientSession(r,w,read_timeout_seconds=30) as client:
                await client.initialize();tools=(await client.list_tools()).tools
                t=next(x for x in tools if x.name=='get_musical_evaluation')
                assert t.annotations.read_only_hint and 'motif_seed' in t.input_schema['properties']
                args={'path':str((EXAMPLES/'bach_positive.musicxml').resolve()),'intent':{'wording':'Identify the prepared suspension.','lens':{'composer':'bach'},'goals':[{'category':'harmonic','criterion':'prepared_suspension'}]}}
                result=await client.call_tool(t.name,args);assert not result.is_error
                data=result.structured_content
                assert data['versions']['refinement']=='v02_classical_refinement_v1'
                args|={'expected_evaluation_snapshot':data['evaluation_snapshot_id'],'section':'feedback'}
                result=await client.call_tool(t.name,args);assert not result.is_error
                f=result.structured_content['items'][0];assert 'suspension' in f['data']['why']
                args|={'section':'evidence','record_ref':f['record_ref'],'page_size':50,'max_bytes':32768}
                refs=[]
                while True:
                    result=await client.call_tool(t.name,args);assert not result.is_error
                    data=result.structured_content;assert data['start_index']==len(refs)
                    assert result.content[0].text==json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False)
                    refs.extend(x['record_ref'] for x in data['items'])
                    if data['section_exhausted']:break
                    args['cursor']=data['next_cursor']
                assert len(refs)==len(set(refs))==data['total_items']
                assert any(x.startswith('segment:') for x in refs)
    asyncio.run(run())
