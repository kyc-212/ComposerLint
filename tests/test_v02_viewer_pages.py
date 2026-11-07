"""Full-page mapping, independent selection limits and real loopback lifecycle."""
from pathlib import Path
import hashlib
import json
import urllib.request
import urllib.error
import pytest
from composerlint.service import get_review_page
from composerlint.viewer import run, decode_selection
from composerlint.viewer_models import ViewArguments, ResolveArguments
from composerlint.viewer_runtime import runtime, Runtime
from composerlint.viewer_source import ViewerFailure

SOURCE=Path(__file__).parent/'fixtures/v02_m2/multipage.musicxml'


@pytest.fixture(scope='module')
def opened():
    before=SOURCE.read_bytes()
    args=ViewArguments(path=str(SOURCE),expected_snapshot=get_review_page(SOURCE).snapshot_id,presentation='interactive')
    view=run(args)
    yield args,view,runtime.find(view.view_instance_id)
    assert SOURCE.read_bytes()==before
    runtime.close()


def read(url):
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def post_selection(view,start,end):
    current=read(view.viewer_url+'selection')
    body=json.dumps(dict(start=start,end=end,revision=current['revision']+1)).encode()
    request=urllib.request.Request(view.viewer_url+'selection',data=body,headers={'Content-Type':'application/json'})
    return read(request)


def test_default_full_page_no_range_no_selection_and_natural_coverage(opened):
    args,v,e=opened
    assert args.mode=='whole_score' and args.start_measure_index is None
    assert v.notation_page==1 and v.page_count>=3 and v.selection is None and v.selection_ref is None
    assert read(v.viewer_url+'selection')['selection'] is None
    pages=e['manifest']['pages'];ms=e['manifest']['measures']
    assert any(len(p['groups'])>8 for p in pages)
    assert len({m for p in pages for m in p['measure_ids']})==192
    assert sum(len(p['measure_ids']) for p in pages)==192
    for page in pages:
        selected=[m for m in ms if m['id'] in page['measure_ids']]
        assert {m['part_id'] for m in selected}=={'p0001','p0002'}
        assert len({m['ordinal'] for m in selected})==len(page['groups'])
    expected=json.loads(SOURCE.with_suffix('.expected.json').read_text())
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==expected['source_sha256']
    assert b'<print' not in SOURCE.read_bytes()  # No authored system/page breaks.


def test_page_requests_resources_and_metadata_are_independent(opened):
    a,v,e=opened
    for number in range(1,v.page_count+1):
        page=run(a.model_copy(update=dict(view_instance_id=v.view_instance_id,notation_page=number,page_size=1,max_bytes=8192)))
        assert page.page_count==v.page_count and page.notation_page==number
        assert page.view_key==v.view_key and page.view_instance_id==v.view_instance_id
        data,mime=runtime.resource(page.svg_uri)
        with urllib.request.urlopen(page.svg_url) as response:
            assert response.read()==data and response.headers['Content-Type']=='image/svg+xml'
        assert mime=='image/svg+xml' and hashlib.sha256(data).hexdigest()==page.svg_sha256
        assert page.selection is None and page.returned_count==1 and len(page.canonical_json().encode())<=8192
    for number in [v.page_count+1]:
        with pytest.raises(ViewerFailure,match='outside'):
            run(a.model_copy(update=dict(view_instance_id=v.view_instance_id,notation_page=number)))


def test_ninth_measure_and_later_page_selection_use_exact_original_segments(opened):
    a,v,e=opened
    result=post_selection(v,9,9)
    sel=decode_selection(result['selection_ref'])
    assert sel.start_measure_index==sel.end_measure_index==9
    resolved=run(ResolveArguments(path=a.path,expected_snapshot=a.expected_snapshot,selection_ref=result['selection_ref'],
        view_instance_id=v.view_instance_id,revision=result['revision']),True)
    segments=[i.data for i in resolved.items if i.record_ref.startswith('segment:')]
    assert {(s['part_id'],s['staff'],s['written_step'],s['written_octave']) for s in segments}=={
        ('p0001','1','D','5'),('p0002','1','D','4'),('p0002','2','A','2')}
    later=next(m['ordinal'] for m in e['manifest']['measures'] if m['part_id']=='p0001' and m['id'] in e['manifest']['pages'][-1]['measure_ids'])
    result=post_selection(v,later,later)
    assert decode_selection(result['selection_ref']).start_measure_index==later
    runtime.validate_live(v.view_instance_id,result['revision'],decode_selection(result['selection_ref']))


