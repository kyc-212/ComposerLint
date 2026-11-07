import hashlib
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest
from composerlint.service import analyze_score,analyze_normalized,analyze_request
from composerlint.engine.parser import load_score
from composerlint.engine.source_context import read_source_context,context_from_bytes
from composerlint.errors import ComposerLintError
F=Path(__file__).parent/'fixtures'/'m2'
M1=F.parent


@pytest.mark.parametrize('name',[p.name for p in sorted(F.glob('*')) if p.suffix in ('.musicxml','.mid')]+['piano_multivoice.musicxml','ugly_ambiguous.mid','unknown_pitch.musicxml','empty.musicxml','all_rest.musicxml'])
@pytest.mark.parametrize('quantize',[True,False])
def test_double_run_immutable_self_contained(name,quantize):
    p=F/name if (F/name).exists() else M1/name; before=hashlib.sha256(p.read_bytes()).hexdigest()
    a=analyze_score(p,{'midi':{'quantize_post':quantize}});b=analyze_score(p,{'midi':{'quantize_post':quantize}})
    assert a.canonical_json()==b.canonical_json() and before==hashlib.sha256(p.read_bytes()).hexdigest()==a.score.source_sha256
    assert str(p) not in a.canonical_json()
    assert a.score.canonical_json()==load_score(p,{'midi':{'quantize_post':quantize}}).canonical_json()


@pytest.mark.parametrize('name',['estimate_major.musicxml','ugly_ambiguous.mid'])
def test_fresh_process_determinism(name):
    p=F/name if (F/name).exists() else M1/name
    code='from composerlint.service import analyze_score; print(analyze_score('+repr(str(p))+').canonical_json())'
    a=subprocess.check_output([sys.executable,'-c',code]);b=subprocess.check_output([sys.executable,'-c',code])
    assert a==b


def test_normalizes_once_owned_inputs_and_consecutive_requests():
    import composerlint.service as service
    with patch.object(service,'load_score',wraps=load_score) as loader:
        r=analyze_score(F/'estimate_major.musicxml');assert loader.call_count==1
    score=load_score(F/'declared_major.musicxml');context=read_source_context(F/'declared_major.musicxml',score)
    before=score.canonical_json();context_before=context.model_dump_json()
    r=analyze_normalized(score,context)
    r.score.parts[0].events[0].source_segment_ids.append('mutation')
    r.key_analysis.source_context.key_observations.clear()
    assert score.canonical_json()==before and context.model_dump_json()==context_before
    assert analyze_normalized(score,context).score.canonical_json()==before


def test_mixed_hash_and_change_between_reads_fail(tmp_path,monkeypatch):
    score=load_score(F/'declared_major.musicxml')
    with pytest.raises(ComposerLintError) as exc:context_from_bytes(b'changed',score)
    assert (exc.value.code,exc.value.reason)==('parse_failed','source_changed_during_analysis')
    context=read_source_context(F/'declared_minor.musicxml',load_score(F/'declared_minor.musicxml'))
    with pytest.raises(ComposerLintError) as exc:analyze_normalized(score,context)
    assert exc.value.code=='parse_failed'
    import composerlint.service as service
    p=tmp_path/'score.musicxml';p.write_bytes((F/'declared_major.musicxml').read_bytes())
    def changed(path,options):
        s=load_score(path,options);p.write_bytes((F/'declared_minor.musicxml').read_bytes());return s
    monkeypatch.setattr(service,'load_score',changed)
    with pytest.raises(ComposerLintError) as exc:analyze_score(p)
    assert exc.value.reason=='source_changed_during_analysis'


def test_same_path_changed_bytes_no_cache(tmp_path):
    p=tmp_path/'score.musicxml';p.write_bytes((F/'declared_major.musicxml').read_bytes());a=analyze_score(p)
    p.write_bytes((F/'declared_minor.musicxml').read_bytes());b=analyze_score(p)
    assert a.score.source_sha256!=b.score.source_sha256 and a.key_analysis.selected_key!=b.key_analysis.selected_key


