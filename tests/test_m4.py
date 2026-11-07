from pathlib import Path
from fractions import Fraction
from copy import deepcopy
import hashlib,json,subprocess,sys
import pytest
from pydantic import ValidationError
from composerlint import service
from composerlint.service import analyze_score,detect_style,get_style_evidence,inspect_measure
from composerlint.analysis_models import AnalysisReport,StyleAnalysis,StyleEvidenceView,MeasureInspection,STYLE_RULE_IDS,ProfileDefinition,score_indexes
from composerlint.styles.classical import get_profile
from composerlint.errors import ComposerLintError
from m4_helpers import xml,PROGRESSION
ROOT=Path(__file__).parents[1];F=ROOT/'tests/fixtures';M3=F/'m3';M4=F/'m4'
EXPECTED=json.loads((M4/'expectations.json').read_text())

def variant(tmp_path,collections=PROGRESSION,durations=None,**kw):
    p=tmp_path/'authored.musicxml';p.write_text(xml(collections,durations,**kw));return analyze_score(p)
def observation(r,rule,polarity=None):return [o for o in r.style_evidence if o.rule_id==rule and (polarity is None or o.polarity==polarity)]
def f(time):return time.fraction()


@pytest.mark.parametrize('windows,expected',[
    ([(0,Fraction(1,2)),(Fraction(1,2),1),(1,Fraction(3,2)),(Fraction(3,2),2),(2,3),(3,4),(4,5),(5,6)],
     [{'incomplete_sonority'},{'incomplete_sonority','unsupported_sonority'},
      {'incomplete_sonority','unsupported_sonority','missing_key'},
      {'incomplete_sonority','missing_key'},{'missing_key'},
      {'incomplete_sonority'},{'incomplete_sonority'},set()]),
    ([(Fraction(1,4),Fraction(5,4)),(Fraction(5,4),Fraction(7,4)),(Fraction(5,2),Fraction(7,2)),(5,6)],
     [{'incomplete_sonority','unsupported_sonority','missing_key'},
      {'incomplete_sonority','unsupported_sonority','missing_key'},
      {'incomplete_sonority','missing_key'},set()]),
    ([],[]),
],ids=['aligned-boundaries','crossing-boundaries-and-gaps','no-omissions'])
def test_omission_exclusions_keep_all_overlap_reasons_with_one_decode_per_unit(monkeypatch,windows,expected):
    from types import SimpleNamespace as Row
    from composerlint.engine.musical import unit_key
    from composerlint.styles import classical
    def exclusion(reason,spans):
        return Row(reason_code=reason,unit_ids=[unit_key('harmonic_span',[Fraction(a),Fraction(b)]) for a,b in spans])
    coverage=[Row(exclusions=[
        exclusion('incomplete_sonority',[(3,5),(0,1),(1,2)]),
        exclusion('unsupported_sonority',[(Fraction(1,2),Fraction(3,2))]),
        Row(reason_code='missing_key',unit_ids=[unit_key('pitched_event','not-a-harmonic-span')]),
        Row(reason_code='missing_key',unit_ids=None),
    ]),Row(exclusions=[exclusion('missing_key',[(1,3)])])]
    loads=classical.json.loads;decoded=[]
    def counted(text):
        decoded.append(text);return loads(text)
    monkeypatch.setattr(classical.json,'loads',counted)
    rows=list(classical._omission_exclusions(coverage,windows))
    assert [(a,b) for a,b,_ in rows]==windows
    assert [set(codes) for _,_,codes in rows]==expected
    assert len(decoded)==6


def test_omission_overlap_index_keeps_source_order_and_exact_point_boundaries():
    from composerlint.styles.classical import _overlap_windows
    # Input traversal order intentionally differs from onset order. Nested held
    # notes, simultaneous entries and inspection-only points all stay visible.
    intervals=[(2,4,'later'),(0,5,'held'),(1,3,'inner'),(2,2,'point'),(2,4,'simultaneous')]
    windows=[(0,1),(1,2),(2,3),(3,4),(4,5),(5,6)]
    assert [values for _,_,values in _overlap_windows(intervals,windows)]==[
        ['held'],['held','inner'],['later','held','inner','simultaneous'],
        ['later','held','simultaneous'],['held'],[],
    ]
    assert list(_overlap_windows(intervals,[(Fraction(3,2),Fraction(5,2))]))==[
        (Fraction(3,2),Fraction(5,2),['later','held','inner','point','simultaneous'])]

