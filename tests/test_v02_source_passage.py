"""Source-owned passage evidence: no M1 prerequisite and no unproved omissions."""
from pathlib import Path
import asyncio
import hashlib
import io
import json
import sys
from xml.etree import ElementTree as ET

import jsonschema
import pytest
from pydantic import ValidationError
from composerlint import passage
from composerlint.advanced_models import (PASSAGE_POLICY, AdvancedFailure, AdvancedRequest,
    PassageArguments, PassageQuery, PassagePage)
from composerlint.review import canonical, digest
from composerlint.tool_contracts import TOOLS, dispatch, input_schema, output_schema
from v02_advanced_helpers import write_score

F=Path(__file__).parent/'fixtures'
A=F/'v02_m3'
TOOL=next(t for t in TOOLS if t.name=='get_advanced_analysis')


def prepared(path):return passage.prepare(path,None,{})


def query(source,start=1,end=None,**kwargs):
    return PassageQuery(execution_policy=PASSAGE_POLICY,expected_source_snapshot=source.source_snapshot_id,
        scope={'reference_part_id':'p0001','start_measure':start,'end_measure':end or start},**kwargs)


def bundle(path,start=1,end=None,**kwargs):
    source=prepared(path);b=passage.compute(source,query(source,start,end,**kwargs),{})
    passage.validate(b,{})
    return b


def reseal(b):
    # Deliberately rehash malicious content: semantic checks must still reject it.
    for o in b.snapshot.observations:
        o.id='adv:'+digest({'source':b.snapshot.source_sha256,**o.model_dump(mode='json',exclude={'id'})})
    b.snapshot.passage_snapshot_id=digest(b.snapshot.model_dump(mode='json',exclude={'passage_snapshot_id'}))


def request(source,**kwargs):
    return PassageArguments(path=str(source.path),**query(source).model_dump(mode='json'),section='overview',**kwargs)


def test_additive_contract_schema_and_identity_families():
    source={'path':'x.musicxml','execution_policy':PASSAGE_POLICY}
    analysis={**source,'scope':{'reference_part_id':'p0001','start_measure':1,'end_measure':6},
              'section':'overview','expected_source_snapshot':'1'*64}
    for args in (source,analysis,{'path':'x.musicxml'}):
        jsonschema.validate(args,input_schema(TOOL));AdvancedRequest.model_validate(args)
    jsonschema.Draft202012Validator.check_schema(input_schema(TOOL))
    jsonschema.Draft202012Validator.check_schema(output_schema(TOOL))
    bad=[{**source,'expected_base_snapshot':'1'*64},{**analysis,'expected_advanced_snapshot':'1'*64},
         {**analysis,'already_validated':True},{**analysis,'scope':{'kind':'whole_score'}},
         {**analysis,'scope':{**analysis['scope'],'end_measure':9}},
         {**source,'cursor':'x'}, {**source,'record_ref':'event:x'},
         {**analysis,'expected_source_snapshot':None},{**analysis,'section':'evidence','record_ref':'x'},
         {**analysis,'features':['passages']},{**analysis,'analysis_options':{'include_legacy_harmonic_context':True}},
         {**analysis,'analysis_options':{'declared_roles':[{'part_id':'p0001','role':'melody','start_measure':1,'end_measure':1}]}},
         {**analysis,'scope':{**analysis['scope'],'start_measure':True}}]
    for args in bad:
        with pytest.raises(ValidationError):AdvancedRequest.model_validate(args)