@pytest.mark.parametrize('options,code',[
    ({'key_override':{'tonic':'H','mode':'major'}},'invalid_options'),
    ({'target_style':'romantic'},'unsupported_style'),({'voice_order':['absent']},'unknown_voice'),
    ({'ranges':{'absent':{'minimum':0,'maximum':1}}},'unknown_voice'),
    ({'midi':{'quarter_length_divisors':[True]}},'invalid_options'),
    ({'ranges':{'p0001':{'minimum':10,'maximum':2}}},'invalid_options')])
def test_request_loader_option_error_parity(options,code):
    for call in (lambda:analyze_score(F/'declared_major.musicxml',options),lambda:analyze_request({'path':str(F/'declared_major.musicxml'),'options':options})):
        with pytest.raises(ComposerLintError) as exc:call()
        assert exc.value.code==code


@pytest.mark.parametrize('path,code',[('/no/such/score.mid','file_not_found'),('https://example.com/a.mid','unsupported_format'),(str(F),'parse_failed')])
def test_path_error_parity(path,code):
    with pytest.raises(ComposerLintError) as exc:analyze_score(path)
    assert exc.value.code==code


def test_request_strict_extra_and_retained_options():
    with pytest.raises(ComposerLintError) as exc:analyze_request({'path':1})
    assert exc.value.code=='invalid_options'
    with pytest.raises(ComposerLintError):analyze_request({'path':str(F/'declared_major.musicxml'),'extra':1})
    opts={'key_override':{'tonic':'B-','mode':'minor'},'target_style':'classical','voice_order':['p0001:v0001'],'ranges':{'p0001':{'minimum':0,'maximum':127}},'midi':{'quantize_post':False,'quarter_length_divisors':[3,4]}}
    assert analyze_request({'path':str(F/'declared_major.musicxml'),'options':opts}).options.model_dump()==opts


def test_upstream_exceptions_and_context_defects_preserved(monkeypatch):
    import composerlint.service as service
    original=ComposerLintError('parse_failed','known')
    def fail(*a):raise original
    monkeypatch.setattr(service,'load_score',fail)
    with pytest.raises(ComposerLintError) as exc:analyze_score('unused')
    assert exc.value is original
    monkeypatch.setattr(service,'load_score',load_score)
    def defect(*a):raise RuntimeError('context defect')
    monkeypatch.setattr(service,'read_source_context',defect)
    with pytest.raises(RuntimeError,match='context defect'):analyze_score(F/'declared_major.musicxml')


def test_namespaced_source_context(tmp_path):
    p=tmp_path/'namespace.musicxml';p.write_text((F/'declared_major.musicxml').read_text().replace('<score-partwise version="4.0">','<score-partwise xmlns="http://www.musicxml.org/ns/musicxml" version="4.0">'))
    assert analyze_score(p).key_analysis.selected_key.tonic=='C'

from composerlint.service import inspect_measure
from composerlint.analysis_models import (MeasureInspection,AnalysisReport,AnalysisSummary,record_sort)
from composerlint.models import rt
from pydantic import ValidationError


def test_inspection_pickup_labels_ties_support_and_boundaries():
    r=analyze_score(F/'inspection_multivoice.musicxml');before=r.canonical_json()
    first=inspect_measure(r,1);second=inspect_measure(r,2);zero=inspect_measure(r,3)
    assert (first.window.start,first.window.end)==(rt(0),rt(1))
    assert first.reference_measure.original_number=='0'
    assert second.reference_measure.original_number==zero.reference_measure.original_number=='1'
    assert (second.window.start,second.window.end)==(rt(1),rt(5))
    assert zero.window.start==zero.window.end==rt(5)
    a='p0001:v0001:';b='p0002:v0001:'
    assert first.parts[0].primary_event_ids==[a+'e000001',a+'e000002']
    assert first.parts[0].support_event_ids==[a+'e000004']  # Chord partner owned in measure 2.
    assert second.parts[0].primary_event_ids==[a+'e000001',a+'e000003',a+'e000004']
    tie=second.parts[0].events[0]
    assert (tie.onset,tie.end,tie.duration)==(rt(0),rt(5),rt(5))
    assert tie.source_segment_ids==[a+'s000001',a+'s000004']
    assert a+'s000001' not in second.parts[0].included_source_segment_ids
    assert a+'s000004' in second.parts[0].included_source_segment_ids
    assert first.parts[1].primary_event_ids==[b+'e000001',b+'e000002']
    assert second.parts[1].primary_event_ids==[b+'e000003',b+'e000004',b+'e000005']
    assert zero.parts[0].primary_event_ids==[a+'e000005'] and zero.parts[1].primary_event_ids==[b+'e000005']
    assert all(not p.window_intersections for p in zero.parts)
    for v in (first,second,zero):
        assert v.key_analysis==r.key_analysis and v.coverage==r.coverage and v.assumptions==r.assumptions
        assert v.model_dump(exclude_defaults=True)['schema_version']=='0.1'
        assert MeasureInspection.model_validate_json(v.canonical_json()).canonical_json()==v.canonical_json()
        assert not hasattr(v,'summary')
    assert r.canonical_json()==before
    assert inspect_measure(r,2,'p0002').reference_part_id=='p0002'


