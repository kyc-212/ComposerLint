from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from composerlint.service import analyze_score,CATALOG
from composerlint.analysis_models import (AnalysisReport,AnalysisRequest,Coverage,AnalysisSummary,
    RULE_IDS,global_scope,CoverageExclusion,KeyAnalysis)
F=Path(__file__).parent/'fixtures'/'m2'


def report(): return analyze_score(F/'estimate_major.musicxml')


def test_root_composition_version_roundtrip_and_ownership():
    from composerlint.engine.parser import load_score
    r=report(); score=load_score(F/'estimate_major.musicxml')
    assert r.score.canonical_json()==score.canonical_json()
    assert 'key_analysis' not in score.model_dump()
    assert AnalysisReport.model_validate_json(r.canonical_json()).canonical_json()==r.canonical_json()
    assert r.model_dump(exclude_defaults=True)['schema_version']=='0.1'
    assert r.model_dump(exclude_defaults=True)['score']['schema_version']=='0.1'
    assert r.score.options is not r.options
    assert r.harmony and not r.cadences and r.style_analysis is not None
    assert r.summary==AnalysisSummary.derive(r.diagnostics,r.coverage)
    assert [c.rule_id for c in r.coverage]==list(RULE_IDS)
    assert all(c.flagged_count==0 for c in r.coverage)
    assert all(c.implementation_state=='implemented' and c.candidate_count is not None for c in r.coverage[3:11])
    assert r.coverage[-1].status=='complete' and r.coverage[-1].candidate_count==1
    assert all(d.category=='analysis_limitation' for d in r.diagnostics)


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(schema_version=0.1),lambda d:d.update(schema_version='0.2'),lambda d:d.update(extra=1),
    lambda d:d['summary'].update(diagnostic_count=True),lambda d:d['summary'].update(diagnostic_count=0),
    lambda d:d['key_analysis']['statistics']['histogram'][0].update(numerator=1.0),
    lambda d:d['key_analysis']['statistics']['histogram'][0].update(numerator=24,denominator=2),
    lambda d:d['key_analysis']['statistics']['histogram'][0].update(numerator=13),
    lambda d:d['key_analysis']['statistics'].update(eligible_event_count=7),
    lambda d:d['key_analysis']['statistics'].update(distinct_pitch_class_count=4),
    lambda d:d['key_analysis']['statistics']['eligible_duration_ratio'].update(numerator=0),
    lambda d:d['key_analysis']['statistics']['eligible_activity_duration'].update(numerator=21),
    lambda d:d['key_analysis']['statistics']['eligible_event_ids'].append('absent'),
    lambda d:d['key_analysis'].update(selected_key=None),
    lambda d:d['key_analysis']['confidence'].update(level='certain'),
    lambda d:d['key_analysis']['confidence'].update(reason_code='random'),
    lambda d:d['key_analysis']['candidates'][0].update(correlation='NaN'),
    lambda d:d['key_analysis']['candidates'][0].update(correlation='0.9'),
    lambda d:d['key_analysis']['candidates'][0].update(tonic_pitch_class=1),
    lambda d:d['key_analysis']['candidates'].reverse(),
    lambda d:d['key_analysis']['limitation_ids'].append('absent'),
    lambda d:d['key_analysis']['source_context'].update(source_sha256='0'*64),
    lambda d:d['coverage'].pop(),lambda d:d['coverage'][0].update(checked_count=0),
    lambda d:d['coverage'][3].update(candidate_count=0),
    lambda d:d['coverage'][3].update(status='complete'),
    lambda d:d['diagnostics'][0].update(category='wrong'),
    lambda d:d['diagnostics'][0]['scope']['references']['event_ids'].append('absent'),
    lambda d:d['diagnostics'][0].update(assumption_ids=['absent']),
    lambda d:d['options']['midi'].update(quarter_length_divisors=[3,4]),
    lambda d:d['analysis_metadata']['components'].reverse(),
    lambda d:d['assumptions'][0].update(id='bad'),
])
def test_strict_rejection(mutation):
    d=report().model_dump(mode='json'); mutation(d)
    with pytest.raises(ValidationError): AnalysisReport.model_validate(d)


