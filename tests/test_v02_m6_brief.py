"""M6.2 authored engineering controls; no repertoire, renderer or candidate runs."""
from copy import deepcopy
import hashlib
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import pytest
from composerlint.advanced_models import PassageArguments
from composerlint.expressive_models import BriefArguments, BriefResult
from composerlint.expressive_brief import context
from composerlint.passage_session import Store, worker as passage_worker
from composerlint.editing_runtime import editing, read_source
from composerlint.tool_contracts import dispatch, TOOL_BY_NAME, input_schema, output_schema
from composerlint.review import digest
from composerlint.viewer_models import Selection
from composerlint.viewer_runtime import runtime
from v02_advanced_helpers import write_score


def source(path, lanes=False, bars=3):
    write_score(path, [
        {'name':'Authored protected line', 'bars':[[('C5',2),('D5',1)]]*bars},
        {'name':'Authored accompaniment', 'bars':[[('C4',1),(None,1),('G4',1)]]*bars},
    ], meter=(3,4))
    root=ET.parse(path).getroot()
    if lanes:
        bar=root.findall('part')[1].find('measure')
        ET.SubElement(ET.SubElement(bar,'backup'),'duration').text='12'
        note=deepcopy(bar.find('note'));note.find('voice').text='2';note.find('duration').text='12'
        note.find('type').text='half';ET.SubElement(note,'dot');bar.append(note)
    for p in root.findall('part'):
        d=ET.Element('direction');typ=ET.SubElement(d,'direction-type')
        ET.SubElement(ET.SubElement(typ,'dynamics'),'p');p.find('measure').insert(1,d)
    path.write_bytes(ET.tostring(root))
    return path


def prepare(path, store=None, end=3, request=None):
    call=request or (lambda a: store.run(PassageArguments(**a)).model_dump(mode='json'))
    common=dict(path=str(path),execution_policy='source_bound_passage_v1')
    s=call(common)
    query=dict(execution_policy='source_bound_passage_v1',expected_source_snapshot=s['source_snapshot_id'],
        expression_policy='v02_m6_expression_v1',features=['notes','sonorities','melody','ensemble'],
        scope=dict(kind='measure_range',reference_part_id='p0001',start_measure=1,end_measure=end))
    overview=call(dict(path=str(path),section='overview',**query))
    return query,overview['passage_snapshot_id'],s['options_sha256']


def draft(query, snapshot):
    return dict(composer_request='Make this passage more restless, but preserve the melody and rhythm.',
        intended_effect='Explore a less settled accompaniment beneath the unchanged line.',
        allowed_families=['articulation'],
        protected_material=[dict(wording='preserve the melody',kind='pitches',part_id='p0001'),
            dict(wording='preserve the rhythm',kind='timing',part_id='p0002')],
        artistic_preferences=[dict(wording='keep it lyrical',interpretation='A conversational preference, not a measurable guarantee.')],
        assumptions=['The composer identifies part 1 as the melody and part 2 as accompaniment.'],
        directions=[dict(direction_id='accent_frame',title='Weight the accompaniment’s late return',
            current_character='The lower part alternates attacks with rests beneath a longer upper note.',
            evidence_refs=[],operations=[dict(family='articulation',action='set',symbol='accent',notes=[
                dict(source_note_ordinal=3,expected_written=dict(step='G',alteration=0,octave=4))])],
            musical_reasoning='Highlighting the third-beat return after the rest may draw weight away from the bar’s opening and toward the upper line’s C5–D5 step.',
            possible_effect='The setting could feel less settled while its rhythm remains exact.',
            tradeoff='The accompaniment may become more insistent and distract from the lyrical line.',
            preserved_material='Every pitch and duration; all upper-line notes; other voices and score structure.',
            candidate_unknowns=['Actual balance and the effect of the accents remain untested.'])],
        evidence_query=query,expected_passage_snapshot=snapshot)