@pytest.mark.parametrize('value',[True,False,0,-1,1.0,'1',None])
def test_inspection_bad_index(value):
    with pytest.raises(ComposerLintError) as exc:inspect_measure(analyze_score(F/'declared_major.musicxml'),value)
    assert exc.value.code=='invalid_options'


def test_inspection_unknown_part_missing_measure_and_empty_part(tmp_path):
    r=analyze_score(F/'declared_major.musicxml')
    with pytest.raises(ComposerLintError) as exc:inspect_measure(r,1,'absent')
    assert exc.value.code=='unknown_voice'
    with pytest.raises(ComposerLintError) as exc:inspect_measure(r,2)
    assert exc.value.code=='measure_not_found'
    empty=inspect_measure(analyze_score(M1/'empty.musicxml'),1)
    assert empty.window.start==empty.window.end==rt(0) and not empty.parts[0].events
    data=(F/'conflicting_parts.musicxml').read_text()
    import re
    data=re.sub(r'(<part id="P1">).*?(</part>)',r'\1\2',data)
    p=tmp_path/'empty_first.musicxml';p.write_text(data)
    r=analyze_score(p)
    with pytest.raises(ComposerLintError) as exc:inspect_measure(r,1)
    assert exc.value.code=='measure_not_found'
    assert inspect_measure(r,1,'p0002').reference_part_id=='p0002'


def test_inspection_reads_nothing_after_source_unavailable(tmp_path,monkeypatch):
    p=tmp_path/'temporary.musicxml';p.write_bytes((F/'estimate_major.musicxml').read_bytes());r=analyze_score(p);p.unlink()
    def fail(*args,**kwargs):raise AssertionError('unexpected source/estimator call')
    monkeypatch.setattr(Path,'read_bytes',fail)
    import composerlint.service as service
    monkeypatch.setattr(service,'load_score',fail);monkeypatch.setattr(service,'read_source_context',fail)
    from music21.analysis.discrete import KrumhanslSchmuckler
    monkeypatch.setattr(KrumhanslSchmuckler,'getSolution',fail)
    view=inspect_measure(r,1)
    assert view.key_analysis.selected_key.tonic=='C'
    before=r.canonical_json();view.parts[0].events[0].source_segment_ids.clear()
    assert r.canonical_json()==before


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(schema_version=1),lambda d:d.update(extra=True),
    lambda d:d.update(source_sha256='0'*64),
    lambda d:d['window']['start'].update(numerator=2),
    lambda d:d['parts'][0]['primary_event_ids'].append('p0001:v0001:e999999'),
    lambda d:d['parts'][0]['support_event_ids'].append(d['parts'][0]['primary_event_ids'][0]),
    lambda d:d['parts'][0]['included_source_segment_ids'].clear(),
    lambda d:d['parts'][0]['source_segments'].pop(),
    lambda d:d['parts'][0]['window_intersections'][0]['end'].update(numerator=10),
    lambda d:d['context_reference_ids']['event_ids'].append('absent'),
    lambda d:d['coverage'][3].update(status='complete'),
])
def test_inspection_schema_rejects_inconsistent_views(mutation):
    d=inspect_measure(analyze_score(F/'inspection_multivoice.musicxml'),1).model_dump(mode='json');mutation(d)
    with pytest.raises(ValidationError):MeasureInspection.model_validate(d)


