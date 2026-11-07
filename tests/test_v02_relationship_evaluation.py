"""Stage-1 independent source arithmetic, provenance and versioned compatibility."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import json
import xml.etree.ElementTree as ET
import pytest,jsonschema
from composerlint.engine.evaluation import compute
from composerlint.evaluation_models import EvaluationQuery,EvaluationArguments,EvaluationFailure
from composerlint.evaluation_review import EvaluationGraph,project_evaluation,validate_evaluation
from composerlint.review import digest
from composerlint.service import analyze_score
from composerlint.tool_contracts import TOOLS,input_schema,output_schema,dispatch
from v02_advanced_helpers import write_score
from v02_evaluation_helpers import query_for,F

POLICY='v02_relationship_evaluation_v2'


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    p=tmp_path_factory.mktemp('relationships')/'source.musicxml'
    melody=[[('C4',1),('Bb3',1)],[('Eb4',Fraction(3,2)),('Db4',Fraction(1,2))],
            [('C4',Fraction(1,2)),('Eb4',Fraction(1,2)),('Ab4',Fraction(1,2)),('Bb4',Fraction(1,2))]]
    first=[[('Ab3' if i%2==0 else 'Eb3',Fraction(1,4)) for i in range(8)] for _ in range(3)]
    second=[[('C3' if i%3==0 else 'Eb3',Fraction(1,6)) for i in range(12)] for _ in range(3)]
    bass=[[('Ab2',1),('Db3',1)],[('C3',1),('Db3',1)],[('Ab2',1),('G2',1)]]
    later=[[(['Ab1','Ab2'],1),('Db3',1)],*bass[1:]]
    parts=[{'bars':melody+melody},{'bars':first+second},{'bars':bass+later}]
    write_score(p,parts,meter=(2,4))
    # Explicit independent integer durations: 12 divisions per quarter admits 1/6.
    root=ET.fromstring(p.read_bytes())
    for part,spec in zip(root.findall('part'),parts):
        part.find('measure/attributes/divisions').text='12'
        for measure,bar in zip(part.findall('measure'),spec['bars']):
            durations=[int(duration*12) for pitches,duration in bar for _ in (pitches if isinstance(pitches,list) else [pitches])]
            for note,units in zip(measure.findall('note'),durations):note.find('duration').text=str(units)
    p.write_bytes(ET.tostring(root))
    return p


def query(path,**changes):
    return query_for(path,(1,3),(4,6),evaluation_policy=POLICY,assessment_focus=['identity_and_setting'],
        analysis_options={'neighbor_measures':0,'pattern_min_attacks':3,'pattern_max_attacks':3},**changes)


def relation(bundle,criterion):return next(r for r in bundle.snapshot.relationships if r.criterion==criterion)


def test_no_intention_identity_setting_and_depth_are_source_backed(source):
    before=source.read_bytes();b=compute(source,query=query(source));s=b.snapshot
    assert s.evaluation_schema_version==2 and s.requested_query.intent is None
    r=relation(b,'identity_with_changed_setting')
    assert r.facts['identity_voice_id']=='p0001:v0001'
    assert r.facts['whole_span_pitch_duration_register_identity'] is True
    assert r.facts['setting_attacks']==[24,36]
    assert [Fraction(x['numerator'],x['denominator']) for x in r.facts['setting_attack_rates']]==[4,6]
    assert r.criterion_provenance=='analytical_question' and not r.goal_criterion_ids and r.advice is None
    assert 'conditional' in r.interpretation and 'not_measured_intensity' in ' '.join(r.limitations)
    low=relation(b,'register_entrance');assert low.facts['lowest_sounding_pitches']==[44,32]
    assert low.facts['semitone_change']==-12
    assert source.read_bytes()==before
    g=EvaluationGraph(b)
    for row in s.relationships+s.diagnostics:
        closure=g.closure(row.id);refs={x.record_ref for x in closure}
        assert len(refs)==len(closure)
        assert all(set(g.links[x.record_ref])<=refs for x in closure)
    assert not g.raw['evaluation:overview']['categories']


def test_hypothetical_intention_prioritizes_real_criterion_without_changing_facts(source):
    q=query(source);base=compute(source,query=q)
    q['intent']={'wording':'Explicitly hypothetical test intention: preserve rhythmic identity in the accompaniment.',
        'goals':[{'category':'rhythmic','criterion':'rhythmic_identity','voice_ids':['p0002:v0001']}]}
    b=compute(source,query=q)
    assert b.advanced.snapshot==base.advanced.snapshot
    assert b.snapshot.indicators==base.snapshot.indicators and b.snapshot.styles==base.snapshot.styles
    r=relation(b,'identity_with_changed_setting')
    assert r.facts==relation(base,'identity_with_changed_setting').facts
    assert r.priority_basis==['composer_intention:0:rhythmic_identity:goal_tension']
    assert r.goal_criterion_ids and r.advice and 'retaining' in r.advice
    assert r.criterion_provenance=='analytical_question'  # A real goal does not invent the analytical criterion.
    assert next(x for x in b.snapshot.ratings if x.category=='rhythmic').label=='Review suggested'


def test_mozart_explicit_rhythmic_identity_legacy_and_v2_preserve_assessment_and_advice():
    p=F/'classical_positive.musicxml'
    q=query_for(p,(1,4),(5,8),intent={'wording':'Illustrative goal: identical rhythms in both lines.',
        'goals':[{'category':'rhythmic','criterion':'rhythmic_identity'}]})
    legacy=compute(p,query=q);new=compute(p,query={**q,'evaluation_policy':POLICY})
    assert legacy.snapshot.evaluation_schema_version==1
    assert not {'evaluation_policy','assessment_focus'}&legacy.snapshot.requested_query.model_dump(mode='json').keys()
    assert not {'relationships','diagnostics'}&legacy.snapshot.model_dump(mode='json').keys()
    assert legacy.snapshot.criteria==new.snapshot.criteria
    assert legacy.snapshot.ratings==new.snapshot.ratings and legacy.snapshot.feedback==new.snapshot.feedback
    rhythm=next(r for r in legacy.snapshot.ratings if r.category=='rhythmic')
    assert rhythm.label=='Partly supported'
    assert legacy.snapshot.criteria[0].outcome=='mixed'
    assert any(f.kind=='review_opportunity' and f.alternative for f in legacy.snapshot.feedback)


def test_incomplete_and_ambiguous_lanes_are_not_confused(source,tmp_path):
    q=query(source)
    q['comparison']['first']['voice_ids']=['p0001:v0001'];q['comparison']['first']['part_ids']=['p0001']
    q['comparison']['second']['voice_ids']=['p0002:v0001'];q['comparison']['second']['part_ids']=['p0002']
    partial=compute(source,query=q)
    assert any(d.cause=='source_evidence_incomplete' and 'missing_comparison_lane' in d.reasons for d in partial.snapshot.diagnostics)
    p=tmp_path/'chord.musicxml';write_score(p,[{'bars':[[(['C4','E4'],2)],[(['C4','E4'],2)]]}],meter=(2,4))
    q=query_for(p,(1,1),(2,2),evaluation_policy=POLICY)
    ambiguous=compute(p,query=q)
    assert any(d.cause=='musical_ambiguity' and 'ambiguous_overlap_or_chord' in d.reasons for d in ambiguous.snapshot.diagnostics)
    assert not ambiguous.snapshot.relationships


def test_unsuitable_focus_and_actual_existing_chord_alternatives():
    p=F.parents[0]/'v02_m3'/'ambiguity.musicxml'
    b=compute(p,query={'evaluation_policy':POLICY,'assessment_focus':['harmonic_direction']})
    assert any(d.cause=='musical_ambiguity' and 'multiple_admitted_chord_alternatives' in d.reasons for d in b.snapshot.diagnostics)
    assert any('no_adjacent_sonority_cells' in d.reasons for d in b.snapshot.diagnostics)
    d=compute(p,query={'evaluation_policy':POLICY,'assessment_focus':['development']})
    assert not d.snapshot.relationships and any(x.cause=='question_not_operational' for x in d.snapshot.diagnostics)


def test_versioned_schema_cursor_and_rehashed_semantic_tampering(source):
    q=query(source);b=compute(source,query=q)
    tool=next(t for t in TOOLS if t.name=='get_musical_evaluation')
    args=EvaluationArguments(path=str(source),**q,section='ratings',expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id,page_size=1)
    jsonschema.validate(args.model_dump(mode='json'),input_schema(tool))
    page=project_evaluation(b,args);jsonschema.validate(page.model_dump(mode='json'),output_schema(tool))
    assert page.evaluation_schema_version==2 and page.next_cursor and not page.evidence_complete_in_this_response
    bad=args.model_copy(update={'evaluation_policy':None,'assessment_focus':[]})
    with pytest.raises(EvaluationFailure,match='query'):project_evaluation(b,bad)
    forged=deepcopy(b);r=relation(forged,'identity_with_changed_setting');r.facts['setting_attacks'][1]=99
    old=r.id;r.id='eval_relationship:'+digest({'source':forged.snapshot.source_sha256,**r.model_dump(mode='json',exclude={'id'})})
    forged.snapshot.evaluation_snapshot_id=digest(forged.snapshot.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))
    with pytest.raises(EvaluationFailure,match='policies'):validate_evaluation(forged)


def test_execution_failure_remains_a_typed_error_not_musical_ambiguity(monkeypatch):
    from composerlint import evaluation_runtime
    def timeout(*args,**kwargs):raise EvaluationFailure('analysis_timeout','Existing 25-second deadline reached.')
    monkeypatch.setattr(evaluation_runtime,'page',timeout)
    result,error=dispatch('get_musical_evaluation',{'path':'not-read.musicxml','evaluation_policy':POLICY})
    assert error and result.error.code=='analysis_timeout'
    assert not hasattr(result,'execution_complete') and not hasattr(result,'diagnostics')


@pytest.mark.parametrize('alteration',['orphan_tie','grace'])
def test_unknown_tie_or_inspection_events_block_whole_span_certification(source,tmp_path,alteration):
    p=tmp_path/(alteration+'.musicxml');root=ET.fromstring(source.read_bytes())
    n=root.find('part/measure/note')
    if alteration=='orphan_tie':ET.SubElement(n,'tie',type='stop')
    else:
        grace=deepcopy(n);ET.SubElement(grace,'grace');grace.remove(grace.find('duration'))
        root.find('part/measure').insert(list(root.find('part/measure')).index(n),grace)
    p.write_bytes(ET.tostring(root))
    b=compute(p,query=query(p))
    assert not any(r.criterion=='identity_with_changed_setting' and r.facts['identity_voice_id']=='p0001:v0001' for r in b.snapshot.relationships)
    assert any(d.cause=='source_evidence_incomplete' and ('unknown_pitch_or_invalid_tie' in d.reasons or 'inspection_or_grace_not_in_attack_stream' in d.reasons) for d in b.snapshot.diagnostics)