def test_T01_profile_and_archives():
    p=get_profile();assert (p.profile_id,p.profile_version,p.policy_id)==('classical','1.0','m4_classical_compatibility_v1')
    assert [d.rule_id for d in p.rules]==list(STYLE_RULE_IDS)
    p.rules.clear();assert len(get_profile().rules)==6
    d=get_profile().model_dump(mode='json');d['rules'].reverse()
    with pytest.raises(ValidationError):ProfileDefinition.model_validate(d)
    for name in ('M2-report','M3-report'):
        r=AnalysisReport.model_validate_json((ROOT/f'docs/examples/{name}.json').read_text())
        assert r.style_analysis is None and r.analysis_metadata.style_policy_id is None
        assert inspect_measure(r,1).style_analysis is None
        for fn in (detect_style,get_style_evidence):
            with pytest.raises(ComposerLintError,match='style analysis unavailable in this snapshot'):fn(r)

@pytest.mark.parametrize('case',list(EXPECTED))
def test_T02_T04_T11_authored_gates(case):
    r=analyze_score(M4/(case+'.musicxml'));s=detect_style(r);e=EXPECTED[case]
    assert (s.tonal_episode_count,s.raw_qualifying_span_count,s.cadence_candidate_count)==(e['N'],e['raw'],e['C'])
    assert (f(s.pitched_duration),f(s.recognized_tonal_duration),f(s.recognized_tonal_ratio))==tuple(Fraction(e[x]) for x in ('P','Q','ratio'))
    assert s.state==e['state'] and s.confidence.level=='low'
    assert [h.chord_label for h in r.harmony[-4:]]==['C:major','D:minor','G:major','C:major']
    assert [h.roman_numeral for h in r.harmony[-4:]]==['I','ii','V','I']
    assert [c.kind for c in r.cadences]==['authentic']
    assert len(observation(r,'parallel_fifths','conflicting'))==e['fifths']
    assert r.coverage[-1].flagged_count==int(e['fifths']>0)
    assert s.failed_gates==(['style_tonal_duration_insufficient'] if case=='below_sixty' else [])
    assert [d.category for d in r.diagnostics].count('theory_issue')==e['fifths']
    for o in r.style_evidence:assert o.confidence.level=='low' and o.severity=='info'
    assert s.supportive_observation_count>0

@pytest.mark.parametrize('path',[F/'all_rest.musicxml',F/'m2/sparse.musicxml'])
def test_T03_insufficient(path):
    r=analyze_score(path);s=r.style_analysis
    assert s.state=='insufficient_evidence' and s.confidence.level=='low'
    assert r.coverage[-1].model_dump()['candidate_count']==1 and r.coverage[-1].checked_count==1
    assert r.coverage[-1].flagged_count==0
    if path.name=='all_rest.musicxml':
        assert f(s.pitched_duration)==0 and s.recognized_tonal_ratio is None and s.pitched_intervals==[]
        assert 'style_no_pitched_duration' in s.failed_gates
    assert 'style_tonal_episode_count_insufficient' in s.failed_gates

@pytest.mark.parametrize('case',['three','no_cadence'])
def test_T04_other_gates(tmp_path,case):
    r=variant(tmp_path,PROGRESSION[::] if case=='no_cadence' else [PROGRESSION[0],*PROGRESSION[2:]],independent=False)
    if case=='no_cadence':r=variant(tmp_path,[*PROGRESSION,['C3']],independent=False)
    s=r.style_analysis
    assert s.failed_gates==(['style_cadence_evidence_insufficient'] if case=='no_cadence' else ['style_tonal_episode_count_insufficient'])