def with_timed_diagnostics(report):
    from composerlint.analysis_models import Diagnostic,Confidence,AnalysisScope,ReferenceSet,EventEvidence
    a='p0001:v0001:'
    specs=[(0,1,a+'s000001'),(1,5,a+'s000004')]
    records=report.diagnostics.copy()
    for start,end,sid in specs:
        seg=next(s for s in report.score.parts[0].source_segments if s.id==sid)
        refs=ReferenceSet(part_ids=['p0001'],voice_ids=[a[:-1]],measure_ids=[seg.location.measure_id],event_ids=[a+'e000001'],source_segment_ids=[sid])
        records.append(Diagnostic(id='pending',rule_id='key_selection',category='analysis_limitation',severity='info',confidence=Confidence(level='high',reason_code='global_key_only',explanation='Test-only located analysis record.'),scope=AnalysisScope(kind='span',start=rt(start),end=rt(end),references=refs),evidence=[EventEvidence(kind='events',references=refs)],explanation='Same prose at different locations.',assumption_ids=[]))
    records.sort(key=record_sort)
    records=[r.model_copy(update={'id':f'd{i:06d}'}) for i,r in enumerate(records,1)]
    data=report.model_dump();data['diagnostics']=records;data['summary']=AnalysisSummary.derive(records,report.coverage)
    return AnalysisReport.model_validate(data)


def test_located_cross_bar_diagnostics_dedup_and_filter():
    r=with_timed_diagnostics(analyze_score(F/'inspection_multivoice.musicxml'))
    timed=[d for d in r.diagnostics if d.scope.kind=='span']
    assert len(timed)==2 and timed[0].id!=timed[1].id
    assert [d.scope.references.source_segment_ids for d in inspect_measure(r,2).diagnostics if d.scope.kind=='span']==[['p0001:v0001:s000004']]
    assert [d.scope.references.source_segment_ids for d in inspect_measure(r,1).diagnostics if d.scope.kind=='span']==[['p0001:v0001:s000001']]
    d=r.model_dump(mode='json');target=next(d for d in d['diagnostics'] if d['scope']['kind']=='span' and d['scope']['start']['numerator']==1)
    target['scope']['references']['source_segment_ids']=['p0001:v0001:s000001']
    # Segment 1 ends at 1; cannot locate a positive diagnostic [1,5).
    data=r.model_dump(mode='json');data['diagnostics']=[target if x['id']==target['id'] else x for x in data['diagnostics']]
    with pytest.raises(ValidationError):AnalysisReport.model_validate(data)


def test_zero_view_other_part_owned_zero_and_sustained_exclusion(tmp_path):
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('fixture_author',F/'generate.py');author=module_from_spec(spec);spec.loader.exec_module(author)
    p1=author.measure(author.note(duration=4))+author.measure(author.note('B',0,extra='<grace/>'),label='2',setup=False)
    p2=author.measure(author.note('G',8)+author.note('A',0,extra='<grace/>'))
    p=tmp_path/'zero.musicxml';p.write_text(author.xml([p1,p2]))
    v=inspect_measure(analyze_score(p),2)
    assert v.window.start==v.window.end==rt(4)
    assert not v.parts[1].primary_event_ids  # Sustained G crosses an empty half-open interval.
    p2=author.measure(author.note('G',4))+author.measure(author.note('A',0,extra='<grace/>'),label='2',setup=False)
    p.write_text(author.xml([p1,p2]));v=inspect_measure(analyze_score(p),2)
    assert v.parts[1].primary_event_ids==['p0002:v0001:e000002']


@pytest.mark.parametrize('name,quantize',[('piano_multivoice.musicxml',True),('ugly_ambiguous.mid',True),('ugly_ambiguous.mid',False)])
def test_m1_edge_views_preserve_source_and_global_coverage(name,quantize):
    p=M1/name;before=hashlib.sha256(p.read_bytes()).hexdigest();r=analyze_score(p,{'midi':{'quantize_post':quantize}})
    for part in r.score.parts:
        for m in part.measures:
            v=inspect_measure(r,m.ordinal,part.id)
            assert v.coverage==r.coverage and v.key_analysis==r.key_analysis
            assert MeasureInspection.model_validate_json(v.canonical_json()).source_sha256==before
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_home_relative_path_matches_loader(tmp_path,monkeypatch):
    fixture=tmp_path/'ComposerLint/tests/fixtures/m2/declared_major.musicxml'
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes((F/'declared_major.musicxml').read_bytes())
    monkeypatch.setenv('HOME',str(tmp_path))
    assert analyze_score('~/ComposerLint/tests/fixtures/m2/declared_major.musicxml').key_analysis.selected_key.tonic=='C'