def seed(path, query, snapshot, options_sha, store):
    """Seed existing workspace/selection state only; does not claim viewer acceptance."""
    p,raw,identity=read_source(path)
    selection=Selection(source_sha256=hashlib.sha256(raw).hexdigest(),snapshot_id='a'*64,
        options_sha256=options_sha,reference_part_id='p0001',part_ids=['p0001','p0002'],
        start_measure_index=1,end_measure_index=query['scope']['end_measure'])
    entry=dict(instance='authored-existing-view',revision=4,selected=selection,
        manifest={'scope':selection.model_dump(mode='json')})
    runtime.entries[entry['instance']]=entry
    now=time.monotonic()
    w=dict(id='authored-workspace',path=p,identity=identity,source_sha256=selection.source_sha256,
        snapshot=selection.snapshot_id,options_sha256=options_sha,options_request=None,
        revision=0,entry=entry,state='no_proposal',pending=None,proposal=None,created=now,touched=now,
        candidate_path=None,proposal_size=0)
    editing.workspaces[w['id']]=w
    d=draft(query,snapshot)
    owned=next(iter(store.sources.values()))
    b=next(iter(owned.passages.values())).bundle
    # An actual ensemble relationship supported by the confirmed accompaniment.
    relation=next(o for o in b.snapshot.observations if o.fact.kind!='note'
        and o.feature=='ensemble' and any(s.part_id=='p0002' for s in o.support))
    d['directions'][0]['evidence_refs']=[relation.id]
    args=dict(workspace_id=w['id'],expected_source_sha256=w['source_sha256'],expected_snapshot=w['snapshot'],
        expected_options_sha256=options_sha,expected_workspace_revision=0,client_request_id='brief-1',
        selection=selection.model_dump(mode='json'),target=dict(part_id='p0002'),draft=d)
    return w,args


@pytest.fixture
def setup(tmp_path,monkeypatch):
    path=source(tmp_path/'authored.musicxml');store=Store()
    q,s,o=prepare(path,store)
    w,args=seed(path,q,s,o,store)
    def request(req,**kwargs):
        assert req['operation']=='expressive_brief_context'
        return context(store,req['arguments'],req['path'],req['options'])
    monkeypatch.setattr(passage_worker,'request',request)
    yield path,store,w,args
    editing.workspaces.clear();runtime.entries.clear()


def invoke(args,code=None):
    result,error=dispatch('prepare_expressive_edit_brief',args)
    if code:
        assert error,result
        assert result.error.code==code,result
    else:
        assert not error,result
    return result


def follow(args,result,action,**extra):
    return {**{k:v for k,v in args.items() if k!='draft'},'action':action,
        'expected_workspace_revision':result.workspace_revision,'expected_brief_sha256':result.brief_sha256,
        'client_request_id':action+'-request',**extra}


def test_bound_brief_exact_wording_modes_and_zero_mutation(setup,monkeypatch):
    path,store,w,args=setup;before=path.read_bytes()
    from composerlint import editing_source, editing_pitch, editing_runtime, service
    def forbidden(*a,**kw):raise AssertionError('brief invoked candidate/render/M1/file mutation')
    monkeypatch.setattr(editing_source,'transpose',forbidden)
    monkeypatch.setattr(editing_pitch,'transform',forbidden)
    monkeypatch.setattr(editing_runtime,'worker',forbidden)
    monkeypatch.setattr(service,'analyze_score',forbidden)
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    monkeypatch.setattr(Path,'write_text',forbidden)
    monkeypatch.setattr(editing,'clear_candidate',forbidden)
    r=invoke(args)
    assert r.brief.draft.composer_request==args['draft']['composer_request']
    assert r.brief.selection.model_dump(mode='json')==args['selection']
    assert r.brief.source_sha256==hashlib.sha256(before).hexdigest()
    assert r.brief.workspace_revision==w['revision']==1
    assert r.brief.code_identity==next(iter(store.sources.values())).source.code_id
    assert r.brief.target['voices'][0]['voice_id']=='p0002:v0001'
    assert r.brief.constraint_baselines and all(x['baseline_sha256'] for x in r.brief.constraint_baselines)
    assert r.brief.evidence[0]['closure_record_count']>1
    assert r.brief.mutation_authorized is False and r.brief.draft.artistic_preferences[0].status.endswith('not_verifiable')
    assert path.read_bytes()==before and w['proposal'] is None and w['candidate_path'] is None
    # No explanation mode is an engine input; both use exactly the same brief facts.
    assert 'mode' not in input_schema(TOOL_BY_NAME['prepare_expressive_edit_brief'])['properties']
    assert invoke(follow(args,r,'get')).brief==r.brief


