"""Independent M5 boundary/projection controls against existing source facts."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,subprocess,sys
import pytest
import jsonschema
from pydantic import ValidationError
from composerlint import service
from composerlint.analysis_models import AnalysisReport,STYLE_RULE_IDS,score_indexes,ReferenceSet
from composerlint.focused import CheckHarmonyView,CheckVoiceLeadingView,HARMONY_RULES,VOICE_RULES
from composerlint.errors import ComposerLintError
from composerlint.tool_contracts import LEGACY_TOOLS as TOOLS,dispatch,input_schema,output_schema,DomainErrorResult
ROOT=Path(__file__).parents[1];F=ROOT/'tests/fixtures'
NAMES=[t.name for t in TOOLS]

def args(name,path=F/'m4/four_episodes.musicxml',**kw):
    value={'path':str(path)}
    if name=='inspect_measure':value['measure_index']=1
    return dict(value,**kw)

def direct(name,path,options=None):
    if name=='analyze_score':return service.analyze_score(path,options)
    if name=='inspect_measure':return service.inspect_measure(service.analyze_score(path,options),1)
    return getattr(service,name)(path,options)

@pytest.mark.parametrize('tool',TOOLS,ids=lambda t:t.name)
@pytest.mark.parametrize('path',[F/'m4/four_episodes.musicxml',F/'m3/fifths_positive.mid',F/'ugly_ambiguous.mid'])
def test_T04_direct_equivalence_source_determinism(tool,path):
    before=path.read_bytes();one,error=dispatch(tool.name,args(tool.name,path));two,error2=dispatch(tool.name,args(tool.name,path,options=None))
    expected=direct(tool.name,path)
    assert not error and not error2
    assert one.canonical_json()==two.canonical_json()==expected.canonical_json()
    jsonschema.validate(one.model_dump(mode='json'),output_schema(tool))
    assert path.read_bytes()==before
    assert hashlib.sha256(before).hexdigest()==(one.source_sha256 if hasattr(one,'source_sha256') else one.score.source_sha256)

@pytest.mark.parametrize('tool',TOOLS,ids=lambda t:t.name)
def test_T03_published_schemas_and_defaults(tool):
    s=input_schema(tool);jsonschema.Draft202012Validator.check_schema(s);jsonschema.Draft202012Validator.check_schema(output_schema(tool))
    assert s['additionalProperties'] is False
    assert set(s['required'])==({'path','measure_index'} if tool.name=='inspect_measure' else {'path'})
    jsonschema.validate(args(tool.name),s);jsonschema.validate(args(tool.name,options=None),s)
    for invalid in [args(tool.name,extra=1),{**args(tool.name),'path':None},args(tool.name,options={'midi':{'quarter_length_divisors':[]}}),args(tool.name,options={'voice_order':['p0001:v0001','p0001:v0001']}),args(tool.name,options={'key_override':{'tonic':'C4','mode':'major'}})]:
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(invalid,s)

BAD=[{}, {'path':None}, {'path':True}, {'path':3}, {'path':[]}, {'path':'x','extra':1},
 {'path':'x','options':[]},{'path':'x','options':{'extra':1}}, {'path':'x','options':{'midi':{'quantize_post':1}}},
 {'path':'x','options':{'midi':{'quarter_length_divisors':[True]}}}, {'path':'x','options':{'midi':{'quarter_length_divisors':[1.0]}}},
 {'path':'x','options':{'midi':{'quarter_length_divisors':[0]}}}, {'path':'x','options':{'midi':{'quarter_length_divisors':[4,4]}}},
 {'path':'x','options':{'key_override':{'tonic':7,'mode':'major'}}}, {'path':'x','options':{'key_override':{'tonic':'c','mode':'major'}}},
 {'path':'x','options':{'ranges':{'p0001':{'minimum':2,'maximum':1}}}},
 {'path':'x','options':{'ranges':{'p0001':{'minimum':False,'maximum':100}}}},
 {'path':'x','options':{'voice_order':[]}}, {'path':'x','options':{'voice_order':['a','a']}}]
@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('bad',BAD)
def test_T03_invalid_arguments(name,bad):
    data=deepcopy(bad)
    if name=='inspect_measure':data['measure_index']=1
    result,error=dispatch(name,data)
    assert error and result.error.code=='invalid_options'
    jsonschema.validate(result.model_dump(mode='json'),output_schema(next(t for t in TOOLS if t.name==name)))
    assert set(result.model_dump())=={'schema_version','error'}

@pytest.mark.parametrize('index',[None,True,False,0,-1,1.0,'1',[]])
def test_T03_strict_measure(index):
    result,error=dispatch('inspect_measure',args('inspect_measure',measure_index=index))
    assert error and result.error.code=='invalid_options'

@pytest.mark.parametrize('name',NAMES)
def test_T07_unsupported_style_precedence(name):
    result,error=dispatch(name,args(name,options={'target_style':'romantic','midi':{'quantize_post':3}}))
    assert error and result.error.code=='unsupported_style' and result.error.reason=='only the classical profile is supported'

@pytest.mark.parametrize('name,extra,code',[
 ('analyze_score',{'path':'/nonexistent/composerlint.musicxml'},'file_not_found'),
 ('analyze_score',{'path':'https://example.com/score.musicxml'},'unsupported_format'),
 ('analyze_score',{'path':'file:///tmp/score.musicxml'},'unsupported_format'),
 ('analyze_score',{'path':''},'parse_failed'),
 ('inspect_measure',{'measure_index':1000},'measure_not_found'),
 ('inspect_measure',{'part_id':'absent'},'unknown_voice'),
 ('inspect_measure',{'part_id':1},'invalid_options'),
 ('get_style_evidence',{'rule_id':'range_issue'},'invalid_options'),
 ('get_style_evidence',{'rule_id':1},'invalid_options'),
 ('analyze_score',{'options':{'voice_order':['absent']}},'unknown_voice'),
 ('analyze_score',{'options':{'ranges':{'absent':{'minimum':0,'maximum':100}}}},'unknown_voice')])
def test_T07_domain_codes(name,extra,code):
    result,error=dispatch(name,args(name,**extra));assert error and result.error.code==code

@pytest.mark.parametrize('name',NAMES)
def test_T08_malformed_source_bytes(name,tmp_path):
    p=tmp_path/'broken.musicxml';p.write_bytes(b'<score-partwise>');before=p.read_bytes()
    result,error=dispatch(name,args(name,p));assert error and result.error.code=='parse_failed';assert p.read_bytes()==before

@pytest.mark.parametrize('rule',STYLE_RULE_IDS)
def test_T04_style_rule_filters(rule):
    result,error=dispatch('get_style_evidence',args('get_style_evidence',rule_id=rule));expected=service.get_style_evidence(F/'m4/four_episodes.musicxml',rule_id=rule)
    assert not error and result.canonical_json()==expected.canonical_json()
    assert [d.rule_id for d in result.definitions]==[rule]

@pytest.mark.parametrize('name',NAMES)
def test_T05_once_per_request(name,monkeypatch):
    calls={};targets=[(service,'load_score'),(service,'select_key')]
    from composerlint.engine import voice_leading,harmony,harmonic_checks
    targets += [(voice_leading,'execute'),(harmony,'execute'),(harmonic_checks,'execute_sevenths'),(harmonic_checks,'execute_cadences')]
    for obj,attr in targets:
        original=getattr(obj,attr);key=obj.__name__+'.'+attr;calls[key]=0
        def counted(*a,_original=original,_key=key,**k):calls[_key]+=1;return _original(*a,**k)
        monkeypatch.setattr(obj,attr,counted)
    result,error=dispatch(name,args(name));assert not error and set(calls.values())=={1}


def test_T05_completed_report_no_io_ownership(tmp_path,monkeypatch):
    p=tmp_path/'score.musicxml';p.write_bytes((F/'m3/fifths_ties.musicxml').read_bytes());report=service.analyze_score(p);canonical=report.canonical_json();p.unlink()
    def forbidden(*a,**k):raise AssertionError('unexpected source/executor access')
    from composerlint.engine import voice_leading,harmony,harmonic_checks
    from composerlint.styles import classical
    for obj,attr in [(service,'load_score'),(service,'read_source_context'),(service,'select_key'),(voice_leading,'execute'),(harmony,'execute'),(harmonic_checks,'execute_sevenths'),(harmonic_checks,'execute_cadences')]:monkeypatch.setattr(obj,attr,forbidden)
    for fn in [service.check_harmony,service.check_voice_leading,service.detect_style,service.get_style_evidence]:
        view=fn(report);assert view
        with pytest.raises(ComposerLintError,match='options must be absent'):fn(report,{})
        if hasattr(view,'parts'):view.parts[0].events.clear()
    assert service.inspect_measure(report,1) and report.canonical_json()==canonical

@pytest.mark.parametrize('fixture',['fifths_ties','crossing_positive','range_chord_tie','seventh_positive_up','cadence_gap_silence','roman_missing_key'])
def test_T06_truthful_groups_source_closure(fixture):
    options={'voice_order':['p0001:v0001','p0002:v0001']} if fixture=='crossing_positive' else ({'ranges':{'p0001':{'minimum':60,'maximum':72}}} if fixture=='range_chord_tie' else None)
    r=service.analyze_score(F/f'm3/{fixture}.musicxml',options)
    for fn,group,cls in [(service.check_harmony,HARMONY_RULES,CheckHarmonyView),(service.check_voice_leading,VOICE_RULES,CheckVoiceLeadingView)]:
        v=fn(r);assert cls.model_validate_json(v.canonical_json())==v
        assert v.diagnostics==[d for d in r.diagnostics if d.rule_id in group]
        assert v.coverage==[next(c for c in r.coverage if c.rule_id==rule) for rule in group]
        assert v.limitations==[l for l in r.limitations if any(rule in group for rule in l.related_rule_ids)]
        ix=score_indexes(v);parent=score_indexes(r.score)
        for domain in ReferenceSet.model_fields:
            assert all(obj==parent[domain][i] for i,obj in ix[domain].items()) if domain not in ('part_ids','voice_ids') else True
            assert set(ix[domain])|set(getattr(v.context_reference_ids,domain))==set(parent[domain])
        assert v.source_assumptions==r.score.assumptions and v.source_limitations==r.score.limitations
        if group==HARMONY_RULES:
            assert v.harmony==r.harmony and v.cadences==r.cadences and v.key_analysis==r.key_analysis
            assert v.context_coverage==r.coverage[:3]
        else:
            assert not any(key in v.model_dump() for key in ('harmony','cadences','style_evidence','key_analysis'))
    if fixture=='fifths_ties':
        v=service.check_voice_leading(r);ds=[d for d in v.diagnostics if d.category=='theory_issue'];assert ds==[]  # Source C4 stays held while G4 moves to A4: an oblique checked negative.
        # Authored tied source must retain the full chain including both bars.
        assert any(len(e.source_segment_ids)>1 for p in service.check_harmony(r).parts for e in p.events)


def test_T06_missing_configuration_and_historical():
    r=service.analyze_score(F/'m3/fifths_positive.musicxml');v=service.check_voice_leading(r)
    assert next(c for c in v.coverage if c.rule_id=='voice_crossing').reason_codes==['missing_voice_order']
    assert next(c for c in v.coverage if c.rule_id=='range_issue').reason_codes==['missing_range']
    for file in ['M2-report','M3-report']:
        old=AnalysisReport.model_validate_json((ROOT/f'docs/examples/{file}.json').read_text())
        assert service.check_harmony(old) and service.check_voice_leading(old)

@pytest.mark.parametrize('change',['diagnostic_group','coverage_order','segments','context_overlap'])
def test_T06_standalone_mutation_rejected(change):
    v=service.check_voice_leading(F/'m3/fifths_positive.musicxml');data=v.model_dump(mode='json')
    if change=='diagnostic_group':data['diagnostics'][0]['rule_id']='cadence_detection'
    elif change=='coverage_order':data['coverage'].reverse()
    elif change=='segments':data['parts'][0]['source_segments'].pop(0)
    else:data['context_reference_ids']['event_ids']=[data['parts'][0]['events'][0]['id']]
    with pytest.raises((ValidationError,KeyError)):CheckVoiceLeadingView.model_validate(data)

@pytest.mark.parametrize('fault',[RuntimeError('injected'),ValueError('implementation bug')])
def test_T07_internal_fault_not_domain(monkeypatch,fault):
    def fail(*a,**k):raise fault
    monkeypatch.setattr(service,'analyze_score',fail)
    with pytest.raises(type(fault),match=str(fault)):dispatch('analyze_score',args('analyze_score'))


def test_T07_result_validation_not_argument_error(monkeypatch):
    class Bad:
        def model_dump(self,**kw):return {}
    monkeypatch.setattr(service,'analyze_score',lambda *a,**k:Bad())
    with pytest.raises(ValidationError):dispatch('analyze_score',args('analyze_score'))


def test_T09_interleaved_changed_path(tmp_path):
    p=tmp_path/'score.musicxml';p.write_bytes((F/'m4/four_episodes.musicxml').read_bytes())
    first,_=dispatch('analyze_score',args('analyze_score',p));dispatch('analyze_score',args('analyze_score',F/'basic.mid',options={'midi':{'quantize_post':False}}))
    same,_=dispatch('analyze_score',args('analyze_score',p));assert first.canonical_json()==same.canonical_json()
    p.write_bytes((F/'all_rest.musicxml').read_bytes());changed,_=dispatch('analyze_score',args('analyze_score',p));assert changed.score.source_sha256!=first.score.source_sha256


def test_T08_import_has_no_application_io():
    script='''import sys,socket,builtins
old=builtins.open
writes=[]
def opening(file,mode="r",*a,**k):
 if any(x in str(mode) for x in "wax+"):raise AssertionError("write on import")
 return old(file,mode,*a,**k)
builtins.open=opening
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError("network"))
import composerlint.server
from composerlint import service
service.load_score=lambda *a,**k: (_ for _ in ()).throw(AssertionError("analysis on import"))
print("import safe")'''
    result=subprocess.run([sys.executable,'-B','-c',script],cwd=ROOT,capture_output=True,text=True,check=True)
    assert result.stdout=='import safe\n' and result.stderr==''


def test_T11_skill_contract():
    p=ROOT/'skills/composer-review/SKILL.md';text=p.read_text()
    assert text.startswith('---\n')
    front=dict(line.split(': ',1) for line in text.split('---',2)[1].strip().splitlines())
    assert front['name']=='composer-review' and 'MusicXML' in front['description']
    assert all(name in text for name in NAMES)
    assert 'Repair is unavailable in V0.1' in text and 'Never edit the score' in text
    assert 'same source hash and effective options' in text
    assert 'confirm or change' in text and 'Mozart-style means supported Classical conventions' in text
    assert 'insufficient_evidence' in text and 'potentially intentional' in text

@pytest.mark.parametrize('fixture,part,index',[
    ('multi_part.musicxml','p0002',1),('inspection_tie_stop.musicxml',None,1),
    ('empty.musicxml',None,1),('piano_multivoice.musicxml',None,2)])
def test_T04_measure_reference_point_tie_semantics(fixture,part,index):
    path=F/fixture;r=service.analyze_score(path)
    result,error=dispatch('inspect_measure',{'path':str(path),'measure_index':index,'part_id':part})
    assert not error and result==service.inspect_measure(r,index,part)

@pytest.mark.parametrize('name',NAMES)
def test_T03_valid_existing_options_and_exact_error_reason(name):
    options={'target_style':'classical','key_override':{'tonic':'B-','mode':'minor'},'ranges':{'p0001':{'minimum':-10,'maximum':200}},'midi':{'quantize_post':False,'quarter_length_divisors':[3,4]}}
    result,error=dispatch(name,args(name,F/'basic.mid',options=options))
    assert not error and result==direct(name,F/'basic.mid',options)
    # Domain explanations remain the established service explanations.
    bad='/missing/composerlint.musicxml'
    with pytest.raises(ComposerLintError) as exc:direct(name,bad)
    result,error=dispatch(name,args(name,bad))
    assert error and (result.error.code,result.error.reason)==(exc.value.code,exc.value.reason)

@pytest.mark.parametrize('name',NAMES)
def test_T03_unknown_and_missing_top_fields(name):
    for field in ['path',*(['measure_index'] if name=='inspect_measure' else [])]:
        data=args(name);del data[field]
        result,error=dispatch(name,data);assert error and result.error.code=='invalid_options'
    for field in ['report','context','profile_id','command']:
        result,error=dispatch(name,args(name,**{field:'unexpected'}));assert error and result.error.code=='invalid_options'


def test_T06_independent_source_facts_and_full_tie():
    p=F/'m3/fifths_positive.musicxml';r=service.analyze_score(p);v=service.check_voice_leading(r)
    d=next(d for d in v.diagnostics if d.category=='theory_issue')
    assert d.rule_id=='parallel_fifths'
    ev=d.evidence[0];assert ev.voice_ids==['p0001:v0001','p0002:v0001']
    assert ev.before.event_ids==['p0001:v0001:e000001','p0002:v0001:e000001']
    assert ev.after.event_ids==['p0001:v0001:e000002','p0002:v0001:e000002']
    assert ev.chromatic_deltas==[2,2]
    tie=service.check_voice_leading(F/'m3/range_chord_tie.musicxml',{'ranges':{'p0001':{'minimum':60,'maximum':100}}})
    below=next(d for d in tie.diagnostics if d.category=='theory_issue')
    assert below.rule_id=='range_issue' and below.evidence[0].pitch.chromatic_number==59
    event=next(e for p in tie.parts for e in p.events if e.id==below.evidence[0].event_id)
    assert len(event.source_segment_ids)==2
    assert event.id=='p0001:v0001:e000001'


def test_T06_voice_assumptions_have_no_orphan_key_context():
    r=service.analyze_score(F/'m3/fifths_positive.musicxml')
    v=service.check_voice_leading(r)
    referenced={i for d in v.diagnostics for i in d.assumption_ids}
    assert referenced<={a.id for a in v.assumptions}
    assert all(e.kind not in ('key_reference','source_key','estimation') for a in v.assumptions for e in a.evidence)
    # No key prerequisite exists even for checked negatives without located findings.
    negative=service.check_voice_leading(F/'m3/fifths_ties.musicxml')
    assert all(a.reason_code=='m3_observed_pitch_relation' for a in negative.assumptions)


def test_focused_harmony_retains_interleaved_source_event_order(tmp_path):
    # Two authored bars: voice 2 holds E4 while voice 1 changes C4 to G4.
    # The source event order is v1:e1, v2:e1, v1:e2, not lexical ID order.
    path = tmp_path / 'interleaved.musicxml'
    path.write_text('<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Authored ordering regression</part-name></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>1</divisions><key><fifths>-2</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>whole</type></note><backup><duration>4</duration></backup><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><tie type="start"/><voice>2</voice><type>whole</type><notations><tied type="start"/></notations></note></measure><measure number="2"><note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>whole</type></note><backup><duration>4</duration></backup><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><tie type="stop"/><voice>2</voice><type>whole</type><notations><tied type="stop"/></notations></note></measure></part></score-partwise>')
    report = service.analyze_score(path)
    before = report.canonical_json()
    view = service.check_harmony(report)
    assert CheckHarmonyView.model_validate_json(view.canonical_json()) == view
    assert view.harmony == report.harmony
    assert view.cadences == report.cadences
    assert view.key_analysis == report.key_analysis
    assert view.coverage == [c for c in report.coverage if c.rule_id in HARMONY_RULES]
    assert next(c for c in view.coverage if c.rule_id == 'cadence_detection').reason_codes == ['missing_key']
    assert next(c for c in view.coverage if c.rule_id == 'unresolved_seventh').reason_codes == ['no_candidate_units']
    assert [e.id for e in view.parts[0].events] == [e.id for e in report.score.parts[0].events]
    held = next(e for e in view.parts[0].events if e.voice_id == 'p0001:v0002')
    assert len(held.source_segment_ids) == 2
    assert report.canonical_json() == before
    # The corrected order check must still reject corrupted source references.
    for fault in ('order', 'unknown'):
        data = view.model_dump(mode='json')
        refs = next(h['scope']['references'] for h in data['harmony'] if len(h['scope']['references']['event_ids']) > 1)
        if fault == 'order':
            refs['event_ids'].reverse()
        else:
            refs['event_ids'].append('p0001:v0001:e999999')
        with pytest.raises(ValidationError, match='unresolved or unordered event_ids'):
            CheckHarmonyView.model_validate(data)