def test_no_source_mutations_on_supported_failures(tmp_path):
    p=tmp_path/'bad.musicxml';p.write_bytes(b'<score-partwise/>');before=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ComposerLintError) as exc:analyze_score(p)
    assert exc.value.code=='parse_failed' and hashlib.sha256(p.read_bytes()).hexdigest()==before
    p.write_bytes((F/'declared_major.musicxml').read_bytes());before=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ComposerLintError):analyze_score(p,{'voice_order':['absent']})
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_import_has_no_filesystem_network_side_effects():
    code="""
import socket, ssl, urllib.request
from pathlib import Path
import builtins
original=builtins.open
# Imports read modules via Python's loader. Reject application-level reads/writes/network.
def blocked(*a,**k): raise AssertionError('import side effect')
Path.read_bytes=blocked
Path.write_bytes=blocked
Path.write_text=blocked
socket.create_connection=blocked
socket.socket.connect=blocked
import composerlint.service
import composerlint.repair.interfaces
assert not hasattr(composerlint.service,'repair_score')
"""
    subprocess.check_call([sys.executable,'-c',code])


def test_source_local_point_diagnostics_follow_measure_membership():
    from composerlint.analysis_models import Diagnostic,AnalysisScope,ReferenceSet,EventEvidence,Confidence
    r=analyze_score(F/'inspection_multivoice.musicxml');points=[]
    for eid,sid,mid in [('p0001:v0001:e000002','p0001:v0001:s000002','p0001:m0001'),('p0001:v0001:e000003','p0001:v0001:s000003','p0001:m0002')]:
        refs=ReferenceSet(part_ids=['p0001'],voice_ids=['p0001:v0001'],measure_ids=[mid],event_ids=[eid],source_segment_ids=[sid])
        points.append(Diagnostic(id='pending',rule_id='key_selection',category='analysis_limitation',severity='info',confidence=Confidence(level='high',reason_code='inspection_only',explanation='Test-only point.'),scope=AnalysisScope(kind='point',start=rt(1),end=rt(1),references=refs),evidence=[EventEvidence(kind='events',references=refs)],explanation='Two different source-owned points.',assumption_ids=[]))
    records=sorted([*r.diagnostics,*points],key=record_sort);records=[x.model_copy(update={'id':f'd{i:06d}'}) for i,x in enumerate(records,1)]
    data=r.model_dump();data.update(diagnostics=records,summary=AnalysisSummary.derive(records,r.coverage));r=AnalysisReport.model_validate(data)
    assert [d.scope.references.event_ids for d in inspect_measure(r,1).diagnostics if d.scope.kind=='point']==[['p0001:v0001:e000002']]
    assert [d.scope.references.event_ids for d in inspect_measure(r,2).diagnostics if d.scope.kind=='point']==[['p0001:v0001:e000003']]


@pytest.mark.parametrize('defect',['primary_mislabeled','timed_diagnostic_outside','wrong_segment_beat','incomplete_segment_chain'])
def test_inspection_audit_semantic_rejections(defect):
    r=with_timed_diagnostics(analyze_score(F/'inspection_multivoice.musicxml'))
    data=inspect_measure(r,1).model_dump(mode='json')
    if defect=='primary_mislabeled':
        eid=data['parts'][0]['primary_event_ids'].pop();data['parts'][0]['support_event_ids'].insert(0,eid)
    if defect=='timed_diagnostic_outside':
        d=next(d for d in data['diagnostics'] if d['scope']['kind']=='span');d['scope']['start']['numerator']=4;d['scope']['end']['numerator']=5
    if defect=='wrong_segment_beat':data['parts'][0]['source_segments'][0]['location']['beat']['numerator']=999
    if defect=='incomplete_segment_chain':data['parts'][0]['source_segments'][2]['normalized_onset']['numerator']=2;data['parts'][0]['source_segments'][2]['location']['offset']['numerator']=1
    with pytest.raises(ValidationError):MeasureInspection.model_validate(data)
