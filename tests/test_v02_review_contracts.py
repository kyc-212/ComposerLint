"""Strict additive MCP boundary, legacy compatibility and domain/error separation."""
from pathlib import Path
import hashlib,json
import pytest,jsonschema
from composerlint.tool_contracts import TOOLS,LEGACY_TOOLS,dispatch,input_schema,output_schema
from composerlint.review_models import ReviewErrorResult
F=Path(__file__).parent/'fixtures';TOOL=next(t for t in TOOLS if t.name=='get_review_page')


def test_legacy_schemas_are_byte_equivalent():
    old=json.loads((F.parents[1]/'docs/V0.2-M1-evidence/legacy-schemas.json').read_text())
    assert [t.name for t in LEGACY_TOOLS]==['analyze_score','detect_style','inspect_measure','check_harmony','check_voice_leading','get_style_evidence']
    assert [t.name for t in TOOLS[:7]]==[*old,'get_review_page']
    assert {t.name:{'input':input_schema(t),'output':output_schema(t)} for t in LEGACY_TOOLS}==old


@pytest.mark.parametrize('path',['m3/fifths_positive.musicxml','ugly_ambiguous.mid'])
def test_new_schema_parity_hash_and_error_recovery(path):
    source=F/path;before=source.read_bytes();args={'path':str(source),'page_size':50}
    schema=input_schema(TOOL);jsonschema.Draft202012Validator.check_schema(schema);jsonschema.Draft202012Validator.check_schema(output_schema(TOOL))
    jsonschema.validate(args,schema)
    page,error=dispatch('get_review_page',args);assert not error;jsonschema.validate(page.model_dump(mode='json'),output_schema(TOOL))
    assert page.source_sha256==hashlib.sha256(before).hexdigest()
    assert dispatch('get_review_page',args)[0].canonical_json()==page.canonical_json()
    fields={'path':str(source),'section':'findings','expected_snapshot':page.snapshot_id}
    rows,error=dispatch('get_review_page',fields);assert not error
    bad,error=dispatch('get_review_page',dict(fields,expected_snapshot='0'*64));assert error and bad.error.code=='stale_snapshot'
    assert not hasattr(bad,'engine_completed');jsonschema.validate(bad.model_dump(mode='json'),output_schema(TOOL))
    assert not dispatch('get_review_page',args)[1] and source.read_bytes()==before


@pytest.mark.parametrize('bad',[{},None,{'path':True},{'path':'x','extra':1},{'path':'x','page_size':True},{'path':'x','page_size':0},{'path':'x','max_bytes':32769},{'path':'x','options':{'extra':1}},{'path':'x','section':'findings'},{'path':'x','record_ref':'diagnostic:x'},{'path':'x','cursor':'abc'},{'path':'x','section':'measures','expected_snapshot':'0'*64,'measure_index':'1'}])
def test_invalid_requests_have_no_success_flags(bad):
    out,error=dispatch('get_review_page',bad);assert error and out.error.code=='invalid_options'
    assert isinstance(out,ReviewErrorResult) and 'engine_completed' not in out.model_dump()
    jsonschema.validate(out.model_dump(mode='json'),output_schema(TOOL))


def test_domain_priority_and_unknown_record():
    out,error=dispatch('get_review_page',{'path':'missing','options':{'target_style':'romantic','midi':{'quantize_post':3}}});assert error and out.error.code=='unsupported_style'
    path=str(F/'m3/fifths_positive.musicxml');a,error=dispatch('get_review_page',{'path':path})
    out,error=dispatch('get_review_page',{'path':path,'section':'evidence','expected_snapshot':a.snapshot_id,'record_ref':'diagnostic:absent'});assert error and out.error.code=='unknown_record'
    out,error=dispatch('get_review_page',{'path':'/missing.musicxml'});assert error and out.error.code=='file_not_found'


def test_implementation_fault_not_relabelled_as_bad_input(monkeypatch):
    from composerlint import service
    from pydantic import ValidationError
    class Bad:
        def model_dump(self,**kw):return {}
    monkeypatch.setattr(service,'get_review_page',lambda *a,**k:Bad())
    with pytest.raises(ValidationError):dispatch('get_review_page',{'path':'x'})


