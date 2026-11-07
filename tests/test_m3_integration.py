from pathlib import Path
from copy import deepcopy
import hashlib,json,subprocess,sys
import pytest
from pydantic import ValidationError
from composerlint.service import analyze_score,inspect_measure,analyze_normalized
from composerlint.engine.parser import load_score
from composerlint.engine.source_context import read_source_context
from composerlint.analysis_models import AnalysisReport,MeasureInspection
F=Path(__file__).parent/'fixtures/m3'
@pytest.mark.parametrize('path',sorted([*F.glob('*.musicxml'),*F.glob('*.mid')]),ids=lambda p:p.name)
def test_all_authored_reports_views_and_source(path):
    before=hashlib.sha256(path.read_bytes()).hexdigest();options={}
    r=analyze_score(path,options)
    assert r.score.source_sha256==before
    assert r.score.canonical_json()==load_score(path,options).canonical_json()
    assert r.canonical_json()==analyze_score(path,options).canonical_json()
    assert AnalysisReport.model_validate_json(r.canonical_json()).canonical_json()==r.canonical_json()
    assert len(r.coverage)==12 and all(c.implementation_state=='implemented' for c in r.coverage[:11])
    assert r.coverage[-1].implementation_state=='implemented' and r.style_analysis is not None
    for part in r.score.parts:
        for m in part.measures:
            v=inspect_measure(r,m.ordinal,part.id)
            assert v.coverage==r.coverage and v.key_analysis==r.key_analysis
            assert MeasureInspection.model_validate_json(v.canonical_json()).canonical_json()==v.canonical_json()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before

def first_evidence(data,kind):
    for field in ('diagnostics','harmony','cadences','limitations'):
        for record in data[field]:
            for ev in record.get('evidence',[]):
                if ev['kind']==kind:return ev
    raise AssertionError(kind)
@pytest.mark.parametrize('case',[
    'interval_motion','interval_quality','range_owner','range_bounds','bass','root','inversion','noneligible',
    'roman_degree','roman_figure','seventh_wrong_voice','seventh_later','cue_duration','cue_barline',
    'h_missing','segment_missing','stage_parent','duplicate_unit','key_context_claim','missing_h_evidence'])
def test_mutation_semantics(case):
    name='fifths_positive' if case.startswith('interval') else ('range_positive' if case.startswith('range') else ('seventh_positive_up' if case.startswith('seventh') else ('cadence_barline' if case=='cue_barline' else 'cadence_silence_exact')))
    options={'ranges':{'p0001':{'minimum':60,'maximum':84}}} if case.startswith('range') else {}
    r=analyze_score(F/(name+'.musicxml'),options);data=r.model_dump(mode='json')
    if case.startswith('interval'):
        ev=first_evidence(data,'interval_motion')
        if case=='interval_motion':ev['chromatic_deltas'][0]=3
        else:ev['before']['interval']['quality']='DIMINISHED'
    elif case.startswith('range'):
        ev=first_evidence(data,'range_comparison')
        if case=='range_owner':ev['config_owner_id']='p0001:v0001'
        else:ev['minimum']=58
    elif case in ('bass','root','inversion','noneligible','roman_degree','roman_figure','missing_h_evidence'):
        h=data['harmony'][0];ev=h['evidence'][0]
        if case=='bass':ev['bass_pitch']=deepcopy(h['sounding_pitches'][1])
        if case=='root':ev['root']='D'
        if case=='inversion':ev['inversion']=1
        if case=='noneligible':h['event_ids'][-1]=data['harmony'][-1]['event_ids'][-1]
        if case=='roman_degree':ev['scale_degree']=2
        if case=='roman_figure':ev['figure']='II';h['roman_numeral']='II'
        if case=='missing_h_evidence':h['evidence']=[]
    elif case.startswith('seventh'):
        ev=first_evidence(data,'seventh_resolution')
        ev['successor_event_id']='p0002:v0001:e000002' if case=='seventh_wrong_voice' else 'p0004:v0001:e000001'
    elif case.startswith('cue'):
        ev=first_evidence(data,'cadence_ending')
        if case=='cue_duration':ev['silence_duration']={'numerator':3,'denominator':4}
        else:ev['barlines'][0]['barline_type']='light-heavy'
    elif case=='h_missing':data['cadences'][0]['harmony_ids']=['h999999']
    elif case=='segment_missing':data['harmony'][0]['scope']['references']['source_segment_ids']=['p0001:v0001:s999999']
    elif case=='stage_parent':data['coverage'][3]['stages'][0]['checked_count']-=1
    elif case=='duplicate_unit':
        ex=data['coverage'][8]['exclusions'][0];ex['unit_ids'].append(ex['unit_ids'][0]);ex['count']+=1
    elif case=='key_context_claim':data['key_analysis']['selected_key']['tonic']='D'
    with pytest.raises((ValidationError,ValueError)):AnalysisReport.model_validate(data)

def test_view_support_across_measure_and_silence():
    r=analyze_score(F/'cadence_silence_exact.musicxml');v=inspect_measure(r,2)
    assert v.primary_harmony_ids==['h000002'] and 'h000001' in v.support_harmony_ids
    assert 'p0001:v0001:e000001' in v.parts[0].support_event_ids
    assert 'p0001:v0001:e000003' in v.parts[0].support_event_ids
    assert v.parts[0].primary_event_ids==['p0001:v0001:e000002']

