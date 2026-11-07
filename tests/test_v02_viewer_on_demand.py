"""Complete layout ownership is distinct from verified, delivered SVG pages."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from composerlint import passage
from composerlint.renderers import verovio
from composerlint.viewer_source import source_index,ViewerFailure
from composerlint.viewer_pages import layout_pages,verify_page
from composerlint.viewer_runtime import runtime
from composerlint.viewer_models import SourceViewArguments
from composerlint.viewer_passage import run

SOURCE=Path(__file__).parent/'fixtures/v02_m2/multipage.musicxml'


@pytest.fixture(scope='module')
def layout():
    data=SOURCE.read_bytes();source=passage.prepare(SOURCE,None,{})
    root,measures,notes=source_index(data,source.score.model_dump(mode='json'))
    xml=ET.tostring(root,encoding='unicode')
    first=verovio.render(xml,notation_page=1)
    last=verovio.render(xml,notation_page=first['page_count'])
    yield xml,measures,notes,first,last
    assert SOURCE.read_bytes()==data


def test_complete_layout_but_only_requested_svg_and_exact_build(layout):
    xml,ms,ns,first,last=layout
    assert first['package_version']=='6.3.0+composerlint.tie1'
    assert first['page_count']>=3 and len(first['pages'])==len(last['pages'])==1
    assert first['rendered_page']==1 and last['rendered_page']==first['page_count']
    pages=layout_pages(first,ms,ns)
    assert sum(len(p['measure_ids']) for p in pages)==len(ms)==192
    assert pages==layout_pages(last,ms,ns)
    assert max(len(p['groups']) for p in pages)>8
    verify_page(first['svg'],pages[0],ms,ns)
    verify_page(last['svg'],pages[-1],ms,ns)
    assert first['timings']['complete_layout_seconds']>0


@pytest.mark.parametrize('damage',['missing_note','duplicate_measure','missing_page'])
def test_global_layout_losses_rejected_before_page_delivery(layout,damage):
    _,ms,ns,first,_=layout;r=deepcopy(first);root=ET.fromstring(r['mei'])
    if damage=='missing_note':
        ident=next(n for n,v in ns.items() if not v['hidden'])
        element=next(e for e in root.iter() if e.get('{http://www.w3.org/XML/1998/namespace}id')==ident)
        element.set('{http://www.w3.org/XML/1998/namespace}id','lost')
    elif damage=='duplicate_measure':
        page=next(e for e in root.iter() if e.tag.endswith('}page'))
        measure=next(e for e in page.iter() if e.tag.endswith('}measure'));page.append(deepcopy(measure))
    else:
        parent=next(e for e in root.iter() if any(c.tag.endswith('}page') for c in e))
        parent.remove(next(c for c in parent if c.tag.endswith('}page')))
    r['mei']=ET.tostring(root,encoding='unicode')
    with pytest.raises(ViewerFailure):layout_pages(r,ms,ns)


@pytest.mark.parametrize('damage',['wrong_page','missing_note','active_svg'])
def test_layout_alone_cannot_authorize_corrupted_svg(layout,damage):
    _,ms,ns,first,last=layout;pages=layout_pages(first,ms,ns)
    svg=first['svg']
    if damage=='wrong_page':svg=last['svg']
    elif damage=='missing_note':
        ident=next(n for n,v in ns.items() if not v['hidden'] and v['measure_id'] in pages[0]['measure_ids'])
        svg=svg.replace(ident,'lost')
    else:svg=svg.replace('</svg>','<script>bad()</script></svg>')
    with pytest.raises(ViewerFailure):verify_page(svg,pages[0],ms,ns)


def test_configured_build_is_pinned_and_tampering_rejected(monkeypatch):
    executable,identity=verovio.renderer_command()
    config=verovio.runtime_paths.renderer_config()
    assert config is not None
    value=json.loads(config.read_text())
    assert identity=='6.3.0+composerlint.tie1'
    assert Path(executable)==config.parent/value['python'] and Path(executable).is_file()
    key=next(iter(value['files_sha256']))
    value['files_sha256'][key]='0'*64
    monkeypatch.setattr(verovio.json,'loads',lambda text:value)
    with pytest.raises(ViewerFailure,match='verified files'):verovio.renderer_command()


def test_lazy_navigation_budget_and_source_freshness(tmp_path,monkeypatch):
    path=tmp_path/'authored.musicxml';path.write_bytes(SOURCE.read_bytes())
    try:
        view=run(SourceViewArguments(path=str(path),presentation='interactive'))
        entry=runtime.find(view.view_instance_id)
        assert entry['pages'][0] is not None and all(p is None for p in entry['pages'][1:])
        before=entry['size'];revision=entry['revision'];selection=entry['selected']
        page=runtime.page_asset(entry,2)
        assert hashlib.sha256(page).hexdigest()==runtime.references(entry,2)['svg_sha256']
        assert entry['size']==before+len(page)
        assert runtime.page_asset(entry,2) is page
        assert (entry['revision'],entry['selected'])==(revision,selection)
        pending=next(i+1 for i,p in enumerate(entry['pages']) if p is None)
        with monkeypatch.context() as m:
            m.setattr(runtime,'max_bytes',entry['size'])
            with pytest.raises(ViewerFailure,match='budget'):runtime.page_asset(entry,pending)
        assert entry['pages'][pending-1] is None
        path.write_bytes(path.read_bytes()+b'\n')
        with pytest.raises(ViewerFailure,match='changed'):runtime.page_asset(entry,1)
    finally:runtime.close()