def test_idempotency_choose_revise_reject_and_no_edit_authority(setup):
    _,_,w,args=setup;r=invoke(args)
    assert invoke(args)==r
    changed=deepcopy(args);changed['draft']['intended_effect']='Different words'
    invoke(changed,'revision_conflict')
    chosen=invoke(follow(args,r,'choose',direction_id='accent_frame'))
    assert chosen.brief.discussion_state=='direction_chosen' and not chosen.brief.mutation_authorized
    invoke(args,'stale_brief')
    revised={**args,'client_request_id':'revision-2','expected_workspace_revision':w['revision'],
        'expected_brief_sha256':chosen.brief_sha256}
    revised['draft']=deepcopy(args['draft']);revised['draft']['assumptions'].append('Consider a more restrained emphasis.')
    new=invoke(revised);assert new.brief.discussion_state=='open' and new.brief_sha256!=chosen.brief_sha256
    rejected=invoke(follow(revised,new,'reject'))
    assert rejected.brief.discussion_state=='rejected' and rejected.brief.chosen_direction_id is None
    assert w['proposal'] is None


@pytest.mark.parametrize('field',['expected_source_sha256','expected_snapshot','expected_options_sha256'])
def test_wrong_source_options_base(setup,field):
    *_,args=setup;args[field]='0'*64;invoke(args,'stale_snapshot')


def test_source_change_and_wrong_brief_digest(setup):
    path,_,w,args=setup;r=invoke(args)
    get=follow(args,r,'get',expected_brief_sha256='0'*64);invoke(get,'stale_brief')
    path.write_bytes(path.read_bytes()+b'\n')
    invoke(follow(args,r,'get'),'stale_source')


@pytest.mark.parametrize('case',['selection_source','selection_range','workspace_revision','live_revision','unknown_part','all_lanes','notes_outside','wrong_pitch'])
def test_target_and_selection_refusals(setup,case):
    _,_,w,a=setup
    expected='invalid_edit'
    if case=='selection_source':a['selection']['source_sha256']='0'*64;expected='stale_selection'
    if case=='selection_range':a['selection']['end_measure_index']=2;expected='stale_selection'
    if case=='workspace_revision':a['expected_workspace_revision']=1;expected='revision_conflict'
    if case=='live_revision':a.pop('selection');a['live_selection_revision']=3;expected='stale_selection'
    if case=='unknown_part':a['target']['part_id']='p0003';expected='invalid_selection'
    if case=='all_lanes':a['target']['lane_mode']='all'
    if case=='notes_outside':a['draft']['directions'][0]['operations'][0]['notes'][0]['source_note_ordinal']=999;expected='ambiguous_target'
    if case=='wrong_pitch':a['draft']['directions'][0]['operations'][0]['notes'][0]['expected_written']['step']='A';expected='stale_note'
    invoke(a,expected);assert w['revision']==0 and w.get('expressive_brief') is None


def test_contradictory_lock_retains_prior_brief_and_evidence_remains_reusable(setup):
    _,_,w,args=setup;r=invoke(args)
    conflict=deepcopy(args);conflict.update(client_request_id='locked',expected_workspace_revision=1,expected_brief_sha256=r.brief_sha256)
    conflict['draft']['protected_material'].append(dict(wording='Keep accompaniment articulations unchanged',kind='articulations',part_id='p0002'))
    invoke(conflict,'constraint_conflict')
    assert w['expressive_brief']==r
    assert invoke(follow(args,r,'get'))==r
    # Reusing the source/passage after a failed brief must not fail its graph seal.
    fixed=deepcopy(conflict);fixed['client_request_id']='fixed';fixed['draft']['protected_material'].pop()
    invoke(fixed)