@pytest.mark.parametrize('kind',['reattack','inversion','doubling','async','tie'])
def test_T05_equivalent_unions_episodes(tmp_path,kind):
    collections=deepcopy(PROGRESSION);durations=[1]*4
    if kind in ('reattack','inversion','tie'):
        collections.insert(0,deepcopy(collections[0]));durations=[Fraction(1,2),Fraction(1,2),1,1,1]
        if kind=='inversion':collections[1]=['E3','G4','C5']
    if kind=='doubling':collections=[c+[c[0][:-1]+'5'] for c in collections]
    if kind=='async':
        collections.insert(0,['C3','E5','G4']);durations=[Fraction(1,2),Fraction(1,2),1,1,1]
    p=tmp_path/'equivalent.musicxml';text=xml(collections,durations)
    if kind=='tie':
        import xml.etree.ElementTree as ET
        root=ET.fromstring(text)
        for part in root.findall('part'):
            for j,measure in enumerate(part.findall('measure')[:2]):ET.SubElement(measure.find('note'),'tie',type='start' if j==0 else 'stop')
        text=ET.tostring(root,encoding='unicode')
    p.write_text(text);r=analyze_score(p);s=r.style_analysis
    assert (f(s.pitched_duration),f(s.recognized_tonal_duration),s.tonal_episode_count)==(4,4,4)
    assert s.raw_qualifying_span_count==(4 if kind in ('tie','doubling') else 5)
    assert s.pitched_intervals==s.tonal_intervals and len(s.pitched_intervals)==1
    assert r.key_analysis.statistics.source_pitched_duration.fraction()>=12 # event sum is distinct from clock-time 4
    assert [h.id for h in r.harmony]==[i for e in s.episodes for i in e.harmony_ids]

@pytest.mark.parametrize('middle',[None,'C3'])
def test_T05_breaks_and_pitched_unknown(tmp_path,middle):
    r=variant(tmp_path,[PROGRESSION[0],[middle],PROGRESSION[0]],independent=False)
    s=r.style_analysis;assert s.tonal_episode_count==2
    assert f(s.pitched_duration)==(2 if middle is None else 3) and f(s.recognized_tonal_duration)==2
    assert len(s.pitched_intervals)==(2 if middle is None else 1)
    if middle:assert s.omissions

@pytest.mark.parametrize('name',['unknown_pitch.musicxml','microtone.musicxml','invalid_ties.musicxml'])
def test_T05_blocked_denominator(name):
    p=F/name
    if not p.exists():pytest.fail(f'Authored source not found: {name}')
    r=analyze_score(p)
    from composerlint.models import PitchedEvent
    events=[e for part in r.score.parts for e in part.events if isinstance(e,PitchedEvent) and e.duration.fraction()>0]
    # Independent endpoint sweep counts clock time once, including unavailable notes.
    cuts=sorted({x for e in events for x in (e.onset.fraction(),e.end.fraction())})
    expected=sum((b-a for a,b in zip(cuts,cuts[1:]) if any(e.onset.fraction()<=a and e.end.fraction()>=b for e in events)),Fraction())
    assert r.style_analysis.pitched_duration.fraction()==expected
    assert r.style_analysis.omissions

@pytest.mark.parametrize('name',['chords_incomplete_cluster','chords_spelling','roman_inversions','function_six_four','function_minor','function_negative','chords_neighbor','chords_passing','roman_chromatic','roman_minor','chords_sevenths'])
def test_T06_cautious_harmony(name):
    r=analyze_score(M3/(name+'.musicxml'));s=r.style_analysis
    assert not observation(r,'harmony_classification','conflicting') and not observation(r,'harmonic_function','conflicting')
    hids={i for e in s.episodes for i in e.harmony_ids}
    for h in r.harmony:
        e=h.evidence[0]
        if e.function_reason in ('chromatic_function_unsupported','tonic_six_four_context_required') or 'transient_surface_sonority' in e.surface_flags:
            assert h.id not in hids
        if e.function_reason=='context_dependent_function' and e.chord_reason is None and e.roman_reason is None and not e.surface_flags:
            assert h.id in hids
            assert not any(h.id in o.source_references.harmony_ids for o in observation(r,'harmonic_function','supportive'))
    if name in ('chords_neighbor','chords_passing'):
        assert not any(o.polarity=='conflicting' for o in r.style_evidence)

