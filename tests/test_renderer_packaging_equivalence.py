"""Opt-in comparison of installed official/patched builds; authored controls only."""
from pathlib import Path
import os
import sys
import pytest
from composerlint.renderers import verovio
from composerlint.viewer_source import ViewerFailure
from test_v02_renderer_ties import score, deferred_tie_measure, endpoints


@pytest.fixture(params=['official','patched'])
def build(request,monkeypatch):
    if os.environ.get('COMPOSERLINT_RENDERER_EQUIVALENCE')!='1':
        pytest.skip('Explicit opt-in required for installed renderer comparison.')
    if request.param=='patched':
        executable,identity=verovio.renderer_command()
        assert identity=='6.3.0+composerlint.tie1'
    else:executable,identity=sys.executable,None
    monkeypatch.setattr(verovio,'renderer_command',lambda:(executable,identity))
    return request.param


@pytest.mark.parametrize('count',[1,4])
def test_deferred_tie_equivalence_boundary(build,count):
    xml=score(''.join(deferred_tie_measure(n) for n in range(1,count+1)))
    if build=='official':
        with pytest.raises(ViewerFailure,match='ties left open'):verovio.render(xml)
    else:
        result=verovio.render(xml)
        assert endpoints(result)==[(f'#start{n}',f'#end{n}') for n in range(1,count+1)]


def test_forward_ties_remain_exact(build):
    from test_v02_renderer_ties import test_existing_forward_order_cross_measure_tie_remains_exact
    test_existing_forward_order_cross_measure_tie_remains_exact()


def test_invalid_notation_is_not_silenced(build):
    from test_v02_renderer_ties import (test_genuinely_open_tie_still_fails_the_warning_gate,
        test_unsupported_multimetric_notation_still_fails)
    test_genuinely_open_tie_still_fails_the_warning_gate()
    test_unsupported_multimetric_notation_still_fails()


def test_complete_authored_page_source_mapping(build):
    import xml.etree.ElementTree as ET
    from composerlint import passage
    from composerlint.viewer_source import source_index
    from composerlint.viewer import verify_pages
    source=Path(__file__).parent/'fixtures/v02_m2/multipage.musicxml'
    prepared=passage.prepare(source,None,{})
    root,measures,notes=source_index(source.read_bytes(),prepared.score.model_dump(mode='json'))
    result=verovio.render(ET.tostring(root,encoding='unicode'))
    pages=verify_pages(result,measures,notes,source_bound=True)
    assert len(pages)==result['page_count']>1