@pytest.mark.parametrize('case',['rhythm','multiple_parts','harmony_lock','unallowed_family','missing_relationship','foreign_relationship','two_generic_directions','missing_m6_evidence'])
def test_scope_and_hard_constraint_contract(setup,case):
    *_,args=setup;d=args['draft'];expected='invalid_edit'
    if case=='rhythm':d['allowed_families']=['rhythm']
    if case=='multiple_parts':args['target']=[{'part_id':'p0001'},{'part_id':'p0002'}]
    if case=='harmony_lock':d['protected_material'][0]['kind']='harmony'
    if case=='unallowed_family':d['allowed_families']=['pitch_register'];expected='constraint_conflict'
    if case=='missing_relationship':d['directions'][0]['evidence_refs']=['source:prepared'];expected='ungrounded_direction'
    if case=='foreign_relationship':d['directions'][0]['evidence_refs']=['adv:from-other-source'];expected='ungrounded_direction'
    if case=='two_generic_directions':d['directions']*=3
    if case=='missing_m6_evidence':d['evidence_query'].pop('expression_policy')
    invoke(args,expected)


def test_live_selection_binding_and_publication_race(setup,monkeypatch):
    _,_,w,args=setup;args.pop('selection');args['live_selection_revision']=4
    actual=passage_worker.request
    def raced(*a,**kw):
        result=actual(*a,**kw);w['entry']['revision']+=1;return result
    monkeypatch.setattr(passage_worker,'request',raced)
    invoke(args,'revision_conflict');assert w.get('expressive_brief') is None
    monkeypatch.setattr(passage_worker,'request',actual);args['live_selection_revision']=5
    r=invoke(args);assert r.brief.live_selection_revision==5
    w['entry']['revision']+=1
    invoke(follow(args,r,'get',live_selection_revision=6),'stale_brief')


def test_known_articulation_conflicts_and_conditional_dynamic(setup):
    _,_,_,args=setup
    dynamic=deepcopy(args)
    dynamic['draft']['allowed_families']=['dynamics']
    dynamic['draft']['directions'][0]['operations']=[dict(family='dynamics',action='set',symbol='mp',anchor=dict(
        source_note_ordinal=1,expected_written=dict(step='C',alteration=0,octave=4)))]
    r=invoke(dynamic)
    assert any('baseline' in s and 'restoration' in s for s in r.brief.future_verification_required)
    remove=deepcopy(args);remove.update(client_request_id='remove',expected_workspace_revision=1,expected_brief_sha256=r.brief_sha256)
    remove['draft']['directions'][0]['operations'][0]['action']='remove'
    invoke(remove,'constraint_conflict')


def test_pitch_intentions_are_non_mutating_and_protected_pitch_conflicts(setup):
    path,_,w,args=setup;before=path.read_bytes()
    args['draft']['allowed_families']=['pitch_register']
    args['draft']['directions'][0]['operations']=[dict(family='pitch_register',change=dict(kind='transpose',
        notes=[dict(source_note_ordinal=3,expected_written=dict(step='G',alteration=0,octave=4))],diatonic_steps=-7,semitones=-12))]
    args['draft']['protected_material'].append(dict(wording='Keep the accompaniment above C3',kind='register_bounds',part_id='p0002',minimum_written_midi=48,maximum_written_midi=84))
    r=invoke(args)
    assert any('register bounds' in s for s in r.brief.future_verification_required)
    conflict=deepcopy(args);conflict.update(client_request_id='pitchlock',expected_workspace_revision=1,expected_brief_sha256=r.brief_sha256)
    conflict['draft']['protected_material'].append(dict(wording='Keep all accompaniment pitches',kind='pitches',part_id='p0002'))
    invoke(conflict,'constraint_conflict')
    assert path.read_bytes()==before and w['candidate_path'] is None