@pytest.mark.parametrize('name',['roman_missing_key','seventh_override_conflict','seventh_minor_missing_key'])
def test_T07_key_uncertain(name):
    r=analyze_score(M3/(name+'.musicxml'))
    assert r.style_analysis.tonal_episode_count==0 and not r.cadences
    assert not observation(r,'harmony_classification','supportive')
    assert r.style_analysis.failed_gates[0] in ('missing_key','key_context_uncertain')

@pytest.mark.parametrize('name',['conflicting_parts','late_key','key_change','uniform_distribution'])
def test_T07_m2_uncertainty(name):
    r=analyze_score(F/'m2'/(name+'.musicxml'))
    assert r.style_analysis.failed_gates[0] in ('missing_key','key_context_uncertain')

def test_T07_override_follows_guard():
    r=analyze_score(M3/'roman_missing_key.musicxml',{'key_override':{'tonic':'C','mode':'major'}})
    assert r.style_analysis.key_prerequisite_reason is None
    assert r.style_analysis.tonal_episode_count==2

@pytest.mark.parametrize('name,kinds',[
 ('cadence_authentic_continuing',[]),('cadence_authentic_end',['authentic']),('cadence_half',['half']),
 ('cadence_deceptive',['deceptive']),('cadence_plagal',['plagal']),('cadence_inversions',['authentic']),
 ('cadence_silence_exact',['authentic']),('cadence_silence_short',[]),('cadence_barline',['authentic'])])
def test_T08_cadence_source(name,kinds):
    r=analyze_score(M3/(name+'.musicxml'))
    assert [c.kind for c in r.cadences]==kinds
    assert r.style_analysis.cadence_candidate_count==len(kinds)
    for o in observation(r,'cadence_detection','supportive'):
        c=next(c for c in r.cadences if c.id in o.source_references.cadence_ids)
        assert o.evidence==c.evidence and c.evidence[0].cues

def test_T08_multiple_cues_once(tmp_path):
    r=variant(tmp_path,bars=[4])
    assert len(r.cadences[0].evidence[0].cues)==2 and r.style_analysis.cadence_candidate_count==1

@pytest.mark.parametrize('rule,name,count',[
 ('parallel_fifths','fifths_positive',1),('parallel_fifths','fifths_compound',1),('parallel_fifths','fifths_negative',0),
 ('parallel_fifths','fifths_oblique',0),('parallel_fifths','fifths_static',0),('parallel_fifths','fifths_altered',0),('parallel_fifths','fifths_spelling',0),('parallel_fifths','fifths_orientation_reversal',0),
 ('parallel_octaves','octaves_positive',1),('parallel_octaves','octaves_compound',1),('parallel_octaves','octaves_unison',0),('parallel_octaves','octaves_contrary',0),('parallel_octaves','octaves_static',0),('parallel_octaves','octaves_enharmonic',0)])
def test_T09_parallel_sources(rule,name,count):
    r=analyze_score(M3/(name+'.musicxml'));obs=observation(r,rule,'conflicting')
    assert len(obs)==count and not observation(r,rule,'supportive')
    for o in obs:
        d=next(d for d in r.diagnostics if d.id in o.source_references.diagnostic_ids)
        assert o.evidence==d.evidence and d.category=='theory_issue'
    if not count:assert next(a for a in r.style_analysis.applicability if a.rule_id==rule).availability=='available'

@pytest.mark.parametrize('name',['fifths_blocked_recovery','octaves_blocked','seventh_chordal_lane'])
def test_T09_skipped_not_negative(name):
    r=analyze_score(M3/(name+'.musicxml'))
    assert any(a.unavailable for a in r.style_analysis.applicability)
    assert all(o.polarity!='conflicting' or o.source_references.diagnostic_ids for o in r.style_evidence)

@pytest.mark.parametrize('name,support,conflict',[
 ('seventh_negative_down',1,0),('seventh_negative_whole_step',1,0),('seventh_positive_up',0,1),
 ('seventh_repeated_pitch',0,1),('seventh_held_delayed',0,0),('seventh_held_recognized',0,0),('seventh_exception',0,0),
 ('seventh_rest_gap',0,0),('seventh_wrong_voice',0,1),('seventh_unknown_next',0,0),('seventh_no_successor',0,0),('seventh_tie_inversion',1,0)])