def test_source_worker_and_complete_evidence_cannot_build_m1(monkeypatch):
    from composerlint import service,report_reuse,advanced_worker
    from composerlint.analysis_models import AnalysisReport
    from composerlint.engine import voice_leading,harmony,harmonic_checks,key_analysis
    def forbidden(*a,**k):raise AssertionError('whole-score musical report/predicate/reuse called')
    for name in ('_build_analysis_fields','analyze_score','analyze_normalized','select_key'):
        monkeypatch.setattr(service,name,forbidden)
    monkeypatch.setattr(AnalysisReport,'model_validate',forbidden)
    monkeypatch.setattr(AnalysisReport,'model_construct',forbidden)
    monkeypatch.setattr(AnalysisReport,'__init__',forbidden)
    monkeypatch.setattr(AnalysisReport,'integrity',forbidden)
    for module,names in [(voice_leading,['execute']),(harmony,['execute']),
                         (harmonic_checks,['execute_sevenths','execute_cadences']),
                         (key_analysis,['select_key','estimate_candidates']), (report_reuse,['take','publish'])]:
        for name in names:monkeypatch.setattr(module,name,forbidden)
    def worker(args):
        out=io.StringIO()
        with monkeypatch.context() as scoped:
            scoped.setattr(sys,'stdin',io.StringIO(canonical({'operation':'passage','arguments':args})))
            scoped.setattr(sys,'stdout',out);advanced_worker.main()
        result=json.loads(out.getvalue());assert result['ok'],result
        return PassagePage.model_validate(result['result'])
    base={'path':str(A/'trio.musicxml'),'execution_policy':PASSAGE_POLICY}
    source=worker(base)
    args={**base,'section':'overview','scope':{'reference_part_id':'p0001','start_measure':1,'end_measure':1},'expected_source_snapshot':source.source_snapshot_id}
    first=worker(args)
    notes=worker({**args,'section':'observations','expected_passage_snapshot':first.passage_snapshot_id})
    target=notes.items[0].record_ref
    evidence={**args,'section':'evidence','record_ref':target,'expected_passage_snapshot':first.passage_snapshot_id}
    rows=[]
    while True:
        page=worker(evidence);rows+=page.items
        assert page.timings['source_loads']==1 and page.timings['scoped_predicate_passes']==2
        if not page.next_cursor:break
        evidence['cursor']=page.next_cursor
    assert len({r.record_ref for r in rows})==len(rows)
    assert any(r.record_ref.startswith('transpose:') for r in rows)
    assert all('base_snapshot_id' not in r.data for r in rows)


def test_horn_transposition_stable_source_and_timing():
    b=bundle(A/'variants/solo_horn.musicxml',end=2)
    notes=[o.fact for o in b.snapshot.observations if o.fact.kind=='note']
    spell=lambda p:None if p is None else (p.step,p.alteration,p.octave)
    assert [spell(n.written_pitch) for n in notes[:4]]==[('C',0,5),('D',0,5),('E',0,5),('G',0,5)]
    assert [spell(n.sounding_pitch) for n in notes[:4]]==[('F',0,4),('G',0,4),('A',0,4),('C',0,5)]
    assert [n.onset.fraction() for n in notes[:4]]==[0,1,2,3]
    assert all(a.status=='verified' for a in b.snapshot.transposition_audit if a.status!='not_applicable')
    assert b.snapshot.source_sha256==hashlib.sha256(b.source.path.read_bytes()).hexdigest()
    assert prepared(b.source.path).source_snapshot_id==b.source.source_snapshot_id


@pytest.mark.parametrize('name', ['piano_multivoice.musicxml','unknown_pitch.musicxml','timing_grace.musicxml',
    'invalid_ties.musicxml','m3/fifths_ties.musicxml','m3/fifths_async.musicxml','m3/fifths_unrelated_voice.musicxml',
    'm3/chords_unknown_lower.musicxml','m3/crossing_chordal.musicxml','v02_m3/orchestra.musicxml'])
def test_source_occupancy_and_notes_are_complete(name):
    b=bundle(F/name)
    scope=b.snapshot.resolved_scope
    events={e.id:e for p in b.source.score.parts for e in p.events}
    noteids={o.fact.event_id for o in b.snapshot.observations if o.fact.kind=='note'}
    assert noteids==set(scope.primary_event_ids+scope.context_event_ids)
    assert set(scope.occupancy_event_ids)<=noteids
    assert scope.part_ids==[p.id for p in b.source.score.parts]
    for o in b.snapshot.observations:
        if o.fact.kind=='sonority':
            occupied={e.id for e in events.values() if e.duration.fraction()>0 and e.onset.fraction()<=o.span.start.fraction() and e.end.fraction()>=o.span.end.fraction()}
            assert {s.event_id for s in o.support}==occupied
    assert all(c.checked+c.skipped==c.candidate for c in b.snapshot.coverage)
    assert not any(o.fact.kind in ('passage','legacy_context') for o in b.snapshot.observations)