def test_actual_owned_worker_schema_and_dispatch_without_renderer(tmp_path):
    """Real isolated passage transport, seeded workspace; no browser acceptance claim."""
    import jsonschema
    path=source(tmp_path/'transport.musicxml');store=Store()
    try:
        # Identical pure/owned queries allow seeding just the analytical reference, not facts supplied to production.
        q,s,o=prepare(path,store)
        real=lambda a: passage_worker.request({'operation':'passage','arguments':a})
        rq,rs,ro=prepare(path,request=real)
        assert (rq,rs,ro)==(q,s,o)
        w,args=seed(path,rq,rs,ro,store)
        tool=TOOL_BY_NAME['prepare_expressive_edit_brief']
        jsonschema.validate(args,input_schema(tool))
        r=invoke(args)
        jsonschema.validate(r.model_dump(mode='json'),output_schema(tool))
        assert BriefResult.model_validate_json(r.canonical_json())==r
        assert w['proposal'] is None
    finally:
        passage_worker.close();editing.workspaces.clear();runtime.entries.clear()


@pytest.mark.parametrize('case',['five_measures','unknown_lane','multi_lane','unaligned','filtered_evidence','stale_passage','changed_query','expired_evidence','oversized','register_conflict','tie_fragment','unsupported_notation'])
def test_additional_scope_freshness_and_constraint_gates(tmp_path,monkeypatch,case):
    path=source(tmp_path/'guard.musicxml',lanes=case=='multi_lane',bars=5 if case=='five_measures' else 3)
    if case in ('tie_fragment','unsupported_notation'):
        root=ET.parse(path).getroot();part=root.findall('part')[1];notes=part.findall('measure/note')
        if case=='tie_fragment':
            # One complete tied G4 from m1 beat 3 to m2 beat 1, with matching sounding/visual endpoints.
            notes[3].find('pitch/step').text='G'
            for note,kind in [(notes[2],'start'),(notes[3],'stop')]:
                ET.SubElement(note,'tie',type=kind);ET.SubElement(ET.SubElement(note,'notations'),'tied',type=kind)
        else:ET.SubElement(notes[2],'cue')
        path.write_bytes(ET.tostring(root))
    store=Store();q,s,o=prepare(path,store,end=5 if case=='five_measures' else 3)
    w,args=seed(path,q,s,o,store)
    monkeypatch.setattr(passage_worker,'request',lambda req,**kw:context(store,req['arguments'],req['path'],req['options']))
    expected='unsupported_brief_scope'
    try:
        if case=='unknown_lane':args['target'].update(lane_mode='lane',voice_id='p0002:v9999');expected='ambiguous_target'
        if case=='multi_lane':expected='ambiguous_target'
        if case=='unaligned':
            owned=next(iter(store.sources.values()))
            # Invoke the pure admission helper on a detached source with independently shifted target measure mapping.
            from dataclasses import replace
            from composerlint.expressive_brief import validate
            from composerlint.models import rt
            from composerlint.viewer_source import ViewerFailure
            score=owned.source.score.model_copy(deep=True)
            score.parts[1].measures[-1].end=rt(10)
            verified=next(iter(owned.passages.values()))
            with pytest.raises(ViewerFailure,match='not time-aligned'):
                validate(replace(owned.source,score=score),verified.bundle.snapshot,verified.graph,BriefArguments(**args))
            return
        if case=='filtered_evidence':args['draft']['evidence_query']['scope']['part_ids']=['p0002'];expected='stale_advanced_snapshot'
        if case=='stale_passage':args['draft']['expected_passage_snapshot']='0'*64;expected='stale_advanced_snapshot'
        if case=='changed_query':args['draft']['evidence_query']['analysis_options']={'neighbor_measures':0};expected='stale_advanced_snapshot'
        if case=='expired_evidence':
            next(iter(store.sources.values())).expires=0;expected='stale_snapshot'
        if case=='oversized':
            # Legal field lengths whose complete brief exceeds the bounded response. No truncation/storage.
            args['draft']['composer_request']='x'*8000
            args['draft']['assumptions']=[str(i)+'x'*1500 for i in range(8)]
            args['draft']['directions'][0]['candidate_unknowns']=[str(i)+'y'*1500 for i in range(8)]
            expected='edit_limit'
        if case in ('register_conflict','tie_fragment'):
            args['draft']['allowed_families']=['pitch_register']
            args['draft']['directions'][0]['operations']=[dict(family='pitch_register',change=dict(kind='transpose',
                notes=[dict(source_note_ordinal=3,expected_written=dict(step='G',alteration=0,octave=4))],diatonic_steps=-7,semitones=-12))]
            if case=='register_conflict':
                args['draft']['protected_material'].append(dict(kind='register_bounds',wording='Keep the accompaniment above C4',
                    part_id='p0002',minimum_written_midi=60,maximum_written_midi=84));expected='constraint_conflict'
            else:expected='boundary_tie'
        if case=='unsupported_notation':expected='unsupported_edit'
        invoke(args,expected)
        assert w['revision']==0 and w.get('expressive_brief') is None and w['proposal'] is None
    finally:
        editing.workspaces.clear();runtime.entries.clear()