def test_T10_seventh_sources(name,support,conflict):
    r=analyze_score(M3/(name+'.musicxml'))
    assert len(observation(r,'unresolved_seventh','supportive'))==support
    assert len(observation(r,'unresolved_seventh','conflicting'))==conflict
    for o in observation(r,'unresolved_seventh','supportive'):
        assert o.source_references.limitation_ids and o.source_references.diagnostic_ids
    keys=[o.unit_key for o in observation(r,'unresolved_seventh')];assert len(keys)==len(set(keys))

def test_T12_views_coverage():
    r=analyze_score(M4/'four_episodes.musicxml');allview=get_style_evidence(r)
    assert allview.style_analysis==r.style_analysis and len(allview.definitions)==6
    for rule in STYLE_RULE_IDS:
        v=get_style_evidence(r,rule_id=rule)
        assert v.observations==observation(r,rule) and v.style_analysis==r.style_analysis
        assert StyleEvidenceView.model_validate_json(v.canonical_json()).canonical_json()==v.canonical_json()
        row=v.applicability[0];assert row.upstream_coverage==[next(c for c in r.coverage if c.rule_id==rule)]
    empty=get_style_evidence(r,rule_id='parallel_octaves');assert not empty.observations and empty.applicability[0].availability=='available'
    for rule in ('nonsense','range_issue','classical_style_evidence'):
        with pytest.raises(ComposerLintError,match='invalid_options'):get_style_evidence(r,rule_id=rule)
    for i in range(1,5):
        v=inspect_measure(r,i);assert v.style_analysis==r.style_analysis and v.style_analysis.context=='global'
        assert v.coverage==r.coverage
        assert MeasureInspection.model_validate_json(v.canonical_json()).canonical_json()==v.canonical_json()
        assert set(o.id for o in v.style_evidence)<=set(o.id for o in r.style_evidence)

def test_T13_once_then_no_io(tmp_path,monkeypatch):
    from composerlint.engine import voice_leading,harmony,harmonic_checks
    path=tmp_path/'source.musicxml';path.write_text(xml(PROGRESSION));counts={}
    for module,name in [(service,'load_score'),(service,'select_key'),(voice_leading,'execute'),(harmony,'execute'),(harmonic_checks,'execute_sevenths'),(harmonic_checks,'execute_cadences')]:
        original=getattr(module,name);tag=module.__name__+'.'+name
        def spy(*a,_original=original,_tag=tag,**kw):counts[_tag]=counts.get(_tag,0)+1;return _original(*a,**kw)
        monkeypatch.setattr(module,name,spy)
    s=detect_style(path);assert len(counts)==6 and set(counts.values())=={1}
    counts.clear();r=analyze_score(path);assert set(counts.values())=={1}
    path.unlink()
    def blocked(*a,**k):raise AssertionError('lookup reran I/O or musical execution')
    for module,name in [(service,'load_score'),(service,'read_source_context'),(service,'select_key'),(voice_leading,'execute'),(harmony,'execute'),(harmonic_checks,'execute_sevenths'),(harmonic_checks,'execute_cadences')]:monkeypatch.setattr(module,name,blocked)
    assert detect_style(r)==s
    get_style_evidence(r);inspect_measure(r,1)
    for fn in (detect_style,get_style_evidence):
        with pytest.raises(ComposerLintError,match='options must be absent'):fn(r,{})
    owned=detect_style(r);owned.failed_gates.append('style_no_pitched_duration');owned.options.ranges['absent']=None
    assert detect_style(r)==s
    v=get_style_evidence(r);v.observations.clear();v.profile.rules.clear();v.harmony.clear()
    assert get_style_evidence(r).observations==r.style_evidence

@pytest.mark.parametrize('fn',[detect_style,get_style_evidence])
@pytest.mark.parametrize('source,options,code',[(None,None,'invalid_options'),('https://example.com/x.musicxml',None,'unsupported_format'),('/absent/music.musicxml',None,'file_not_found'),(str(M4/'four_episodes.musicxml'),{'target_style':'romantic'},'unsupported_style')])
def test_T13_errors(fn,source,options,code):
    with pytest.raises(ComposerLintError) as e:fn(source,options)
    assert e.value.code==code