@pytest.mark.parametrize('evidence',[
    {'kind':'source_key','observation_ids':['absent']},
    {'kind':'coverage','coverage_ids':['absent']},
    {'kind':'key_reference','key_reference_id':'kref0002'},
    {'kind':'events','references':{'event_ids':[]}},
    {'kind':'events','references':{'event_ids':['absent']}},
    {'kind':'estimation','candidate_ids':['absent'],'histogram_reference':'key_analysis.statistics.histogram','statistics_reference':'key_analysis.statistics','reason_codes':['weak_key_estimate']},
])
def test_evidence_resolution(evidence):
    d=report().model_dump(mode='json'); d['diagnostics'][0]['evidence']=[evidence]
    with pytest.raises(ValidationError): AnalysisReport.model_validate(d)


def test_metadata_location_reference_and_timing():
    d=analyze_score(F/'declared_major.musicxml').model_dump(mode='json')
    d['key_analysis']['source_context']['key_observations'][0]['location']['measure_id']='missing'
    with pytest.raises(ValidationError): AnalysisReport.model_validate(d)
    d=analyze_score(F/'conductor_key.mid').model_dump(mode='json')
    d['key_analysis']['source_context']['key_observations'][0]['location']['source_tick']=1
    with pytest.raises(ValidationError): AnalysisReport.model_validate(d)


def test_coverage_nonzero_flagged_and_negative_result_and_disjoint_exclusions():
    c=Coverage(id='cv0001',rule_id='source_key_context',implementation_state='implemented',status='complete',unit='score',scope=global_scope(),candidate_count=1,eligible_count=1,checked_count=1,skipped_count=0,flagged_count=1,reason_codes=['executed'],exclusions=[])
    assert c.flagged_count==1
    d=c.model_dump();d['flagged_count']=2
    with pytest.raises(ValidationError):Coverage.model_validate(d)
    # A meaningful controlled future producer's 3 event units: one checked, two excluded.
    d.update(rule_id='range_issue',unit='pitched_event',candidate_count=3,eligible_count=2,checked_count=1,skipped_count=2,flagged_count=0,status='partial',exclusions=[{'unit_ids':['e1','e2'],'count':2,'reason_code':'missing_range','evidence':[]}])
    assert Coverage.model_validate(d).status=='partial'
    d['exclusions'].append({'unit_ids':['e1'],'count':1,'reason_code':'not_harmony_eligible','evidence':[]})
    with pytest.raises(ValidationError):Coverage.model_validate(d)


def test_unknown_selection_is_checked_but_estimation_skipped():
    r=analyze_score(F/'sparse.musicxml')
    assert r.key_analysis.status=='unknown'
    assert r.coverage[1].checked_count==1 and r.coverage[1].status=='complete'
    assert r.coverage[2].checked_count==0 and r.coverage[2].candidate_count==1 and r.coverage[2].skipped_count==1
    assert r.coverage[2].exclusions[0].unit_ids==['score']


def test_duplicate_unordered_and_global_timed_scopes_rejected():
    d=report().model_dump(mode='json');d['key_analysis']['reason_codes']=set(d['key_analysis']['reason_codes'])
    with pytest.raises(ValidationError):AnalysisReport.model_validate(d)
    d=report().model_dump(mode='json');d['diagnostics'][0]['scope'].update(kind='global',start={'numerator':0,'denominator':1})
    with pytest.raises(ValidationError):AnalysisReport.model_validate(d)
    d=report().model_dump(mode='json');d['key_analysis']['reason_codes'].reverse()
    with pytest.raises(ValidationError):AnalysisReport.model_validate(d)


def test_future_constraints_and_protocol_are_unusable():
    from composerlint.repair.interfaces import RepairConstraints,RepairEngine
    r=report();constraints=RepairConstraints(locked_event_ids=[r.score.parts[0].events[0].id],editable_voice_ids=['p0001:v0001'],scope=global_scope())
    assert constraints.validate_against(r) is None
    with pytest.raises(TypeError):RepairEngine()
    with pytest.raises(ValidationError):RepairConstraints(locked_event_ids=['e','e'],editable_voice_ids=[],scope=global_scope())
    bad=constraints.model_copy(update={'locked_event_ids':['absent']})
    with pytest.raises(ValueError):bad.validate_against(r)
    bad=constraints.model_copy(update={'editable_voice_ids':['absent']})
    with pytest.raises(ValueError):bad.validate_against(r)


