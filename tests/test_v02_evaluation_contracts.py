"""Strict transport/ownership, complete focused closure, finite work and protected compatibility."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,subprocess,sys,time
import pytest,jsonschema
from pydantic import ValidationError
from composerlint.evaluation_models import EvaluationArguments,EvaluationQuery,EvaluationFailure
from composerlint.evaluation_review import project_evaluation,EvaluationGraph,validate_evaluation
from composerlint.evaluation_runtime import request_worker
from composerlint.engine.evaluation import compute
from composerlint.engine.evaluation_evidence import Evidence
from composerlint.tool_contracts import TOOLS,TOOL_BY_NAME,dispatch,input_schema,output_schema,canonical_payload
from composerlint.review import canonical,digest
from v02_evaluation_helpers import F,REF,evaluate,query_for,values
ROOT=Path(__file__).parents[1]


def test_ten_unchanged_contracts_and_new_readonly_schema():
    baseline=json.loads((ROOT/'docs/V0.2-M4-evidence/baseline.json').read_text())['contracts']
    assert [t.name for t in TOOLS]==['analyze_score','detect_style','inspect_measure','check_harmony','check_voice_leading','get_style_evidence',
        'get_review_page','get_score_view','resolve_score_selection','get_musical_evaluation','get_edit_workspace','prepare_score_edit',
        'get_edit_proposal','discard_edit_proposal','prepare_expressive_edit_brief','present_workspace_analysis','get_advanced_analysis']
    assert len(baseline)==10
    from v02_advanced_helpers import assert_legacy_schema_preserved
    for name,expected in baseline.items():
        t=TOOL_BY_NAME[name]
        assert_legacy_schema_preserved(t,expected)
        if name not in ('get_score_view','resolve_score_selection','get_advanced_analysis'):assert t.description==expected['description']
    tool=TOOL_BY_NAME['get_musical_evaluation']
    p,e=dispatch(tool.name,{'path':str(F/'romantic_positive.musicxml'),'page_size':1,'max_bytes':8192})
    assert not e and len(canonical_payload(p).encode())<=8192
    jsonschema.validate(p.model_dump(mode='json'),output_schema(tool))


@pytest.mark.parametrize('args',[
    {'scope':{'kind':'events','event_ids':['fake']}}, {'section':'ratings'},
    {'profiles':['modern']},{'profiles':['baroque','baroque']},{'page_size':True},{'max_bytes':32769},
    {'intent':{'wording':'','goals':[]}},{'intent':{'wording':'x','style_neutral':True,'target_styles':['modern']}},
    {'intent':{'wording':'x','goals':[{'category':'melodic','criterion':'tonal_ending'}]}},
    {'analysis_context':{'tonic':'H','collection':'major','provenance':'user_declared','wording':'invalid'}},
])
def test_malformed_requests_typed_and_never_fabricated(args):
    p,e=dispatch('get_musical_evaluation',{'path':str(F/'romantic_positive.musicxml'),**args})
    assert e and p.error.code=='invalid_analysis_scope'


def test_lossless_claim_closure_and_cursor_binding():
    path=F/'romantic_positive.musicxml';q=query_for(path,(1,2),(3,4),analysis_context=REF)
    b=compute(path,query=q);style=next(r for r in b.snapshot.styles if r.family=='romantic')
    args=EvaluationArguments(path=str(path),**q,section='evidence',expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id,record_ref=style.id,page_size=50,max_bytes=32768)
    pages=[];seen=[]
    while True:
        p=project_evaluation(b,args);pages.append(p)
        assert p.start_index==len(seen);assert len(canonical_payload(p).encode())<=32768
        seen.extend(x.record_ref for x in p.items)
        if not p.next_cursor:break
        args=args.model_copy(update={'cursor':p.next_cursor})
    assert len(seen)==len(set(seen))==pages[0].total_items
    assert seen==[r.record_ref for r in EvaluationGraph(b).closure(style.id)]
    assert any(x.startswith('segment:') for x in seen) and any(x.startswith('evaluation_claim_coverage:') for x in seen)
    assert pages[-1].section_exhausted
    if len(pages)>1:
        assert not pages[0].evidence_complete_in_this_response
        for changes in ({'page_size':49},{'max_bytes':32000},{'section':'style','record_ref':None}):
            with pytest.raises(EvaluationFailure) as exc:project_evaluation(b,args.model_copy(update={'cursor':pages[0].next_cursor,**changes}))
            assert exc.value.code=='invalid_cursor'
    changed=EvaluationArguments.model_validate({**args.model_dump(mode='json'),'intent':{'wording':'personal','target_styles':['personal']}})
    with pytest.raises(EvaluationFailure) as exc:project_evaluation(b,changed)
    assert exc.value.code=='stale_evaluation_snapshot'


def test_stale_cross_source_and_forged_semantic_values():
    b=evaluate('romantic_positive',(1,2),(3,4),analysis_context=REF)
    args=EvaluationArguments(path=str(F/'romantic_positive.musicxml'),**b.snapshot.requested_query.model_dump(mode='json'),expected_evaluation_snapshot='0'*64,section='style')
    with pytest.raises(EvaluationFailure):project_evaluation(b,args)
    b.snapshot.indicators[0].limitations.append('forged')
    b.snapshot.evaluation_snapshot_id=digest(b.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    args=args.model_copy(update={'expected_evaluation_snapshot':b.snapshot.evaluation_snapshot_id})
    with pytest.raises(EvaluationFailure):project_evaluation(b,args)


def test_source_filters_unknowns_and_midi_withhold_dependent_claims():
    p=F/'classical_positive.musicxml';q=query_for(p,(1,4),(5,8),analysis_context=REF)
    q['comparison']['first']['part_ids']=['p0001'];q['comparison']['second']['part_ids']=['p0001']
    b=compute(p,query=q)
    assert not values(b,'E07')[0].complete
    for name in ['microtone.musicxml','unknown_pitch.musicxml','invalid_ties.musicxml','all_rest.musicxml','empty.musicxml','basic.mid','ugly_ambiguous.mid']:
        p=ROOT/'tests/fixtures'/name
        b=compute(p)
        assert len(b.snapshot.ratings)==6
        if p.suffix=='.mid':
            assert 'midi_spelling_lanes_and_quantization_inferred' in b.snapshot.limitations
            assert not any(v.eligible for v in values(b,'E08'))


def test_deadline_process_terminated_reaped_and_source_intact(monkeypatch):
    p=F/'classical_positive.musicxml';before=hashlib.sha256(p.read_bytes()).hexdigest();children=[];original=subprocess.Popen
    def observe(*a,**kw):
        child=original(*a,**kw);children.append(child);return child
    monkeypatch.setattr(subprocess,'Popen',observe)
    started=time.monotonic()
    with pytest.raises(EvaluationFailure) as exc:request_worker({'operation':'analysis','path':str(p),'options':None,'query':{}},timeout=0.01)
    assert exc.value.code=='analysis_timeout' and time.monotonic()-started<2
    assert len(children)==1 and children[0].poll() is not None
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_work_preflight_and_indivisible_oversize_are_honest(tmp_path,monkeypatch):
    p=tmp_path/'oversized.musicxml';p.write_bytes(b'x'*(10*1024*1024+1))
    r,e=dispatch('get_musical_evaluation',{'path':str(p)})
    assert e and r.error.code=='analysis_limit_exceeded'
    b=evaluate('baroque_positive')
    import composerlint.engine.evaluation_evidence as ev
    monkeypatch.setattr(ev,'LIMITS',{'derived_records':0})
    with pytest.raises(EvaluationFailure):ev.derive(b.advanced,b.snapshot.requested_query)
    monkeypatch.undo()
    # Indivisible oversized caller-owned data cannot be silently truncated.
    graph=EvaluationGraph(b);graph.raw['evaluation:oversize']={'text':'x'*40000}
    monkeypatch.setattr(EvaluationGraph,'rows',lambda self,args: [__import__('composerlint.advanced_models',fromlist=['AdvancedItem']).AdvancedItem(record_ref='evaluation:oversize',data={'text':'x'*40000})])
    args=EvaluationArguments(path=str(F/'baroque_positive.musicxml'),section='evidence',record_ref='evaluation:oversize',expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id)
    with pytest.raises(EvaluationFailure) as exc:project_evaluation(b,args)
    assert exc.value.code=='oversized_record'


@pytest.mark.parametrize('name',['romantic_positive','classical_positive','mixed_br','romantic_ambiguous'])
def test_separate_process_determinism_and_source_preservation(name):
    p=F/(name+'.musicxml');before=hashlib.sha256(p.read_bytes()).hexdigest()
    code='from composerlint.service import evaluate_music;from composerlint.review import digest;import sys;print(digest(evaluate_music(sys.argv[1]).snapshot.model_dump(mode="json")))'
    outputs=[subprocess.check_output([sys.executable,'-B','-c',code,str(p)],text=True) for _ in range(2)]
    assert outputs[0]==outputs[1]
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_requested_unsupported_claim_is_typed():
    for claim in ['overall_grade','authorship','emotion','rewrite','modulation']:
        p,e=dispatch('get_musical_evaluation',{'path':str(F/'romantic_positive.musicxml'),'requested_claim':claim})
        assert e and p.error.code=='unsupported_claim'


def test_source_measure_labels_and_real_grandstaff_kept(tmp_path):
    from xml.etree import ElementTree as ET
    path=tmp_path/'labels.musicxml';root=ET.fromstring((F/'romantic_positive.musicxml').read_bytes())
    for m,label in zip(root.findall('.//part/measure'),['0','7','7','12']):m.set('number',label)
    path.write_bytes(ET.tostring(root))
    b=compute(path,query=query_for(path,(1,2),(3,4),analysis_context=REF))
    graph=EvaluationGraph(b)
    assert graph.raw['measure:p0001:m0002']['original_number']==graph.raw['measure:p0001:m0003']['original_number']=='7'
    assert graph.raw['measure:p0001:m0001']['original_number']=='0'
    assert graph.raw['measure:p0001:m0002']['label_occurrence']==1
    assert graph.raw['measure:p0001:m0003']['label_occurrence']==2
    assert graph.raw['measure:p0001:m0003']['ordinal']==3
    grand=compute(ROOT/'tests/fixtures/v02_m2/grandstaff.musicxml')
    supports=[s for o in grand.snapshot.indicators for s in o.support]
    assert {s.staff for s in supports}>={'1','2'}
    assert all(cv.checked+cv.skipped==cv.candidate and cv.nonmatch==cv.checked-cv.matched for cv in grand.snapshot.coverage)


def test_coverage_lookup_preserves_duplicates_empty_sets_and_source_order():
    from composerlint.evaluation_review import _coverage_unit_index,_matching_units
    class Unit:
        def __init__(self,ids):self.ids=ids;self.reads=0
        @property
        def event_ids(self):self.reads+=1;return self.ids
    units=[Unit(ids) for ids in (['a','b'],[],['z'],['b','a','a'],['c'],[])]
    index=_coverage_unit_index(iter(units))
    for sets,positions in [([{'b','a'}],[0,3]),([set(),{'a','b'},set()],[0,1,3,5]),([{'c'},{'z'}],[2,4]),([{'missing'}],[])]:
        assert _matching_units(index,sets)==[units[i] for i in positions]
    assert [u.reads for u in units]==[1]*len(units)


@pytest.mark.parametrize('forged',[False,True])
def test_worker_page_validates_once_before_publication(monkeypatch,capsys,forged):
    from io import StringIO
    import composerlint.engine.evaluation as engine
    import composerlint.evaluation_review as review
    from composerlint.evaluation_worker import main
    path=F/'classical_positive.musicxml';before=hashlib.sha256(path.read_bytes()).hexdigest()
    args=EvaluationArguments(path=str(path),**query_for(path,(1,8)),profiles=['classical'],sections_requested=['style'],intent={'wording':'Opening clarity','lens':{'composer':'mozart'}})
    build=engine._build_evaluation;validate=review._validated_graph;graph_init=review.EvaluationGraph.__init__
    counts={'validation':0,'graph':0}
    def constructed(*a,**kw):
        bundle=build(*a,**kw)
        if forged:
            bundle.snapshot.indicators[0].limitations.append('forged')
            bundle.snapshot.evaluation_snapshot_id=digest(bundle.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
        return bundle
    def checked(*a,**kw):
        assert capsys.readouterr().out==''
        counts['validation']+=1
        return validate(*a,**kw)
    def graph(self,*a,**kw):
        counts['graph']+=1;graph_init(self,*a,**kw)
    monkeypatch.setattr(engine,'_build_evaluation',constructed)
    monkeypatch.setattr(review,'_validated_graph',checked)
    monkeypatch.setattr(review.EvaluationGraph,'__init__',graph)
    monkeypatch.setattr(sys,'stdin',StringIO(canonical({'operation':'page','arguments':args.model_dump(mode='json')})))
    main();payload=json.loads(capsys.readouterr().out)
    assert counts=={'validation':1,'graph':1}
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    if forged:
        assert not payload['ok'] and payload['error']['code']=='unavailable_evidence'
        assert 'bound policies' in payload['error']['reason'] and 'result' not in payload
    else:
        assert payload['ok'] and payload['result']['source_sha256']==before
        monkeypatch.undo()
        reference=compute(path,args.options,args.query())
        expected=project_evaluation(reference,args)
        assert payload['result']==expected.model_dump(mode='json')
        graph=EvaluationGraph(reference)
        for observation in reference.advanced.snapshot.observations:
            cv=next(cv for cv in reference.advanced.snapshot.coverage if cv.feature==observation.feature)
            sets=[{s.event_id for s in observation.support}]
            if observation.fact.kind=='pattern':sets=[set(x.event_ids) for x in observation.fact.occurrences]
            expected=[u.model_dump(mode='json') for u in cv.units if set(u.event_ids) in sets]
            assert graph.raw['evaluation_m3_coverage:'+observation.id]['relevant_units']==expected


def test_public_compute_and_projection_keep_semantic_validation(monkeypatch):
    import composerlint.engine.evaluation as engine
    path=F/'classical_positive.musicxml';args=EvaluationArguments(path=str(path),**query_for(path,(1,8)))
    bundle=compute(path,query=args.query())
    bundle.snapshot.indicators[0].limitations.append('forged')
    bundle.snapshot.evaluation_snapshot_id=digest(bundle.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    monkeypatch.setattr(engine,'_build_evaluation',lambda *a,**kw:bundle)
    for operation in (lambda:engine.compute(path,query=args.query()),lambda:project_evaluation(bundle,args)):
        with pytest.raises(EvaluationFailure) as exc:operation()
        assert exc.value.code=='unavailable_evidence' and 'bound policies' in exc.value.reason