def test_focus_keeps_other_instruments_and_held_occupancy(tmp_path):
    path=tmp_path/'held.musicxml'
    write_score(path,[{'name':'Horn in F','transpose':{'diatonic':-4,'chromatic':-7},'bars':[[('C5',4)],[('D5',4)],[('E5',4)]]},
                      {'name':'Piano','bars':[[(['F3','A3','C4'],4)],[(['G3','B3','D4'],4)],[(['A3','C4','E4'],4)]]},
                      {'name':'Held bass','bars':[[('F2',4)],[('F2',4)],[('F2',4)]]}])
    root=ET.fromstring(path.read_bytes());held=root.findall('part')[2].findall('measure')
    for index,measure in enumerate(held):
        note=measure.find('note')
        if index<2:ET.SubElement(note,'tie',type='start')
        if index>0:ET.SubElement(note,'tie',type='stop')
    path.write_bytes(ET.tostring(root))
    source=prepared(path);q=query(source,2)
    q.scope.part_ids=['p0001'];b=passage.compute(source,q,{});passage.validate(b,{})
    scope=b.snapshot.resolved_scope
    assert all(e.startswith('p0001:') for e in scope.primary_event_ids)
    assert {'p0001','p0002','p0003'}=={s.part_id for o in b.snapshot.observations for s in o.support}
    bass=source.score.parts[2].events[0]
    assert bass.id in scope.occupancy_event_ids and bass.onset.fraction()==0 and bass.end.fraction()==12
    assert len(bass.source_segment_ids)==3
    for o in b.snapshot.observations:
        if o.fact.kind=='sonority':assert bass.id in {s.event_id for s in o.support} and len(o.fact.members)==5
    graph=passage.PassageGraph(source,b.snapshot)
    target=next(o for o in b.snapshot.observations if o.fact.kind=='sonority')
    rows=graph.evidence(target.id);refs={r.record_ref for r in rows}
    assert all('segment:'+s in refs for s in bass.source_segment_ids)
    assert all('part:'+p.id in refs for p in source.score.parts)
    assert all(set(graph.links[r.record_ref])<=refs for r in rows)


def test_adjacent_support_and_duplicate_printed_labels(tmp_path):
    path=tmp_path/'edges.musicxml'
    write_score(path,[{'bars':[[('C4',4)],[('D4',4)],[('E4',4)],[('F4',4)]]}])
    path.write_text(path.read_text().replace('number="2"','number="1"'))
    b=bundle(path,2,analysis_options={'neighbor_measures':0})
    ids=[e.id for e in b.source.score.parts[0].events]
    assert b.snapshot.resolved_scope.primary_event_ids==[ids[1]]
    assert b.snapshot.resolved_scope.context_event_ids==[ids[0],ids[2]]
    assert b.snapshot.resolved_scope.boundary_support_event_ids==[ids[0],ids[2]]
    intervals=[o.fact for o in b.snapshot.observations if o.fact.kind=='interval' and o.fact.relation=='melodic']
    assert [(f.from_event_id,f.to_event_id) for f in intervals]==[(ids[0],ids[1]),(ids[1],ids[2])]
    assert b.source.score.parts[0].measures[0].original_number==b.source.score.parts[0].measures[1].original_number=='1'
    assert 'neighbor_measures_disabled_adjacent_logical_support_only' in b.snapshot.resolved_scope.context_limitations


@pytest.mark.parametrize('damage',['occupancy','context','audit','pitch','support','coverage','missing_observation',
    'motion','coordination','versions','assumptions','work','query','source_id','premise'])
