"""Independent musical identities, negative boundaries and live local transport."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import pytest
from pydantic import ValidationError
from composerlint.service import analyze_score, get_review_page
from composerlint.review import digest
from composerlint.viewer import run, encode_selection, decode_selection
from composerlint.viewer_models import ViewArguments, ResolveArguments
from composerlint.viewer_source import source_index, aligned_range, verify_svg, ViewerFailure
from composerlint.renderers.verovio import render
from composerlint.viewer_runtime import runtime, Runtime
from composerlint.tool_contracts import dispatch, TOOLS, input_schema, output_schema

F=Path(__file__).parent/'fixtures'
CASES=['duet','grandstaff','voices','pickup','ties','duplicate','transpose','rests','repeat','inherited','chord']


def args(name='duet', **kw):
    p=F/'v02_m2'/f'{name}.musicxml'
    return ViewArguments(path=str(p),expected_snapshot=get_review_page(p).snapshot_id,
        **(dict(reference_part_id='p0001',start_measure_index=1,end_measure_index=4,presentation='text')|kw))


@pytest.mark.parametrize('name',CASES)
def test_actual_renderer_complete_source_mapping_and_written_pitch(name):
    p=F/'v02_m2'/f'{name}.musicxml';before=p.read_bytes();report=analyze_score(p)
    root,ms,ns=source_index(before,report.score.model_dump(mode='json'))
    xml,ms,ns=aligned_range(root,ms,ns,[p.id for p in report.score.parts],'p0001',1,4)
    result=render(xml,1,4,1200);groups=verify_svg(result['svg'],ms,ns)
    assert len(groups)==4 and {m for g in groups for m in g['measure_ids']}=={m['id'] for m in ms}
    mei=ET.fromstring(result['mei']);ids={e.get('{http://www.w3.org/XML/1998/namespace}id'):e for e in mei.iter()}
    for ident,n in ns.items():
        e=ids[ident]
        if n['written_step']:
            assert e.get('pname')==n['written_step'].lower() and e.get('oct')==n['written_octave']
            assert (e.get('dur') or next(parent.get('dur') for parent in mei.iter() if e in list(parent)))==('4' if name=='pickup' and n['measure_index']==1 else '1')
    if name=='grandstaff':
        assert {n['staff'] for n in ns.values()}=={'1','2'}
        assert len(report.score.parts[0].voices)==2
    if name=='voices':assert len({n['voice_id'] for n in ns.values()})==2 and {n['staff'] for n in ns.values()}=={'1'}
    if name=='pickup':assert ms[0]['printed_label']=='0' and ms[0]['ordinal']==1
    if name=='duplicate':assert [m['label_occurrence'] for m in ms]==[1,2,3,4] and {m['printed_label'] for m in ms}=={'7'}
    if name=='ties':
        assert ns[ms[0]['note_ids'][0]]['event_id']==ns[ms[1]['note_ids'][0]]['event_id']
        assert len([e for e in mei.iter() if e.tag.endswith('}tie')])>=1
    if name=='transpose':
        event=report.score.parts[0].events[0]
        assert event.written_pitch.step=='C' and event.sounding_pitch.step=='B' and event.sounding_pitch.alteration==-1
    assert p.read_bytes()==before


def test_inherited_crop_determinism_and_missing_mapping_rejected():
    p=F/'v02_m2/inherited.musicxml';r=analyze_score(p)
    root,ms,ns=source_index(p.read_bytes(),r.score.model_dump(mode='json'))
    xml,ms,ns=aligned_range(root,ms,ns,['p0001'],'p0001',2,3)
    a=render(xml,2,3,1200);b=render(xml,2,3,1200)
    assert a['svg']==b['svg']
    assert 'clef' in a['svg'] and 'meterSig' in a['svg']
    verify_svg(a['svg'],ms,ns)
    with pytest.raises(ViewerFailure):verify_svg(a['svg'].replace(next(iter(ns)),'missing'),ms,ns)
    with pytest.raises(ViewerFailure):verify_svg(a['svg'].replace('</svg>','<script>alert(1)</script></svg>'),ms,ns)


def test_original_fixtures_doctype_ties_and_irregular_parts():
    for name in ('piano_multivoice','transposing','timing_grace'):
        p=F/f'{name}.musicxml';r=analyze_score(p)
        root,ms,ns=source_index(p.read_bytes(),r.score.model_dump(mode='json'))
        assert len(ns)==sum(len(p.source_segments) for p in r.score.parts)
    p=F/'multi_part.musicxml';r=analyze_score(p);root,ms,ns=source_index(p.read_bytes(),r.score.model_dump(mode='json'))
    with pytest.raises(ViewerFailure,match='different measure'):
        aligned_range(root,ms,ns,[p.id for p in r.score.parts],'p0001',1,2)
    xml,selected,required=aligned_range(root,ms,ns,['p0001'],'p0001',1,1)
    assert {m['part_id'] for m in selected}=={'p0001'}


def test_selection_roundtrip_paging_findings_and_tie_context():
    a=args(page_size=1,max_bytes=8192);view=run(a);items=[];current=view
    while True:
        assert len(current.canonical_json().encode())<=8192
        items.extend(current.items)
        if not current.next_cursor:break
        current=run(a.model_copy(update={'cursor':current.next_cursor}))
    assert len(items)==view.total_items
    assert {i.data['rule_id'] for i in items if i.record_ref.startswith('diagnostic:')}=={'parallel_fifths'}
    assert decode_selection(view.selection_ref)==view.selection
    assert '/' not in view.selection_ref and str(F) not in view.selection_ref
    sel=view.selection.model_copy(update={'start_measure_index':2,'end_measure_index':2})
    resolved=run(ResolveArguments(path=a.path,expected_snapshot=a.expected_snapshot,selection=sel),True)
    findings=[i.data for i in resolved.items if i.record_ref.startswith('diagnostic:')]
    assert len(findings)==2 and all(f['outside_selection_measure_ids'] for f in findings)
    a=args('ties',start_measure_index=2,end_measure_index=2);v=run(a)
    assert any(i.record_ref.startswith('outside_segment:') and i.data['segment']['location']['measure_index']==1 for i in v.items)


@pytest.mark.parametrize('change',[{'start_measure_index':0},{'end_measure_index':10},{'part_ids':['p0001','p0001']},{'reference_part_id':'p9999'},{'start_measure_index':True}])
def test_invalid_selections(change):
    with pytest.raises((ViewerFailure,ValidationError)):
        run(args(**change))


def test_stale_source_options_cross_score_and_forgery(tmp_path):
    a=args();v=run(a)
    other=args('rests')
    with pytest.raises(ViewerFailure,match='differ'):
        run(ResolveArguments(path=other.path,expected_snapshot=other.expected_snapshot,selection_ref=v.selection_ref),True)
    with pytest.raises(ViewerFailure,match='changed'):
        run(a.model_copy(update={'options':{'midi':{'quantize_post':False}}}))
    p=tmp_path/'copy.musicxml';p.write_bytes(Path(a.path).read_bytes());old=a.model_copy(update={'path':str(p)})
    p.write_bytes(p.read_bytes().replace(b'<step>C</step>',b'<step>D</step>',1))
    with pytest.raises(ViewerFailure,match='changed'):run(old)
    with pytest.raises(ViewerFailure):decode_selection(v.selection_ref[:-2]+'xx')
    with pytest.raises(ValidationError):ResolveArguments(path=a.path,expected_snapshot=a.expected_snapshot,selection={**v.selection.model_dump(),'event_id':'forged'})
    with pytest.raises(ViewerFailure):run(a.model_copy(update={'cursor':'x:1'}))


def test_optional_renderer_failure_keeps_text_and_m1(monkeypatch):
    import composerlint.renderers.verovio as adapter
    from importlib.metadata import PackageNotFoundError
    monkeypatch.setattr(adapter,'version',lambda *a: (_ for _ in ()).throw(PackageNotFoundError()))
    v=run(args(presentation='auto'));assert v.presentation=='text' and v.status=='renderer_unavailable'
    assert run(args()).status=='text'
    assert get_review_page(args().path).engine_completed
    monkeypatch.setattr(adapter,'version',lambda *a:'6.3.0')
    monkeypatch.setattr(adapter.subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(subprocess.TimeoutExpired('worker',15)))
    with pytest.raises(ViewerFailure,match='15 seconds'):render('<score-partwise/>',1,1,1200)


def test_entity_active_and_stale_xml_rejected():
    p=F/'v02_m2/duet.musicxml';data=p.read_bytes();score=analyze_score(p).score.model_dump(mode='json')
    with pytest.raises(ViewerFailure,match='match'):source_index(data+b' ',score)
    for prefix in (b'<!DOCTYPE score-partwise [<!ENTITY evil SYSTEM "file:///etc/passwd">]>',):
        bad=prefix+data;copy=deepcopy(score);copy['source_sha256']=hashlib.sha256(bad).hexdigest()
        with pytest.raises(ViewerFailure,match='entity'):source_index(bad,copy)


def test_runtime_http_resource_hash_scope_revision_expiry_and_shutdown():
    runtime.close()
    try:
        v=run(args(presentation='interactive'))
        assert v.presentation=='interactive' and v.svg_uri
        content,mime=runtime.resource(v.svg_uri)
        assert mime=='image/svg+xml' and hashlib.sha256(content).hexdigest()==v.svg_sha256
        with urllib.request.urlopen(v.svg_url) as response: assert response.read()==content
        req=urllib.request.Request(v.viewer_url+'selection',data=json.dumps({'start':2,'end':3,'revision':1}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as response: result=json.load(response)
        sel=decode_selection(result['selection_ref'])
        assert (sel.start_measure_index,sel.end_measure_index)==(2,3)
        runtime.validate_live(result['view_instance_id'],1,sel)
        with pytest.raises(ViewerFailure):runtime.validate_live(result['view_instance_id'],2,sel)
        for suffix,headers in [('score.svg',{'Origin':'https://hostile.example'}),('score.svg',{'Host':'hostile.example'}),('../score.svg',{})]:
            with pytest.raises(urllib.error.HTTPError) as blocked:urllib.request.urlopen(urllib.request.Request(v.viewer_url+suffix,headers=headers))
            blocked.value.close()
        with pytest.raises(ViewerFailure):runtime.resource('file:///etc/passwd')
        runtime.entries[v.view_instance_id]['touched']-=1801
        with pytest.raises(ViewerFailure):runtime.validate_live(result['view_instance_id'],1,sel)
        with pytest.raises(urllib.error.HTTPError) as e:urllib.request.urlopen(v.svg_url)
        assert e.value.code==410
        e.value.close()
    finally:runtime.close()
    assert runtime.server is None and not runtime.entries


def test_schema_errors_are_additive():
    import jsonschema
    assert [t.name for t in TOOLS[:7]]==['analyze_score','detect_style','inspect_measure','check_harmony','check_voice_leading','get_style_evidence','get_review_page']
    for t in TOOLS[-2:]:
        r,error=dispatch(t.name,{'path':'x','unknown':1});assert error
        jsonschema.validate(r.model_dump(mode='json'),output_schema(t))
        schema=input_schema(t)
        branches=[schema['$defs'][b['$ref'].split('/')[-1]] for b in schema['anyOf']] if 'anyOf' in schema else [schema]
        assert all(b['additionalProperties'] is False for b in branches)