def test_future_harmony_slots_and_cadence_references_validated():
    # Reserved-container probes use the retained archived M2 report, without M3 producer invariants.
    from composerlint.analysis_models import HarmonySpan,AnalysisScope,ReferenceSet,Confidence
    r=AnalysisReport.model_validate_json((Path(__file__).parents[1]/'docs/examples/M2-report.json').read_text());e=r.score.parts[0].events[0]
    scope=AnalysisScope(kind='span',start=e.onset,end=e.end,references=ReferenceSet(event_ids=[e.id]))
    h=HarmonySpan(id='h000001',scope=scope,event_ids=[e.id],voice_ids=[e.voice_id],sounding_pitches=[e.sounding_pitch],bass_event_id=e.id,chord_label=None,roman_numeral=None,function='unknown',key_reference_id='kref0001',confidence=Confidence(level='low',reason_code='missing_key',explanation='Schema-only test.'),unknown_reason='missing_key')
    data=r.model_dump();data['harmony']=[h]
    assert AnalysisReport.model_validate(data).harmony[0].sounding_pitches==[e.sounding_pitch]
    hdata=h.model_dump();hdata['sounding_pitches'][0].update(step='D',chromatic_number=62)
    data['harmony']=[hdata]
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)
    data=r.model_dump();data['cadences']=[dict(id='cad000001',scope=scope,kind='authentic',harmony_ids=['h000001'],evidence=[{'kind':'key_reference','key_reference_id':'kref0001'}],confidence=h.confidence,explanation='No producer.')]
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)


@pytest.mark.parametrize('case',['wrong_override','wrong_metadata_reference','estimated_below_policy','candidate_count_too_small','wrong_confidence','coverage_execution_lie'])
def test_audit_key_policy_and_coverage_consistency(case):
    if case=='wrong_override':
        r=analyze_score(F/'declared_major.musicxml',{'key_override':{'tonic':'D','mode':'minor'}})
        data=r.model_dump(mode='json');data['key_analysis']['selected_key']['tonic']='E'
    elif case=='wrong_metadata_reference':
        r=analyze_score(F/'signature_only.musicxml');data=r.model_dump(mode='json')
        data['key_analysis'].update(status='resolved',selected_key={'tonic':'C','mode':'major'},selection_method='source_metadata',key_reference_id='kref0001',confidence={'level':'high','reason_code':'selected_reference_not_fit','explanation':'Unjustified'},selected_observation_ids=['kobs000001'])
    else:
        data=report().model_dump(mode='json')
        if case=='estimated_below_policy':data['key_analysis']['candidates'][0]['correlation']='0.50000000';data['key_analysis']['candidates'][1]['correlation']='0.40000000';data['key_analysis']['candidates'][2]['correlation']='0.30000000'
        if case=='candidate_count_too_small':data['key_analysis']['candidates'].pop()
        if case=='wrong_confidence':data['key_analysis']['confidence']['level']='high'
        if case=='coverage_execution_lie':
            c=data['coverage'][2];c.update(status='skipped',eligible_count=0,checked_count=0,skipped_count=1,reason_codes=['selected_reference_precedence'],exclusions=[{'unit_ids':['score'],'count':1,'reason_code':'selected_reference_precedence','evidence':[]}]);data['summary']['complete_rule_count']-=1;data['summary']['skipped_rule_count']+=1
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)


def test_coverage_zero_candidates_and_prerequisites():
    data=report().coverage[0].model_dump();data.update(candidate_count=0,eligible_count=0,checked_count=0,skipped_count=0,status='skipped',reason_codes=['no_candidate_units'])
    assert Coverage.model_validate(data).status=='skipped'
    data['reason_codes']=[]
    with pytest.raises(ValidationError):Coverage.model_validate(data)
    r=analyze_score(F/'sparse.musicxml')
    coverage={c.rule_id:c for c in r.coverage}
    assert coverage['harmony_classification'].reason_codes==['missing_key']
    assert coverage['voice_crossing'].reason_codes==['no_candidate_units']
    assert coverage['range_issue'].reason_codes==['missing_range']


def test_ordered_unique_exclusions_and_fraction_arithmetic():
    data=report().model_dump(mode='json')
    data['key_analysis']['statistics']['eligible_event_ids']=list(reversed(data['key_analysis']['statistics']['eligible_event_ids']))
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)
    from composerlint.analysis_models import KeyEvidenceStatistics,EventExclusion
    with pytest.raises(ValidationError):EventExclusion(event_id='e',ineligible_intervals=[{'start':{'numerator':1,'denominator':1},'end':{'numerator':2,'denominator':1}},{'start':{'numerator':2,'denominator':1},'end':{'numerator':3,'denominator':1}}],reason_codes=['not_harmony_eligible'],region_ids=[],source_segment_ids=[])