def test_rehashed_forged_or_incomplete_evidence_is_rejected(damage):
    b=bundle(A/'trio.musicxml',end=2);s=b.snapshot
    if damage=='occupancy':s.resolved_scope.occupancy_event_ids.pop()
    elif damage=='context':s.resolved_scope.context_spans[0].end.numerator+=1
    elif damage=='audit':s.transposition_audit.pop()
    elif damage=='pitch':
        f=next(o for o in s.observations if o.fact.kind=='note').fact
        f.sounding_pitch=f.sounding_pitch.model_copy(update={'octave':f.sounding_pitch.octave+1,'chromatic_number':f.sounding_pitch.chromatic_number+12})
    elif damage=='support':s.observations[0].support[0].source_segment_ids=[]
    elif damage=='coverage':
        c=s.coverage[0]
        s.coverage[0]=c.model_copy(update={'units':c.units[:-1],**{k:getattr(c,k)-1 for k in ('candidate','eligible','checked','matched')}})
    elif damage=='missing_observation':s.observations.pop()
    elif damage=='motion':next(o for o in s.observations if o.fact.kind=='ensemble' and o.fact.relation=='motion').fact.directions=[-1,-1]
    elif damage=='coordination':next(o for o in s.observations if o.fact.kind=='ensemble' and o.fact.relation=='coordination').fact.attacks=[False,False]
    elif damage=='versions':s.component_versions['notes']='forged'
    elif damage=='assumptions':s.assumptions=[]
    elif damage=='work':s.work_counts['rows']+=1
    elif damage=='query':s.requested_query.scope.end_measure=1
    elif damage=='source_id':s.source_snapshot_id='0'*64
    elif damage=='premise':next(o for o in s.observations if o.premise_ids).premise_ids=[]
    reseal(b)
    with pytest.raises((AdvancedFailure,ValidationError)):passage.validate(b,{})


def test_prepared_source_mutation_stale_source_options_and_cursor(tmp_path):
    path=tmp_path/'source.musicxml';path.write_bytes((A/'solo.musicxml').read_bytes())
    b=bundle(path);b.source.score.parts[0].events[0].written_pitch.octave+=1
    with pytest.raises(AdvancedFailure,match='mutated'):passage.validate(b,{})
    source=prepared(path);args=request(source)
    page=passage.run(args)
    with pytest.raises(AdvancedFailure,match='passage_snapshot|Passage query'):
        passage.run(args.model_copy(update={'expected_passage_snapshot':'0'*64}))
    with pytest.raises(AdvancedFailure,match='Cursor'):
        passage.run(args.model_copy(update={'cursor':'forged'}))
    with pytest.raises(AdvancedFailure,match='Source/options'):
        passage.run(args.model_copy(update={'options':{'key_override':{'tonic':'C','mode':'major'}}}))
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(AdvancedFailure,match='Source/options'):passage.run(args)
    assert page.passage_snapshot_id


def test_pages_closed_complete_and_preparation_repeats(monkeypatch):
    from composerlint.engine import parser
    original=parser.load_score;calls=[]
    def counted(*a,**k):calls.append(1);return original(*a,**k)
    monkeypatch.setattr(parser,'load_score',counted)
    source=prepared(A/'trio.musicxml');b=passage.compute(source,query(source),{})
    targets=[next(o for o in b.snapshot.observations if o.fact.kind==kind) for kind in ('interval','sonority','ensemble')]
    for target in targets:
        args=request(source,page_size=7).model_copy(update={'section':'evidence','record_ref':target.id,'expected_passage_snapshot':b.snapshot.passage_snapshot_id})
        expected=passage.PassageGraph(source,b.snapshot).evidence(target.id)
        rows=[];pages=0
        while True:
            before=len(calls);page=passage.run(args);pages+=1
            assert len(calls)==before+1
            assert page.start_index==len(rows)
            assert len(canonical(page.model_dump(mode='json')).encode())<=args.max_bytes
            assert page.timings['preparation_runs']==1 and page.timings['preparation_reused_between_requests']==0
            rows+=page.items
            if not page.next_cursor:break
            args=args.model_copy(update={'cursor':page.next_cursor})
        assert pages>1 and [r.model_dump() for r in rows]==[r.model_dump() for r in expected]
        assert len(rows)==page.total_items==len({r.record_ref for r in rows})
        assert not page.evidence_complete_in_this_response


def test_distant_music_changes_ownership_not_passage_work(tmp_path):
    short=tmp_path/'short.musicxml';long=tmp_path/'long.musicxml'
    local=[[('C4',1),('D4',1),('E4',2)],[('F4',4)],[('G4',4)]]
    write_score(short,[{'bars':local},{'bars':[[('C3',4)]]*3}])
    write_score(long,[{'bars':local+[[('A4',4)]]*16},{'bars':[[('C3',4)]]*19}])
    a,b=bundle(short),bundle(long)
    assert a.snapshot.source_snapshot_id!=b.snapshot.source_snapshot_id
    assert a.snapshot.work_counts==b.snapshot.work_counts
    def strip(z):
        # Observation identities must change with the complete source hash;
        # compare their complete contents using position-bound premise names.
        data=canonical([o.model_dump(mode='json') for o in z.snapshot.observations])
        for index,o in enumerate(z.snapshot.observations):data=data.replace(o.id,'observation:'+str(index))
        return json.loads(data)
    assert strip(a)==strip(b)
    assert a.snapshot.resolved_scope==b.snapshot.resolved_scope
    assert all(not e.endswith('e000019') for e in b.snapshot.resolved_scope.context_event_ids)