@pytest.mark.parametrize('mutation',['ratio','state','count','union','polarity','source','dedup','confidence','coverage','applicability','profile_version','policy','omission'])
def test_T01_malformed_rejected(mutation):
    r=analyze_score(M4/'sixty_percent.musicxml');d=r.model_dump(mode='json');s=d['style_analysis']
    if mutation=='ratio':s['recognized_tonal_ratio']={'numerator':1,'denominator':1}
    elif mutation=='state':s['state']='insufficient_evidence'
    elif mutation=='count':s['tonal_episode_count']=5
    elif mutation=='union':s['pitched_intervals'].append(s['pitched_intervals'][0])
    elif mutation=='polarity':next(o for o in d['style_evidence'] if o['polarity']=='conflicting')['polarity']='supportive'
    elif mutation=='source':d['style_evidence'][-1]['source_references']['harmony_ids']=['h999999']
    elif mutation=='dedup':d['style_evidence'].append(deepcopy(d['style_evidence'][-1]));d['style_evidence'][-1]['id']=f"sty{len(d['style_evidence']):06d}"
    elif mutation=='confidence':s['confidence']['level']='medium'
    elif mutation=='coverage':d['coverage'][-1]['flagged_count']=0
    elif mutation=='applicability':s['applicability'][0]['upstream_coverage'][0]['checked_count']=0
    elif mutation=='profile_version':s['profile_version']='1.1'
    elif mutation=='policy':d['analysis_metadata']['style_policy_id']=None
    elif mutation=='omission':s['omissions']=[]
    with pytest.raises(ValidationError):AnalysisReport.model_validate(d)

@pytest.mark.parametrize('mutation',['ratio','state','confidence','availability','union'])
def test_T01_standalone_result_rejects(mutation):
    d=detect_style(M4/'four_chordal.musicxml').model_dump(mode='json')
    if mutation=='ratio':d['recognized_tonal_ratio']={'numerator':0,'denominator':1}
    elif mutation=='state':d['state']='insufficient_evidence'
    elif mutation=='confidence':d['confidence']['level']='high'
    elif mutation=='availability':d['applicability'][0]['availability']='unavailable'
    elif mutation=='union':d['tonal_intervals']=[]
    with pytest.raises(ValidationError):StyleAnalysis.model_validate(d)

def test_T14_fresh_process():
    code="""
import hashlib,json
from composerlint.service import analyze_score,detect_style,get_style_evidence,inspect_measure
r=analyze_score('tests/fixtures/m4/sixty_percent.musicxml')
values=[r,detect_style(r),get_style_evidence(r),get_style_evidence(r,rule_id='parallel_fifths'),inspect_measure(r,2)]
print(json.dumps([hashlib.sha256(v.canonical_json().encode()).hexdigest() for v in values]))
"""
    runs=[subprocess.check_output([sys.executable,'-c',code],cwd=ROOT,text=True) for _ in range(2)]
    assert runs[0]==runs[1]

def test_T14_bounded_wording():
    r=analyze_score(M4/'four_episodes.musicxml');s=r.style_analysis
    assert 'does not establish' in s.explanation and 'may be intentional' in observation(r,'parallel_fifths','conflicting')[0].explanation
    assert 'Mozart' not in r.canonical_json()
    text=' '.join([s.explanation,s.confidence.explanation,*[o.explanation for o in r.style_evidence]])
    assert 'probability' not in text and 'similarity' not in text
    assert 'does not imply non-Classical' in detect_style(F/'all_rest.musicxml').explanation


@pytest.mark.parametrize('name',list(json.loads((ROOT/'docs/M4-upstream-baseline.json').read_text())))
def test_T15_approved_semantics(name):
    from m4_regression import semantic_hash
    expected=json.loads((ROOT/'docs/M4-upstream-baseline.json').read_text())[name]
    r=analyze_score(ROOT/name)
    assert semantic_hash(r.model_dump(mode='json'))==expected