@pytest.mark.parametrize('symbol',['staccato','tenuto','accent'])
def test_supported_simple_articulation_directions(setup,symbol):
    *_,args=setup;args['draft']['directions'][0]['operations'][0]['symbol']=symbol
    r=invoke(args)
    assert r.brief.draft.directions[0].operations[0].symbol==symbol


@pytest.mark.parametrize('symbol',['pp','p','mp','mf','f','ff'])
def test_supported_simple_dynamics_remain_conditional(setup,symbol):
    *_,args=setup;args['draft']['allowed_families']=['dynamics']
    args['draft']['directions'][0]['operations']=[dict(family='dynamics',action='set',symbol=symbol,
        anchor=dict(source_note_ordinal=3,expected_written=dict(step='G',alteration=0,octave=4)))]
    r=invoke(args)
    assert 'conditional' in ' '.join(r.brief.future_verification_required)


def test_public_mcp_discovery_and_typed_unsupported_request(tmp_path):
    import asyncio,sys
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        with (tmp_path/'stderr.txt').open('w') as log:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','composerlint.server']),errlog=log) as (reader,writer):
                async with ClientSession(reader,writer) as session:
                    await session.initialize()
                    catalog=await session.list_tools()
                    tool=next(t for t in catalog.tools if t.name=='prepare_expressive_edit_brief')
                    assert tool.annotations.read_only_hint is False
                    response=await session.call_tool(tool.name,{'operation':{'kind':'rhythm'}})
                    assert response.is_error and response.structured_content['error']['code']=='invalid_edit'
    asyncio.run(check())


def test_dense_lane_counts_changed_notes_not_every_source_note(tmp_path,monkeypatch):
    path=tmp_path/'dense-authored.musicxml'
    write_score(path,[{'bars':[[('C5',1)]*12]*3},
        {'bars':[[('C4',.5),('G4',.5)]*12]*3}],meter=(12,4))
    store=Store();q,s,o=prepare(path,store);w,args=seed(path,q,s,o,store)
    args['draft']['directions'][0]['operations'][0]['notes'][0]['expected_written']['step']='C'
    monkeypatch.setattr(passage_worker,'request',lambda req,**kw:context(store,req['arguments'],req['path'],req['options']))
    try:
        r=invoke(args)
        assert r.brief.target['pitched_segment_count']==72 and 'notes' not in r.brief.target
        assert len(r.brief.draft.directions[0].operations[0].notes)==1
    finally:
        editing.workspaces.clear();runtime.entries.clear()


def test_brief_does_not_replace_existing_m5_proposal(setup):
    _,_,w,args=setup
    proposal={'proposal_id':'previous-exact-edit'};w.update(proposal=proposal,state='verified',candidate_path='existing-owned-candidate')
    invoke(args)
    assert w['proposal'] is proposal and w['candidate_path']=='existing-owned-candidate' and w['state']=='verified'