@pytest.mark.parametrize('name',['solo','trio','ambiguity','orchestra','sequence'])
def test_existing_m3_fact_outputs_match_full_fixture(name,monkeypatch):
    from composerlint.engine import advanced
    from composerlint.service import analyze_score
    from composerlint.report_reuse import code_identity
    path=A/(name+'.musicxml');source=prepared(path);end=len(source.score.parts[0].measures)
    if end>8:end=8
    b=passage.compute(source,query(source,end=end),{});passage.validate(b,{})
    report=analyze_score(path);base=digest(report.model_dump(mode='json'))
    work=[];execute=advanced.execute_features
    def record(ctx):execute(ctx);work.append(dict(ctx.work))
    monkeypatch.setattr(advanced,'execute_features',record)
    legacy=advanced.compute(path,None,{'features':['notes','sonorities','melody','patterns','ensemble'],
        'expected_base_snapshot':base,'scope':{'kind':'measure_range','reference_part_id':'p0001','start_measure':1,'end_measure':end}},
        _base_report={'report':report.model_dump(mode='json'),'snapshot':base,'code':code_identity()})
    observations=b.snapshot.observations
    if name=='orchestra':
        # The complete source has ten bars; the eight-bar passage adds the
        # first pitched attack after bar nine's rest as explicit note support.
        # Legacy continuity already references this attack but its note list
        # omits it. Require the precise extra source fact, not a looser match.
        extra=[o for o in observations if o.fact.kind=='note' and o.fact.event_id=='p0001:v0001:e000014']
        assert len(extra)==1 and extra[0].fact.onset.fraction()==36
        assert extra[0].support[0].role=='context' and not extra[0].fact.attack_in_target
        assert set(b.snapshot.resolved_scope.context_event_ids)-set(legacy.snapshot.resolved_scope.context_event_ids)=={'p0001:v0001:e000014'}
        observations=[o for o in observations if o not in extra]
        assert b.snapshot.work_counts=={**work[0],'rows':work[0]['rows']+1}
    else:assert work==[b.snapshot.work_counts]
    assert [o.model_dump(mode='json') for o in observations]==[o.model_dump(mode='json') for o in legacy.snapshot.observations]
    assert b.snapshot.coverage==legacy.snapshot.coverage


def test_real_sdk_source_to_passage_evidence(tmp_path):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    root=Path(__file__).parents[1]
    async def run():
        with (tmp_path/'stderr').open('w+') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=root,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (r,w):
                async with ClientSession(r,w,read_timeout_seconds=30) as client:
                    await client.initialize()
                    async def call(args):
                        reply=await client.call_tool('get_advanced_analysis',args)
                        assert not reply.is_error,reply
                        assert reply.content[0].text==canonical(reply.structured_content)
                        jsonschema.validate(reply.structured_content,output_schema(TOOL))
                        return reply.structured_content
                    base={'path':str(A/'variants/solo_horn.musicxml'),'execution_policy':PASSAGE_POLICY}
                    source=await call(base)
                    args={**base,'expected_source_snapshot':source['source_snapshot_id'],'section':'overview',
                          'scope':{'reference_part_id':'p0001','start_measure':1,'end_measure':2}}
                    overview=await call(args)
                    args['expected_passage_snapshot']=overview['passage_snapshot_id']
                    notes=await call({**args,'section':'observations'})
                    proof={**args,'section':'evidence','record_ref':notes['items'][0]['record_ref']};rows=[]
                    while True:
                        page=await call(proof);rows+=page['items']
                        if not page['next_cursor']:break
                        proof['cursor']=page['next_cursor']
                    assert len(rows)==page['total_items']
                    assert any(r['record_ref'].startswith('transpose:') for r in rows)
        assert not (tmp_path/'stderr').read_text()
    asyncio.run(run())


