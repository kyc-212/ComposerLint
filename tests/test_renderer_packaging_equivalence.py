"""Opt-in comparison of installed official/patched builds; authored controls only."""
from contextlib import nullcontext
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
        _,identity=verovio.renderer_command()
        assert identity=='6.3.0+composerlint.tie1'
    else:
        monkeypatch.setattr(verovio,'renderer_scope',lambda:nullcontext((sys.executable,None)))
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


@pytest.mark.parametrize('altered', [False, True])
def test_windows_render_holds_pins_through_worker_exit(monkeypatch, tmp_path, altered):
    from contextlib import contextmanager
    import json
    import subprocess

    from composerlint import windows_install_storage, windows_renderer as pins

    config = tmp_path / 'renderer.json'
    executable = 'renderer/Scripts/python.exe'
    native_module = 'renderer/Lib/site-packages/verovio/_verovio.pyd'
    artifacts = (pins.OFFICIAL_WHEEL, pins.SOURCE_ARCHIVE, pins.PATCH, pins.PATCHED_WHEEL)
    data = {name: name.encode() for name in (
        executable, native_module, 'python/python.exe', 'renderer/pyvenv.cfg',
        *('renderer-artifacts/' + name for name in artifacts),
    )}
    hashes = {name: pins.sha(raw) for name, raw in data.items()}
    for name, attribute in zip(artifacts[:3], ('OFFICIAL_SHA256', 'SOURCE_SHA256', 'PATCH_SHA256'), strict=True):
        monkeypatch.setattr(pins, attribute, hashes['renderer-artifacts/' + name])
    data[config.name] = json.dumps({
        'package_version': pins.BUILD, 'python': executable, 'files_sha256': hashes,
    }).encode()
    if altered:
        data[native_module] = b'changed after manifest'
    held = set()
    launches = []

    @contextmanager
    def install_files(root):
        assert root == tmp_path

        def read(parts, *, limit):
            key = '/'.join(parts)
            held.add(key)
            return data[key]

        try:
            yield read, lambda: None
        finally:
            held.clear()

    def run(command, **kwargs):
        assert set(data) == held
        assert command[0] == str(tmp_path.joinpath(*executable.split('/')))
        launches.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            'package_version': pins.BUILD, 'pages': ['<svg/>'], 'log': '',
        }), '')

    monkeypatch.setattr(windows_install_storage, 'pinned_install_files', install_files)
    monkeypatch.setattr(verovio.host, 'platform_name', lambda: 'win32')
    monkeypatch.setattr(verovio.host, 'run_owned', run)
    monkeypatch.setattr(verovio.runtime_paths, 'renderer_config', lambda: config)
    monkeypatch.setattr(verovio, 'version', lambda package: '6.3.0')
    if altered:
        with pytest.raises(ViewerFailure, match='verified files'):
            verovio.render('<score-partwise/>')
    else:
        assert verovio.render('<score-partwise/>')['pages'] == ['<svg/>']
    assert len(launches) == (0 if altered else 1)
    assert not held