def test_inspection_no_reanalysis_or_io(monkeypatch):
    r=analyze_score(F/'seventh_positive_up.musicxml')
    def fail(*a,**kw):raise AssertionError('unexpected reanalysis/I/O')
    monkeypatch.setattr('composerlint.service.load_score',fail);monkeypatch.setattr('composerlint.service.select_key',fail)
    monkeypatch.setattr('composerlint.service.read_source_context',fail)
    monkeypatch.setattr('composerlint.engine.harmony.execute',fail)
    monkeypatch.setattr('composerlint.engine.harmonic_checks.execute_sevenths',fail)
    monkeypatch.setattr(Path,'read_bytes',fail)
    v=inspect_measure(r,1);assert v.coverage==r.coverage

def test_normalize_and_estimate_once(monkeypatch):
    import composerlint.service as svc
    counts={'normalize':0,'key':0};load=svc.load_score;select=svc.select_key
    def loader(*a,**kw):counts['normalize']+=1;return load(*a,**kw)
    def selector(*a,**kw):counts['key']+=1;return select(*a,**kw)
    monkeypatch.setattr(svc,'load_score',loader);monkeypatch.setattr(svc,'select_key',selector)
    svc.analyze_score(F/'function_major.musicxml');assert counts=={'normalize':1,'key':1}

@pytest.mark.parametrize('name',['fifths_positive','seventh_positive_up','cadence_silence_exact'])
def test_fresh_process_canonical(name):
    code='from composerlint.service import analyze_score;print(analyze_score('+repr(str(F/(name+'.musicxml')))+').canonical_json())'
    a=subprocess.check_output([sys.executable,'-c',code]);b=subprocess.check_output([sys.executable,'-c',code]);assert a==b

def test_archived_m2_additive_defaults():
    p=Path(__file__).parents[1]/'docs/examples/M2-report.json';r=AnalysisReport.model_validate_json(p.read_text())
    assert r.analysis_metadata.musical_policy_id is None and not r.coverage[3].stages

@pytest.mark.parametrize('field',['figure','function','confidence','snapshot_omission'])
def test_standalone_harmony_mutations(field):
    data=analyze_score(F/'roman_chromatic.musicxml').model_dump(mode='json');h=data['harmony'][0];ev=h['evidence'][0]
    if field=='figure':ev['figure']='V';h['roman_numeral']='V'
    if field=='function':ev['function_branch']='dominant';ev['function_reason']=None;h['function']='dominant';h['unknown_reason']=None
    if field=='confidence':h['confidence']['level']='high'
    if field=='snapshot_omission':data['harmony']=[]
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)

@pytest.mark.parametrize('case',['absent_h','wrong_bounds','seventh_key','pair_key','range_owner_view'])
def test_exclusion_key_and_view_domains(case):
    if case=='range_owner_view':
        r=analyze_score(F/'range_positive.musicxml',{'ranges':{'p0001':{'minimum':60,'maximum':84}}});v=inspect_measure(r,1);data=v.model_dump(mode='json')
        ev=next(ev for d in data['diagnostics'] for ev in d['evidence'] if ev['kind']=='range_comparison');ev['config_owner_id']='p9999'
        with pytest.raises(ValidationError):MeasureInspection.model_validate(data)
        return
    name='chords_unknown_lower' if case in ('absent_h','wrong_bounds') else ('seventh_no_successor' if case=='seventh_key' else 'fifths_blocked_recovery')
    data=analyze_score(F/(name+'.musicxml')).model_dump(mode='json')
    c=data['coverage'][10 if case in ('absent_h','wrong_bounds') else (9 if case=='seventh_key' else 5)]
    u=json.loads(c['exclusions'][0]['unit_ids'][0])
    if case=='absent_h':u[2]=['h999999']
    elif case=='wrong_bounds':u[1][1]={'numerator':1,'denominator':2}
    elif case=='seventh_key':u[3]='p0001:v0001:e000001'
    else:u[3][0]={'numerator':1,'denominator':2}
    c['exclusions'][0]['unit_ids'][0]=json.dumps(u,sort_keys=True,separators=(',',':'))
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)

@pytest.mark.parametrize('name,rule',[('crossing_partial_order','voice_crossing'),('piano_multivoice','range_issue')])
def test_exclusion_units_follow_exact_time_before_traversal(name,rule):
    path=(F/(name+'.musicxml')) if name!='piano_multivoice' else F.parent/'piano_multivoice.musicxml'
    r=analyze_score(path);c=next(c for c in r.coverage if c.rule_id==rule)
    ix={e.id:e for p in r.score.parts for e in p.events}
    for ex in c.exclusions:
        starts=[]
        for text in ex.unit_ids:
            u=json.loads(text)
            if rule=='range_issue':starts.append(ix[u[1]].onset.fraction())
            else:
                from fractions import Fraction
                starts.append(Fraction(u[3][0]['numerator'],u[3][0]['denominator']))
        assert starts==sorted(starts)