def test_predicates_receive_only_declared_source_support(tmp_path,monkeypatch):
    path=tmp_path/'bounded.musicxml'
    write_score(path,[{'bars':[[('C4',4)],[('D4',4)]]+[[('E4',4)]]*30},
                      {'bars':[[('C3',4)]]*32}])
    source=prepared(path);q=query(source)
    original=passage.execute_features;seen=[]
    def checked(ctx):
        admitted=set(ctx.resolved.primary_event_ids+ctx.resolved.context_event_ids)
        assert set(ctx.events)==admitted and len(ctx.events)==4
        seen.append(len(ctx.events));original(ctx)
    monkeypatch.setattr(passage,'execute_features',checked)
    b=passage.compute(source,q,{});passage.validate(b,{})
    assert seen==[4,4] and sum(len(p.events) for p in source.score.parts)==64
    assert b.snapshot.work_counts['pair_units']==1


def test_invalid_tie_and_unknown_pitch_block_dependent_claims():
    invalid=bundle(F/'invalid_ties.musicxml')
    assert any(a.status=='unknown' and 'invalid_tie_chain' in a.reasons for a in invalid.snapshot.transposition_audit)
    assert not any(o.fact.kind=='interval' and o.fact.relation=='melodic' for o in invalid.snapshot.observations)
    unknown=bundle(F/'m3/chords_unknown_lower.musicxml')
    chords=[o.fact for o in unknown.snapshot.observations if o.fact.kind=='sonority']
    assert any(f.unknown_event_ids and not f.complete and not f.bass_event_ids and not f.alternatives for f in chords)


def test_boundary_overlap_outside_context_is_not_hidden(tmp_path):
    path=tmp_path/'overlap.musicxml'
    # The primary logical C spans five bars. Its chordal F begins outside
    # requested neighbor context, but overlaps that endpoint and blocks melody.
    write_score(path,[{'bars':[[('C4',4)],[('C4',4)],[('C4',4)],[('C4',4)],[(['C4','F4'],4)],[('D4',4)]]}])
    root=ET.fromstring(path.read_bytes());bars=root.find('part').findall('measure')
    for i,m in enumerate(bars[:5]):
        note=m.find('note')
        if i<4:ET.SubElement(note,'tie',type='start')
        if i>0:ET.SubElement(note,'tie',type='stop')
    path.write_bytes(ET.tostring(root))
    b=bundle(path,analysis_options={'neighbor_measures':0})
    first=b.source.score.parts[0].events[0]
    assert len(first.source_segment_ids)==5
    assert any(e.startswith('p0001:') for e in b.snapshot.resolved_scope.boundary_support_event_ids)
    assert any(o.fact.kind=='continuity_break' and 'ambiguous_overlap_or_chord' in o.fact.reasons for o in b.snapshot.observations)
    assert not any(o.fact.kind=='interval' and o.fact.relation=='melodic' for o in b.snapshot.observations)


def test_source_discovery_pagination_scope_errors_and_limits(monkeypatch):
    source=prepared(A/'trio.musicxml')
    args=PassageArguments(path=str(source.path),execution_policy=PASSAGE_POLICY,page_size=2)
    allrows=[]
    while True:
        page=passage.run(args);allrows+=page.items
        jsonschema.validate(page.model_dump(mode='json'),output_schema(TOOL))
        if not page.next_cursor:break
        args=args.model_copy(update={'cursor':page.next_cursor,'expected_source_snapshot':page.source_snapshot_id})
    assert len(allrows)==page.total_items==len({r.record_ref for r in allrows})
    assert len([r for r in allrows if r.record_ref.startswith('part:')])==3
    assert len([r for r in allrows if r.record_ref.startswith('measure:')])==sum(len(p.measures) for p in source.score.parts)
    for changes in ({'reference_part_id':'wrong'},{'start_measure':8,'end_measure':8},{'part_ids':['missing']},{'voice_ids':['missing']}):
        q=query(source);q.scope=q.scope.model_copy(update=changes)
        with pytest.raises(AdvancedFailure,match='part|reference|Passage|Focus'):passage.compute(source,q,{})
    monkeypatch.setitem(passage.LIMITS,'events',1)
    with pytest.raises(AdvancedFailure,match='events'):prepared(source.path)


