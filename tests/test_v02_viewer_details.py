"""Dense natural layout and exact grace/tuplet source correspondence."""
from pathlib import Path
import json,hashlib
import xml.etree.ElementTree as ET
from composerlint.service import analyze_score
from composerlint.viewer_source import source_index,aligned_range
from composerlint.viewer import verify_pages
from composerlint.renderers.verovio import render
F=Path(__file__).parent/'fixtures/v02_m2'


def test_dense_and_sparse_natural_layout_have_complete_different_page_membership():
    memberships={}
    for name,parts,end in [('dense',['p0001'],32),('multipage',['p0001','p0002'],96)]:
        p=F/f'{name}.musicxml';before=p.read_bytes();expected=json.loads(p.with_suffix('.expected.json').read_text())
        assert hashlib.sha256(before).hexdigest()==expected['source_sha256']
        root,ms,ns=source_index(before,analyze_score(p).score.model_dump(mode='json'))
        xml,ms,ns=aligned_range(root,ms,ns,parts,parts[0],1,end,bounded=False)
        result=render(xml);pages=verify_pages(result,ms,ns)
        memberships[name]=[len(p['groups']) for p in pages]
        assert sum(memberships[name])==end
        assert p.read_bytes()==before
    assert memberships['dense']!=memberships['multipage']
    assert max(memberships['multipage'])>8


def test_grace_tuplet_rhythm_maps_exact_segments_without_rounded_onsets():
    p=F/'rhythm.musicxml';r=analyze_score(p)
    root,ms,ns=source_index(p.read_bytes(),r.score.model_dump(mode='json'))
    assert len(ns)==10
    xml,ms,ns=aligned_range(root,ms,ns,['p0001'],'p0001',1,4)
    result=render(xml,1,4);assert len(verify_pages(result,ms,ns))==1
    mei=ET.fromstring(result['mei'])
    assert any(e.tag.endswith('}tuplet') for e in mei.iter())
    assert any(e.get('grace') for e in mei.iter())
    segments=r.score.parts[0].source_segments
    assert segments[0].normalized_duration.numerator==0
    assert [(s.normalized_duration.numerator,s.normalized_duration.denominator) for s in segments[1:4]]==[(1,3)]*3


import pytest
from composerlint.viewer_source import ViewerFailure


@pytest.mark.parametrize('name',['ending','empty_hidden','multirest'])
def test_endings_empty_hidden_and_multirest_never_merge_source_measures(name):
    p=F/f'{name}.musicxml';before=p.read_bytes();r=analyze_score(p)
    expected=json.loads(p.with_suffix('.expected.json').read_text())
    assert hashlib.sha256(before).hexdigest()==expected['source_sha256']
    root,ms,ns=source_index(before,r.score.model_dump(mode='json'))
    assert len(ms)==4
    xml,ms,ns=aligned_range(root,ms,ns,['p0001'],'p0001',1,4)
    try:
        result=render(xml);pages=verify_pages(result,ms,ns)
    except ViewerFailure as exc:
        assert name=='multirest' and exc.code in {'mapping_unavailable','render_failed'}
    else:
        assert sum(len(page['groups']) for page in pages)==4
        if name=='ending':
            endings=[e for e in ET.fromstring(result['svg']).iter() if 'ending' in e.get('class','').split()]
            assert len(endings)==2 and [''.join(e.itertext()).strip() for e in endings]==['1','2']
        if name=='empty_hidden':assert len(ns)==3 and sum(n['hidden'] for n in ns.values())==1
    assert p.read_bytes()==before
