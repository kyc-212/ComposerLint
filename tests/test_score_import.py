"""Authored archives, adversarial admission, original freshness and real Studio roundtrip."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import zipfile
import pytest
from composerlint import score_import as imp, service
from composerlint.errors import ComposerLintError
from composerlint.tool_contracts import dispatch

F = Path(__file__).parent / 'fixtures'
XML = (F / 'v02_m3/variants/solo_horn.musicxml').read_bytes()
CONTAINER = b'<container><rootfiles><rootfile full-path="scores/main.musicxml" media-type="application/vnd.recordare.musicxml+xml"/></rootfiles></container>'


def archive(path, members=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in (members if members is not None else [('decoy.xml', b'<wrong/>'), ('META-INF/container.xml', CONTAINER), ('scores/main.musicxml', XML)]):
            z.writestr(name, data)
    return path


@pytest.fixture(autouse=True)
def cleanup(monkeypatch):
    created = []
    real = imp.tempfile.mkdtemp
    def track(*args, **kwargs):
        value = real(*args, **kwargs); created.append(value); return value
    monkeypatch.setattr(imp.tempfile, 'mkdtemp', track)
    yield
    for value in created: shutil.rmtree(value, ignore_errors=True)


def test_declared_root_and_identity_review_edit(tmp_path):
    source = archive(tmp_path / 'input.mxl'); original = source.read_bytes()
    result, error = dispatch('import_score', {'path': str(source)})
    assert not error, result
    working = Path(result.working_path)
    assert working.read_bytes() == XML and source.read_bytes() == original
    assert result.original_sha256 == imp.sha(original) and result.musicxml_sha256 == imp.sha(XML)
    assert result.working_path != result.original_path
    assert stat.S_IMODE(working.parent.stat().st_mode) == 0o700
    assert set(p.name for p in working.parent.iterdir()) == {'score.musicxml', 'import.json'}
    report = service.analyze_score(working)
    assert report.score.source_sha256 == result.musicxml_sha256
    page = service.get_review_page(working)
    assert page.snapshot_id
    from composerlint.editing_runtime import read_source
    from composerlint.editing_source import resolve
    from composerlint.editing_pitch import transform
    from composerlint.editing_verify import verify_exact
    from composerlint.editing_models import ExactPitchBatch
    _, data, identity = read_source(working)
    scope = dict(start_measure_index=1, end_measure_index=2)
    target = dict(part_id='p0001', lane_mode='unique')
    resolved = resolve(data, report, scope, target)
    op = ExactPitchBatch(kind='exact_pitch_batch', changes=[dict(kind='transpose', diatonic_steps=1, semitones=2)]).model_dump(mode='json')
    changed, _ = transform(data, resolved, op)
    candidate = tmp_path / 'candidate.musicxml'; candidate.write_bytes(changed)
    verified = verify_exact(data, changed, report, service.analyze_score(candidate), scope, target, op)
    assert all(verified['gates'].values()) and working.read_bytes() == XML
    source.write_bytes(original + b'changed')
    with pytest.raises(ComposerLintError, match='changed'): service.get_review_page(working)
    from composerlint.viewer_source import ViewerFailure
    with pytest.raises(ViewerFailure, match='changed'): read_source(working, identity)


def test_musicxml_passthrough_and_midi_unchanged():
    source = F / 'basic.musicxml'
    result = imp.import_score(str(source))
    assert result.working_path == str(source.absolute()) and result.receipt_path is None
    assert result.conversion_method == 'existing_musicxml'
    assert service.analyze_score(F / 'basic.mid').score.format == 'midi'


@pytest.mark.parametrize('name', ['../escape.xml', '/absolute.xml', 'a/../../escape', 'a\\escape', 'C:/escape', 'a/./b', 'a//b'])
def test_unsafe_archive_names(tmp_path, name):
    with pytest.raises(ComposerLintError): imp.import_score(str(archive(tmp_path/'bad.mxl', [(name, XML)])))


@pytest.mark.parametrize('case', ['duplicate', 'symlink', 'fifo', 'ratio', 'oversize', 'count', 'malformed', 'missing', 'root', 'ambiguous', 'encoding', 'entity', 'asset', 'crc'])
def test_archive_controls(tmp_path, case):
    source = tmp_path/'bad.mxl'
    members = [('META-INF/container.xml', CONTAINER), ('scores/main.musicxml', XML)]
    if case == 'duplicate': members += [('SCORES/main.musicxml', XML)]
    if case == 'ratio': members += [('bomb', b'0'*100000)]
    if case == 'oversize': members += [('large', os.urandom(1024))]
    if case == 'count': members += [(str(i), b'x') for i in range(512)]
    if case == 'missing': members = [('other.xml', XML)]
    if case == 'root': members[1] = ('scores/main.musicxml', b'<museScore/>')
    if case == 'ambiguous': members[0] = ('META-INF/container.xml', CONTAINER.replace(b'</rootfiles>', b'<rootfile full-path="other.xml"/></rootfiles>'))
    if case == 'encoding': members[1] = ('scores/main.musicxml', '<?xml version="1.0" encoding="UTF-16"?><score-partwise/>'.encode('utf-16'))
    if case == 'entity': members[1] = ('scores/main.musicxml', b'<!DOCTYPE score-partwise [<!ENTITY x "x">]><score-partwise/>')
    if case == 'asset': members[1] = ('scores/main.musicxml', XML.replace(b'</score-partwise>', b'<image source="file:///private"/></score-partwise>'))
    archive(source, members)
    if case in ('symlink', 'fifo'):
        with zipfile.ZipFile(source, 'a') as z:
            info = zipfile.ZipInfo('unsafe'); info.create_system = 3
            info.external_attr = ((stat.S_IFLNK if case == 'symlink' else stat.S_IFIFO) | 0o600) << 16
            z.writestr(info, 'target')
    if case == 'oversize':
        # Source compressed bytes pass, inflated selected member exceeds bound.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(imp, 'MAX_SOURCE', 100)
            with pytest.raises(ComposerLintError): imp.archive_entries(source.read_bytes())
        return
    if case == 'malformed': source.write_bytes(b'not a ZIP')
    if case == 'crc':
        data=bytearray(source.read_bytes()); data[60] ^= 0xff; source.write_bytes(data)
    original = source.read_bytes()
    with pytest.raises(ComposerLintError): imp.import_score(str(source))
    assert source.read_bytes() == original


def test_symlink_source_and_provenance_tampering(tmp_path):
    source = archive(tmp_path/'source.mxl')
    link = tmp_path/'linked.mxl'; link.symlink_to(source)
    with pytest.raises(ComposerLintError): imp.import_score(str(link))
    r = imp.import_score(str(source)); working = Path(r.working_path)
    working.chmod(0o600); working.write_bytes(XML+b' ')
    with pytest.raises(ComposerLintError, match='changed'): imp.verify_import(working)
    working.write_bytes(XML); Path(r.receipt_path).unlink()
    with pytest.raises(ComposerLintError): imp.verify_import(working)


@pytest.mark.parametrize('mode', ['missing', 'failed', 'version', 'no_output', 'bad_output', 'linked_output'])
def test_converter_failures(tmp_path, monkeypatch, mode):
    source = archive(tmp_path/'input.mscz'); original = source.read_bytes()
    if mode == 'missing':
        monkeypatch.setenv('COMPOSERLINT_MUSESCORE', str(tmp_path/'absent'))
    else:
        monkeypatch.setattr(imp, 'musescore_executable', lambda: '/installed/mscore')
        def fake(executable, args, directory):
            if args == ['--version']: return 'MuseScore 3.6' if mode == 'version' else 'MuseScore4 4.7.3'
            if mode == 'failed': imp.fail('conversion failed')
            if mode == 'bad_output': Path(args[1]).write_bytes(b'<wrong/>')
            if mode == 'linked_output': Path(args[1]).symlink_to(source)
            return ''
        monkeypatch.setattr(imp, 'run_musescore', fake)
    result, error = dispatch('import_score', {'path':str(source)})
    assert error and result.error.code == 'parse_failed' and source.read_bytes() == original


def test_timeout_reaps_process_group(tmp_path):
    import sys
    with pytest.MonkeyPatch.context() as patch:
        original_wait = imp.subprocess.Popen.wait
        calls = []
        def timeout_once(self, timeout=None):
            if timeout == 45:
                calls.append(self.pid); raise subprocess.TimeoutExpired(self.args, timeout)
            return original_wait(self, timeout=timeout)
        patch.setattr(imp.subprocess.Popen, 'wait', timeout_once)
        with pytest.raises(ComposerLintError, match='timed out'):
            imp.run_musescore(sys.executable, ['-c', 'import time; time.sleep(60)'], tmp_path)
        with pytest.raises(ProcessLookupError): os.kill(calls[0], 0)


@pytest.mark.skipif(os.environ.get('COMPOSERLINT_TEST_MUSESCORE') != '1', reason='opt-in installed MuseScore demonstration')
def test_actual_mxl_mscz_roundtrip(tmp_path):
    exe = imp.musescore_executable()
    authored = tmp_path/'authored.musicxml'; authored.write_bytes((F/'v02_m6/export-review.musicxml').read_bytes())
    native = tmp_path/'authored.mscz'
    imp.run_musescore(exe, ['-o', str(native), str(authored)], tmp_path)
    assert native.is_file() and zipfile.is_zipfile(native)
    mxl = archive(tmp_path/'authored.mxl', [('META-INF/container.xml', CONTAINER), ('scores/main.musicxml', authored.read_bytes())])
    for source in (mxl, native):
        original = source.read_bytes(); result = imp.import_score(str(source))
        report = service.analyze_score(result.working_path)
        assert report.score.source_sha256 == result.musicxml_sha256
        reopened = tmp_path/(source.suffix[1:]+'-reopened.mscz')
        imp.run_musescore(exe, ['-o', str(reopened), result.working_path], tmp_path)
        assert zipfile.is_zipfile(reopened) and source.read_bytes() == original
        print(source.suffix, 'imported, analyzed, reopened by installed MuseScore; source unchanged')



def test_successful_converter_arguments_and_snapshot_copy(tmp_path, monkeypatch):
    source = archive(tmp_path/'input.mscz'); original = source.read_bytes(); calls = []
    monkeypatch.setattr(imp, 'musescore_executable', lambda: '/installed/MuseScore Studio')
    def converter(exe, args, directory):
        calls.append(args)
        if args == ['--version']: return 'MuseScore4 4.7.3'
        assert args[0] == '-o' and Path(args[2]).read_bytes() == original
        assert Path(args[2]) != source and str(source) not in args
        Path(args[1]).write_bytes(XML)
        return 'exported'
    monkeypatch.setattr(imp, 'run_musescore', converter)
    r = imp.import_score(str(source))
    assert r.source_format == 'mscz' and r.converter_version == 'MuseScore4 4.7.3'
    assert r.conversion_method == 'musescore_cli_musicxml' and Path(r.working_path).read_bytes() == XML
    assert source.read_bytes() == original and len(calls) == 2


def test_real_stdio_import_and_analysis(tmp_path):
    import asyncio, sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import jsonschema
    source = archive(tmp_path/'source.mxl')
    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-B','-m','composerlint.server'],
            cwd=Path(__file__).parents[1], env={'PYTHONDONTWRITEBYTECODE':'1'})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=45) as client:
                await client.initialize()
                tools = {t.name:t for t in (await client.list_tools()).tools}
                tool = tools['import_score']; assert not tool.annotations.read_only_hint
                result = await client.call_tool('import_score', {'path':str(source)})
                assert not result.is_error
                data = result.structured_content; jsonschema.validate(data, tool.output_schema)
                assert json.loads(result.content[0].text) == data
                working = Path(data['working_path'])
                try:
                    report = await client.call_tool('analyze_score', {'path':str(working)})
                    assert not report.is_error and report.structured_content['score']['source_sha256'] == data['musicxml_sha256']
                    source.write_bytes(source.read_bytes()+b'changed')
                    stale = await client.call_tool('get_review_page', {'path':str(working)})
                    assert stale.is_error
                finally: shutil.rmtree(working.parent)
    asyncio.run(check())


def test_nonzero_converter_refuses_even_when_output_exists(tmp_path):
    import sys
    script = "from pathlib import Path; import sys; Path('unexpected.musicxml').write_text('<score-partwise/>'); sys.exit(1)"
    with pytest.raises(ComposerLintError, match='status 1'):
        imp.run_musescore(sys.executable, ['-c',script], tmp_path)
    assert (tmp_path/'unexpected.musicxml').exists()


def test_imported_actual_workspace_preview_and_original_invalidation(tmp_path):
    from composerlint.editing_runtime import editing
    from composerlint.viewer_runtime import runtime
    from composerlint.viewer_source import ViewerFailure
    from test_v02_editing_runtime import open_workspace, arguments
    source = archive(tmp_path/'source.mxl')
    result = imp.import_score(str(source)); working = Path(result.working_path)
    try:
        w, _ = open_workspace(working)
        proposal, error = dispatch('prepare_score_edit', arguments(w))
        assert not error, proposal
        assert w['proposal']['preview_status'] == 'ready'
        assert all(w['proposal']['verification']['gates'].values())
        assert working.read_bytes() == XML
        source.write_bytes(source.read_bytes()+b'changed')
        with pytest.raises(ViewerFailure, match='changed'): editing.current(w)
        assert w['state'] == 'stale'
    finally: editing.close(); runtime.close()