def test_passage_worker_keeps_deadline_and_skips_m1_reuse(monkeypatch):
    from composerlint import report_reuse,advanced_runtime
    from composerlint.passage_session import worker
    def forbidden(*a,**k):raise AssertionError('passage entered M1 report reuse')
    monkeypatch.setattr(report_reuse,'take',forbidden)
    worker.close()
    try:
        args={'path':str(A/'solo.musicxml'),'execution_policy':PASSAGE_POLICY}
        page,error=dispatch('get_advanced_analysis',args)
        assert not error and page.analysis_scope=='source_preparation'
        process=worker.process
        second,error=dispatch('get_advanced_analysis',args)
        assert not error and worker.process is process and second.timings['source_reuse_hits']==1
        assert 25==advanced_runtime.LIMITS['deadline_seconds']
        with pytest.raises(AdvancedFailure) as exc:
            worker.request({'operation':'passage','arguments':args},timeout=0)
        assert exc.value.code=='analysis_timeout' and process.poll() is not None and worker.process is None
    finally:worker.close()


def test_source_policy_changes_and_mixed_cursor_rejected(monkeypatch):
    from composerlint import report_reuse
    source=prepared(A/'solo.musicxml');args=request(source,page_size=1)
    first=passage.run(args)
    assert first.next_cursor
    with pytest.raises(AdvancedFailure,match='Cursor'):
        passage.run(args.model_copy(update={'cursor':first.next_cursor,'expected_passage_snapshot':first.passage_snapshot_id,'page_size':2}))
    source_args=PassageArguments(path=str(source.path),execution_policy=PASSAGE_POLICY,page_size=1)
    discovery=passage.run(source_args)
    assert discovery.next_cursor
    with pytest.raises(AdvancedFailure,match='Cursor'):
        passage.run(source_args.model_copy(update={'cursor':discovery.next_cursor,'expected_source_snapshot':source.source_snapshot_id,'features':['notes']}))
    monkeypatch.setattr(report_reuse,'code_identity',lambda:'0'*64)
    with pytest.raises(AdvancedFailure,match='Source/options'):passage.run(args)


def test_parts_align_by_time_not_printed_or_traversal_measure(tmp_path):
    path=tmp_path/'alignment.musicxml'
    write_score(path,[{'bars':[[('C4',4)],[('D4',4)],[('E4',4)]]},
                      {'bars':[[('C3',2)],[('D3',4)],[('E3',4)],[('F3',2)]]}])
    root=ET.fromstring(path.read_bytes())
    for index,m in enumerate(root.findall('part')[1].findall('measure')):m.set('number',str(index+40))
    path.write_bytes(ET.tostring(root))
    b=bundle(path,2);other=b.source.score.parts[1]
    expected={e.id for e in other.events[1:3]}
    assert expected<=set(b.snapshot.resolved_scope.occupancy_event_ids)
    sounding=[o for o in b.snapshot.observations if o.fact.kind=='sonority']
    assert [(o.span.start.fraction(),o.span.end.fraction()) for o in sounding]==[(4,6),(6,8)]
    assert {s.event_id for o in sounding for s in o.support if s.part_id=='p0002'}==expected
    assert [m.original_number for m in other.measures]==['40','41','42','43']


def test_passage_has_no_writes_network_or_viewer():
    import subprocess
    code='''import sys,os,hashlib
from composerlint.passage import run
from composerlint.advanced_models import PassageArguments,PASSAGE_POLICY

def audit(event,args):
 if event=="open":
  path,mode,flags=args
  if not isinstance(path,int) and path!="/dev/null" and (isinstance(mode,str) and any(c in mode for c in "wax+") or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):raise AssertionError("passage write")
 if event in ("socket.connect","socket.bind","subprocess.Popen","os.system","os.remove","os.rename","os.mkdir"):raise AssertionError("passage side effect: "+event)
sys.addaudithook(audit)
a={"path":sys.argv[1],"execution_policy":PASSAGE_POLICY}
source=run(PassageArguments(**a))
a.update(section="overview",expected_source_snapshot=source.source_snapshot_id,scope={"reference_part_id":"p0001","start_measure":1,"end_measure":1})
p=run(PassageArguments(**a))
assert "composerlint.viewer_runtime" not in sys.modules
print(p.source_sha256)
'''
    path=A/'trio.musicxml';before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert subprocess.check_output([sys.executable,'-B','-c',code,str(path)],text=True).strip()==before
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