def test_T06_failed_bounded_guard_never_conflicts(tmp_path):
    # E→F with no following outer collection cannot establish a passing/neighbor guard.
    r=variant(tmp_path,[PROGRESSION[0],['C3','F4','G4']])
    assert all('transient_surface_sonority' not in h.evidence[0].surface_flags for h in r.harmony)
    assert not any(o.polarity=='conflicting' for o in r.style_evidence)
    assert r.style_analysis.tonal_episode_count==1


@pytest.mark.parametrize('grace',[True,False])
def test_T05_zero_points_do_not_inflate(tmp_path,grace):
    import xml.etree.ElementTree as ET
    source=ET.fromstring(xml(PROGRESSION))
    for part in source.findall('part'):
        note=ET.fromstring('<note>'+('<grace/>' if grace else '')+'<pitch><step>C</step><octave>6</octave></pitch>'+('' if grace else '<duration>0</duration>')+'<voice>1</voice></note>')
        part.find('measure').insert(1,note)
    path=tmp_path/'points.musicxml';path.write_text(ET.tostring(source,encoding='unicode'))
    s=detect_style(path)
    assert (s.tonal_episode_count,s.raw_qualifying_span_count,s.pitched_duration.fraction(),s.recognized_tonal_duration.fraction())==(4,4,4,4)


@pytest.mark.parametrize('mutation',['missing_observation','changed_polarity','missing_source','altered_harmony','confidence'])
def test_T01_view_mutations(mutation):
    d=get_style_evidence(M4/'four_episodes.musicxml').model_dump(mode='json')
    if mutation=='missing_observation':d['observations'].pop()
    elif mutation=='changed_polarity':next(o for o in d['observations'] if o['polarity']=='conflicting')['polarity']='unavailable'
    elif mutation=='missing_source':d['diagnostics']=[]
    elif mutation=='altered_harmony':d['harmony'][0]['evidence'][0]['figure']='V'
    elif mutation=='confidence':d['observations'][0]['confidence']['level']='medium'
    with pytest.raises(ValidationError):StyleEvidenceView.model_validate(d)


def test_T09_hidden_fifth_checked_negative(tmp_path):
    r=variant(tmp_path,[['C4','E4'],['D4','A4']])
    assert r.coverage[5].checked_count==1 and r.coverage[5].flagged_count==0
    assert not observation(r,'parallel_fifths','conflicting') and not observation(r,'parallel_fifths','supportive')
    assert next(a for a in r.style_analysis.applicability if a.rule_id=='parallel_fifths').availability=='available'


def test_T07_override_does_not_rescue_conflicting_metadata():
    r=analyze_score(M3/'seventh_override_conflict.musicxml',{'key_override':{'tonic':'C','mode':'major'}})
    assert r.key_analysis.selection_method=='user_override' and r.style_analysis.key_prerequisite_reason=='key_context_uncertain'
    assert r.style_analysis.tonal_episode_count==0 and r.style_analysis.cadence_candidate_count==0
    assert not observation(r,'harmony_classification','supportive')


@pytest.mark.parametrize('mutation',['confidence','rule','unit_key','partial_version'])
def test_T01_standalone_observation_rejects(mutation):
    from composerlint.analysis_models import StyleEvidence
    d=next(o for o in analyze_score(M4/'four_episodes.musicxml').style_evidence if o.rule_id=='parallel_fifths').model_dump(mode='json')
    if mutation=='confidence':d['confidence']['level']='medium'
    elif mutation=='rule':d['rule_id']='voice_crossing'
    elif mutation=='unit_key':d['unit_key']='not canonical JSON'
    elif mutation=='partial_version':d['profile_version']=None
    with pytest.raises(ValidationError):StyleEvidence.model_validate(d)


@pytest.mark.parametrize('mutation',['missing_m3_policy','orphan_style_result'])
def test_T01_milestone_prerequisite_rejected(mutation):
    if mutation=='missing_m3_policy':
        d=analyze_score(M4/'four_chordal.musicxml').model_dump(mode='json');d['analysis_metadata']['musical_policy_id']=None
    else:
        d=json.loads((ROOT/'docs/examples/M2-report.json').read_text());d['style_analysis']=detect_style(M4/'four_chordal.musicxml').model_dump(mode='json')
    with pytest.raises(ValidationError):AnalysisReport.model_validate(d)