def test_error_messages_are_bounded_even_for_unicode_input():
    out,error=dispatch('get_review_page',{'path':str(F/'basic.musicxml'),'options':{'ranges':{'\U0001f3b5'*4000:{'minimum':0,'maximum':100}}}})
    assert error and out.error.code=='unknown_voice'
    assert len(out.canonical_json().encode())<=8192
    # The legacy engine already uses a short generic unknown-ID reason. Exercise a genuinely large validator error too.
    bad={'path':str(F/'basic.musicxml'),**{f'unknown_{i}':'\U0001f3b5'*20 for i in range(200)}}
    out,error=dispatch('get_review_page',bad)
    assert error and out.error.code=='invalid_options'
    assert len(out.canonical_json().encode())<=8192
    assert 'shortened' in out.error.reason


@pytest.mark.parametrize('operation',['inspection','bounded_review'])
def test_completed_report_projection_validates_once_and_rejects_tampering(monkeypatch,operation):
    from composerlint import service
    from composerlint.analysis_models import AnalysisReport
    from composerlint.review import project_review,digest
    from composerlint.review_models import ReviewArguments
    from pydantic import ValidationError
    report=service.analyze_score(F/'basic.musicxml');before=report.canonical_json()
    if operation=='inspection':
        expected=service.inspect_measure(report,1)
        project=lambda source:service.inspect_measure(source,1)
    else:
        ref='segment:'+report.score.parts[0].source_segments[0].id
        selection={'section':'evidence','record_ref':ref,'expected_snapshot':digest(report.model_dump(mode='json')),'page_size':50,'max_bytes':32768}
        expected=project_review(report,ReviewArguments(path='<completed-report>',**selection))
        project=lambda source:service.get_review_page(source,**selection)
    original=AnalysisReport.model_validate;calls=[]
    def checked(cls,*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(AnalysisReport,'model_validate',classmethod(checked))
    def forbidden(*a,**kw):raise AssertionError('completed report unexpectedly reread or recomputed source')
    for name in ('load_score','read_source_context','analyze_normalized'):monkeypatch.setattr(service,name,forbidden)
    assert project(report)==expected and len(calls)==1
    assert report.canonical_json()==before
    forged=report.model_copy(deep=True);forged.score.source_sha256='0'*64
    with pytest.raises(ValidationError):project(forged)


def test_fresh_inspection_dispatch_reuses_owned_report_and_validates_output(monkeypatch):
    from composerlint import service
    from pydantic import ValidationError
    path=F/'basic.musicxml';report=service.analyze_score(path);before=report.canonical_json()
    expected=service.inspect_measure(report,1);calls=[]
    def analyzed(*a,**kw):calls.append(1);return report
    def forbidden(*a,**kw):raise AssertionError('fresh owned report validated twice')
    monkeypatch.setattr(service,'analyze_score',analyzed)
    monkeypatch.setattr(service,'_owned_report',forbidden)
    result,error=dispatch('inspect_measure',{'path':str(path),'measure_index':1})
    assert not error and result==expected and len(calls)==1
    assert report.canonical_json()==before
    forged=expected.model_copy(deep=True);forged.parts[0].primary_event_ids.clear()
    monkeypatch.setattr(service,'_inspect_validated_measure',lambda *a,**kw:forged)
    with pytest.raises(ValidationError):dispatch('inspect_measure',{'path':str(path),'measure_index':1})


@pytest.mark.parametrize('compressed',[False,True])
def test_owned_report_reuse_is_bounded_immutable_and_fresh(tmp_path,monkeypatch,compressed):
    from composerlint import service,report_reuse as reuse
    monkeypatch.setattr(reuse,'_entry',None)
    path=tmp_path/'one.musicxml';path.write_bytes((F/'basic.musicxml').read_bytes())
    report=service.analyze_score(path)
    if compressed:
        from composerlint.review import canonical
        monkeypatch.setattr(reuse,'MAX_BYTES',len(canonical(report.model_dump(mode='json')).encode())-1)
    service.get_review_page(path)
    assert reuse._entry.encoding==('zlib' if compressed else 'json')
    payload=reuse.take(path)
    assert payload and payload['report']==report.model_dump(mode='json')
    payload['report']['score']['title']='caller mutation'
    assert reuse.take(path)['report']==report.model_dump(mode='json')
    assert reuse.take(path,{'key_override':{'tonic':'C','mode':'major'}}) is None
    other=tmp_path/'two.musicxml';other.write_bytes(path.read_bytes())
    assert reuse.take(other) is None
    reuse.publish(other,service.analyze_score(other))
    assert reuse.take(path) is None and reuse.take(other)
    with monkeypatch.context() as scoped:
        scoped.setattr(reuse.time,'monotonic',lambda:reuse._entry.expires+1)
        assert reuse.take(other) is None
    reuse.publish(path,report)
    with monkeypatch.context() as scoped:
        scoped.setattr(reuse,'code_identity',lambda:'0'*64)
        assert reuse.take(path) is None
    reuse.publish(path,report);path.write_bytes(path.read_bytes()+b'\n')
    assert reuse.take(path) is None
    path.write_bytes((F/'basic.musicxml').read_bytes())
    monkeypatch.setattr(reuse,'MAX_BYTES',1);reuse.publish(path,report)
    assert reuse.take(path) is None


@pytest.mark.parametrize('kind',['advanced','evaluation'])
@pytest.mark.parametrize('compressed',[False,True])
def test_isolated_worker_reuse_preserves_results_and_rejects_forged_base(tmp_path,monkeypatch,kind,compressed):
    from composerlint import service,report_reuse as reuse
    from composerlint.review import digest
    from composerlint.advanced_models import AdvancedArguments,AdvancedFailure
    from composerlint.evaluation_models import EvaluationArguments,EvaluationFailure
    from composerlint import advanced_runtime,evaluation_runtime
    monkeypatch.setattr(reuse,'_entry',None)
    path=tmp_path/'source.musicxml';path.write_bytes((F/'basic.musicxml').read_bytes())
    report=service.analyze_score(path)
    if compressed:
        from composerlint.review import canonical
        monkeypatch.setattr(reuse,'MAX_BYTES',len(canonical(report.model_dump(mode='json')).encode())-1)
    model,runtime=(AdvancedArguments,advanced_runtime) if kind=='advanced' else (EvaluationArguments,evaluation_runtime)
    args=model(path=str(path),expected_base_snapshot=digest(report.model_dump(mode='json')))
    request={'operation':'page','arguments':args.model_dump(mode='json')}
    cold=runtime.request_worker(request)
    reuse.publish(path,report);warm=runtime.request_worker(request)
    assert cold==warm
    assert warm['source_sha256']==report.score.source_sha256
    forged=reuse.take(path);forged['report']['score']['source_sha256']='0'*64
    with pytest.raises((AdvancedFailure,EvaluationFailure)) as exc:runtime.request_worker({**request,'base_report':forged})
    assert exc.value.code=='unavailable_evidence'
    stale=reuse.take(path);path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises((AdvancedFailure,EvaluationFailure)) as exc:runtime.request_worker({**request,'base_report':stale})
    assert exc.value.code=='unavailable_evidence'


@pytest.mark.parametrize('kind',['advanced','evaluation'])
def test_report_lookup_is_inside_the_unchanged_deadline(monkeypatch,kind):
    from composerlint import report_reuse,advanced_runtime,evaluation_runtime
    from composerlint.advanced_models import AdvancedFailure
    from composerlint.evaluation_models import EvaluationFailure
    runtime=advanced_runtime if kind=='advanced' else evaluation_runtime
    clock=[0.0]
    def delayed(*a,**kw):clock[0]=26.0;return None
    def forbidden(*a,**kw):raise AssertionError('deadline-expired request launched a worker')
    monkeypatch.setattr(runtime.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(report_reuse,'take',delayed)
    monkeypatch.setattr(runtime.subprocess,'run',forbidden)
    with pytest.raises((AdvancedFailure,EvaluationFailure)) as exc:
        runtime.request_worker({'operation':'page','arguments':{'path':'not-read.musicxml'}})
    assert exc.value.code=='analysis_timeout' and runtime.LIMITS['deadline_seconds']==25


@pytest.fixture
def compressed_report(tmp_path,monkeypatch):
    from composerlint import service,report_reuse as reuse
    from composerlint.review import canonical
    monkeypatch.setattr(reuse,'_entry',None)
    path=tmp_path/'compressed.musicxml';path.write_bytes((F/'basic.musicxml').read_bytes())
    report=service.analyze_score(path);raw=canonical(report.model_dump(mode='json')).encode()
    monkeypatch.setattr(reuse,'MAX_BYTES',len(raw)-1)
    reuse.publish(path,report)
    assert reuse._entry.encoding=='zlib'
    return reuse,path,report,raw


def test_compressed_report_exact_canonical_roundtrip_and_limits(compressed_report,monkeypatch):
    import zlib
    from composerlint.review import canonical
    reuse,path,report,raw=compressed_report;entry=reuse._entry
    assert zlib.decompress(entry.payload)==raw
    assert entry.snapshot==hashlib.sha256(raw).hexdigest()
    assert entry.decoded_bytes==len(raw) and len(entry.payload)<=reuse.MAX_BYTES
    assert canonical(reuse.take(path)['report']).encode()==raw
    assert reuse._entry is entry  # Taking a report never renews its expiry.
    monkeypatch.setattr(reuse,'MAX_DECODED_BYTES',len(raw))
    reuse.publish(path,report);assert reuse.take(path)
    monkeypatch.setattr(reuse,'MAX_DECODED_BYTES',len(raw)-1)
    reuse.publish(path,report);assert reuse._entry is None
    monkeypatch.setattr(reuse,'MAX_DECODED_BYTES',len(raw))
    monkeypatch.setattr(reuse,'MAX_BYTES',len(entry.payload)-1)
    reuse.publish(path,report);assert reuse._entry is None
    assert reuse.TTL_SECONDS==300


@pytest.mark.parametrize('damage',[
    'corrupt','checksum','truncated','trailing','concatenated','oversized_stored','expands_past_length',
    'digest','wrong_length','invalid_size','unknown_encoding','noncanonical','invalid_json',
])
def test_compressed_report_rejects_invalid_representations(compressed_report,damage):
    from dataclasses import replace
    import zlib
    reuse,path,report,raw=compressed_report;entry=reuse._entry
    if damage=='corrupt':change={'payload':b'not a zlib stream'}
    elif damage=='checksum':change={'payload':entry.payload[:-1]+bytes([entry.payload[-1]^1])}
    elif damage=='truncated':change={'payload':entry.payload[:-1]}
    elif damage=='trailing':change={'payload':entry.payload+b'x'}
    elif damage=='concatenated':change={'payload':entry.payload+zlib.compress(b'{}')}
    elif damage=='oversized_stored':change={'payload':b'x'*(reuse.MAX_BYTES+1)}
    elif damage=='expands_past_length':change={'payload':zlib.compress(raw+b'x'*100)}
    elif damage=='digest':change={'snapshot':'0'*64}
    elif damage=='wrong_length':change={'decoded_bytes':len(raw)+1}
    elif damage=='invalid_size':change={'decoded_bytes':reuse.MAX_DECODED_BYTES+1}
    elif damage=='unknown_encoding':change={'encoding':'unknown'}
    else:
        altered=(b' '+raw) if damage=='noncanonical' else raw[:-1]
        change={'payload':zlib.compress(altered),'decoded_bytes':len(altered),
                'snapshot':hashlib.sha256(altered).hexdigest()}
    reuse._entry=replace(entry,**change)
    assert reuse.take(path) is None and reuse._entry is None


def test_decompression_uses_the_declared_bounded_output_limit(compressed_report,monkeypatch):
    reuse,path,report,raw=compressed_report
    original=reuse.zlib.decompressobj;limits=[]
    class BoundedDecoder:
        def __init__(self):self.inner=original()
        def decompress(self,data,max_length):
            limits.append(max_length);return self.inner.decompress(data,max_length)
        def __getattr__(self,name):return getattr(self.inner,name)
    monkeypatch.setattr(reuse.zlib,'decompressobj',BoundedDecoder)
    assert reuse.take(path) and limits==[len(raw)+1]


@pytest.mark.parametrize('name',['bach_positive','mozart_positive','beethoven_positive'])
@pytest.mark.parametrize('kind',['advanced','evaluation'])
def test_compressed_reuse_preserves_existing_composer_control(name,kind,monkeypatch):
    from composerlint import service,report_reuse as reuse,advanced_runtime,evaluation_runtime
    from composerlint.review import canonical,digest
    from composerlint.advanced_models import AdvancedArguments
    from composerlint.evaluation_models import EvaluationArguments
    monkeypatch.setattr(reuse,'_entry',None)
    path=F/'classical_intelligence'/(name+'.musicxml');before=path.read_bytes()
    report=service.analyze_score(path)
    model,runtime=(AdvancedArguments,advanced_runtime) if kind=='advanced' else (EvaluationArguments,evaluation_runtime)
    args=model(path=str(path),expected_base_snapshot=digest(report.model_dump(mode='json')))
    request={'operation':'page','arguments':args.model_dump(mode='json')}
    cold=runtime.request_worker(request)
    monkeypatch.setattr(reuse,'MAX_BYTES',len(canonical(report.model_dump(mode='json')).encode())-1)
    reuse.publish(path,report);assert reuse._entry.encoding=='zlib'
    assert runtime.request_worker(request)==cold
    assert path.read_bytes()==before