def test_cross_page_eight_measure_range_and_overflow_preserve_selection(opened):
    a,v,e=opened
    first=[m['ordinal'] for m in e['manifest']['measures'] if m['part_id']=='p0001' and m['id'] in e['manifest']['pages'][0]['measure_ids']]
    boundary=max(first)
    result=post_selection(v,boundary-3,boundary+4)
    assert decode_selection(result['selection_ref']).end_measure_index==boundary+4
    with pytest.raises(urllib.error.HTTPError) as error: post_selection(v,boundary-3,boundary+5)
    assert error.value.code==400
    error.value.close()
    assert read(v.viewer_url+'selection')==result
    assert len(e['manifest']['pages'][0]['groups'])>8
    for number in range(1,v.page_count+1):
        with urllib.request.urlopen(v.viewer_url+f'page-{number}.svg') as response: assert response.read()
    assert read(v.viewer_url+'selection')==result  # Off-page state is independent of assets.


def test_focus_roundtrip_clear_and_live_revision_invalidation(opened):
    a,v,e=opened
    result=post_selection(v,9,10)
    focused=read(v.viewer_url+'focus-9-10.json')
    assert len(focused['groups'])==2
    assert read(v.viewer_url+'selection')==result
    with urllib.request.urlopen(v.viewer_url+'page-1.svg') as response: assert response.read()==e['pages'][0]
    cleared=post_selection(v,None,None)
    assert cleared['selection'] is None and cleared['revision']==result['revision']+1
    with pytest.raises(ViewerFailure,match='changed'):
        runtime.validate_live(v.view_instance_id,result['revision'],decode_selection(result['selection_ref']))
    # A copy reference remains source-bound and independently resolvable after clearing.
    assert run(ResolveArguments(path=a.path,expected_snapshot=a.expected_snapshot,selection_ref=result['selection_ref']),True).status=='resolved'


def test_whole_score_and_explicit_focused_source_identity_parity(opened):
    a,v,e=opened
    focused=run(a.model_copy(update=dict(mode='focused',start_measure_index=9,end_measure_index=10)))
    assert focused.mode=='focused' and focused.page_count==1
    assert (focused.source_sha256,focused.snapshot_id,focused.options_sha256)==(v.source_sha256,v.snapshot_id,v.options_sha256)
    assert focused.selection.start_measure_index==9
    assert focused.view_key!=v.view_key


def test_bounded_cache_expiry_is_explicit_and_surviving_view_retains_selection(opened,monkeypatch):
    a,v,e=opened
    cache=Runtime();monkeypatch.setattr(cache,'start',lambda:None)
    cache.max_views=2
    first=cache.add('a',e['pages'],e['manifest'])
    kept=cache.add('b',e['pages'],e['manifest'])
    selection=decode_selection(post_selection(v,9,9)['selection_ref'])
    kept['selected']=selection;kept['revision']=1
    cache.find(kept['instance'])
    cache.add('c',e['pages'],e['manifest'])
    assert cache.find(first['instance']) is None
    cache.validate_live(kept['instance'],1,selection)
    with pytest.raises(ViewerFailure,match='expired'):cache.validate_live(first['instance'],1,selection)
    cache.max_bytes=1
    with pytest.raises(ViewerFailure,match='budget'):cache.add('d',e['pages'],e['manifest'])


def test_renderer_corruption_and_unequal_trailing_measures_are_explicit():
    from composerlint.viewer import verify_pages
    from composerlint.viewer_source import aligned_range,source_index
    from composerlint.service import analyze_score
    p=Path(__file__).parent/'fixtures/multi_part.musicxml'
    root,ms,ns=source_index(p.read_bytes(),analyze_score(p).score.model_dump(mode='json'))
    with pytest.raises(ViewerFailure,match='different measure'):
        aligned_range(root,ms,ns,['p0001','p0002'],'p0001',1,1,bounded=False)
    with pytest.raises(ViewerFailure,match='invalid complete SVG'):
        verify_pages({'pages':['not XML']},[],{})
