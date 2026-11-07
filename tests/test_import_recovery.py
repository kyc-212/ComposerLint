"""Authored native/XML pairs and negative controls; no personal scores bundled."""
import copy
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import pytest

from composerlint import score_import as imp, service
from composerlint.errors import ComposerLintError
from composerlint.import_recovery import verify_music

DIAGNOSTIC = (b'libc++abi: terminating due to uncaught exception of type std::__1::system_error: '
              b'mutex lock failed: Invalid argument\n')
NATIVE = b'''<museScore version="4.00"><programVersion>4.7.3</programVersion><Score>
<Part id="1"><Staff id="1"><StaffType group="pitched"/></Staff><trackName>Horn</trackName>
<Instrument id="horn"><trackName>Horn</trackName></Instrument></Part><Staff id="1">
<Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>
<Chord><durationType>quarter</durationType><Note><pitch>60</pitch><tpc>14</tpc></Note></Chord>
<Rest><durationType>half</durationType><dots>1</dots></Rest></voice></Measure>
<Measure><voice><Rest><durationType>measure</durationType><duration>1/1</duration></Rest></voice></Measure>
</Staff></Score></museScore>'''
XML = b'''<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Horn</part-name>
</score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>1</divisions>
<time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note><rest/><duration>3</duration><type>half</type><dot/></note></measure>
<measure number="2"><note><rest measure="yes"/><duration>4</duration></note></measure></part></score-partwise>'''
CONTAINER = b'<container><rootfiles><rootfile full-path="main.mscx"/></rootfiles></container>'


def native(path, content=NATIVE):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('META-INF/container.xml', CONTAINER)
        z.writestr('main.mscx', content)
    return path


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    source = native(tmp_path/'input.mscz')
    monkeypatch.setattr(imp, 'musescore_executable', lambda: '/installed/mscore')
    monkeypatch.setattr(imp, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(imp.Path, 'home', classmethod(lambda cls: tmp_path))
    logdir = tmp_path/'Library/Application Support/MuseScore/MuseScore4/logs'
    logdir.mkdir(parents=True)
    settings = dict(output=XML, code=-6, diagnostics=DIAGNOSTIC, completed=True, version='MuseScore4 4.7.3',
                    modify_source=False)
    folders = []
    def run(executable, args, directory):
        if args == ['--version']:
            return settings['version']
        folders.append(directory)
        assert Path(args[2]).read_bytes() == source.read_bytes() and Path(args[2]) != source
        if settings['output'] is not None:
            Path(args[1]).write_bytes(settings['output'])
        text = ('=== Started MuseScore Studio 4.7.3, build: test ===\n'
                f'ConverterController::convertFile | in: {args[2]}, out: {args[1]}\n')
        if settings['completed']:
            text += 'ConsoleApp::finish | activeThreadCount: 0\nGoodbye!! code: 0\n'
        (logdir/'MuseScore_999999_999999.log').write_text(text)
        if settings['modify_source']:
            source.write_bytes(source.read_bytes()+b'changed')
        if settings['code']:
            raise imp.MuseScoreFailure(settings['code'], settings['diagnostics'])
        return 'exported'
    monkeypatch.setattr(imp, 'run_musescore', run)
    yield source, settings, folders, logdir
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)


def test_recovery_receipt_analysis_freshness_and_edit_read(recovered):
    source, settings, _, _ = recovered; original = source.read_bytes()
    result = imp.import_score(str(source))
    assert result.conversion_status == 'recovered'
    assert result.conversion_method == 'musescore_cli_musicxml_recovered'
    r = result.recovery
    assert r.process_exit_code == -6 and (r.parts, r.measures, r.segments) == (1, 2, 3)
    assert Path(r.diagnostics_path).read_bytes() == DIAGNOSTIC
    assert 'shutdown failure' in result.message and source.read_bytes() == original
    assert json.loads(Path(result.receipt_path).read_text())['recovery']['process_exit_code'] == -6
    report = service.analyze_score(result.working_path)
    assert report.score.source_sha256 == result.musicxml_sha256
    from composerlint.editing_runtime import read_source
    assert read_source(Path(result.working_path))[1] == XML
    source.write_bytes(original+b'changed')
    with pytest.raises(ComposerLintError, match='changed'):
        service.get_review_page(result.working_path)


def test_normal_conversion_remains_default(recovered):
    source, settings, _, _ = recovered; settings['code'] = 0
    result = imp.import_score(str(source))
    assert result.conversion_status == 'normal' and result.recovery is None
    assert result.conversion_method == 'musescore_cli_musicxml'
    assert set(p.name for p in Path(result.working_path).parent.iterdir()) == {'score.musicxml', 'import.json'}


@pytest.mark.parametrize('change', [dict(code=1), dict(code=-11), dict(diagnostics=b'other abort'),
    dict(version='MuseScore4 4.7.4'), dict(completed=False), dict(output=None), dict(output=XML[:-10]),
    dict(output=b'<score-partwise/>'), dict(modify_source=True)])
def test_failure_never_admitted(recovered, change):
    source, settings, folders, _ = recovered; original = source.read_bytes(); settings.update(change)
    with pytest.raises(ComposerLintError):
        imp.import_score(str(source))
    assert all(not p.exists() for p in folders)
    if not change.get('modify_source'):
        assert source.read_bytes() == original


@pytest.mark.parametrize('field', ['diagnostics_path', 'shutdown_log_path'])
def test_recovery_artifact_freshness(recovered, field):
    source, _, _, _ = recovered; result = imp.import_score(str(source))
    artifact = Path(getattr(result.recovery, field)); artifact.chmod(0o600)
    artifact.write_bytes(b'changed')
    with pytest.raises(ComposerLintError, match='diagnostics changed'):
        imp.verify_import(result.working_path)


def test_inconsistent_receipt_refuses(recovered):
    source, _, _, _ = recovered; result = imp.import_score(str(source))
    path = Path(result.receipt_path); path.chmod(0o600)
    data = json.loads(path.read_text()); data['conversion_status'] = 'normal'; path.write_text(json.dumps(data))
    with pytest.raises(ComposerLintError, match='Inconsistent'):
        imp.verify_import(result.working_path)


def test_recovered_preview_and_approval_guards(recovered, monkeypatch, tmp_path):
    """Recovery admits Original only; it cannot approve/export an M5 candidate."""
    from test_v02_editing_runtime import open_workspace, arguments
    from composerlint.editing_runtime import editing
    from composerlint.viewer_runtime import runtime
    from composerlint.viewer_source import ViewerFailure
    from composerlint import editing_export as ex
    from composerlint.tool_contracts import dispatch
    source, _, _, _ = recovered; original = source.read_bytes()
    result = imp.import_score(str(source)); working = Path(result.working_path)
    monkeypatch.setattr(ex, 'EXPORT_ROOT', tmp_path/'exports')
    monkeypatch.setattr(ex, 'confirm', lambda _: pytest.fail('M5 recovery must not request export approval'))
    try:
        w, _ = open_workspace(working)
        prepared, error = dispatch('prepare_score_edit', arguments(w))
        assert not error, prepared
        assert w['proposal']['preview_status'] == 'ready' and all(w['proposal']['verification']['gates'].values())
        with pytest.raises(ViewerFailure):
            ex.approve_export(editing, w['id'], {})
        source.write_bytes(original+b'changed')
        with pytest.raises(ViewerFailure, match='changed'):
            editing.current(w)
        assert not list(ex.EXPORT_ROOT.glob('*.musicxml')) and working.read_bytes() == XML
    finally:
        editing.close(); runtime.close()


@pytest.mark.parametrize('case', ['pitch', 'rest', 'duration', 'onset', 'measure_missing', 'measure_number',
                                'part_name', 'part_id', 'extra_part', 'staff', 'grace', 'tie', 'note_type',
                                'duplicate_duration', 'duplicate_step', 'duplicate_attributes'])
def test_musical_mutations_refused(recovered, case):
    source, settings, _, _ = recovered; root = ET.fromstring(XML)
    notes = root.findall('.//note'); part = root.find('part')
    if case == 'pitch': notes[0].find('pitch/step').text = 'D'
    if case == 'rest': notes[0].remove(notes[0].find('pitch')); ET.SubElement(notes[0], 'rest')
    if case == 'duration': notes[0].find('duration').text = '2'
    if case == 'onset': part[0].remove(notes[0]); part[0].append(notes[0])
    if case == 'measure_missing': part.remove(part[-1])
    if case == 'measure_number': part[0].set('number', '7')
    if case == 'part_name': root.find('part-list/score-part/part-name').text = 'Flute'
    if case == 'part_id': part.set('id', 'wrong')
    if case == 'extra_part': root.append(copy.deepcopy(part))
    if case == 'staff': ET.SubElement(notes[0], 'staff').text = '2'
    if case == 'grace': ET.SubElement(notes[0], 'grace'); notes[0].remove(notes[0].find('duration'))
    if case == 'tie': ET.SubElement(notes[0], 'tie', type='start')
    if case == 'note_type': notes[0].find('type').text = 'half'
    if case == 'duplicate_duration': notes[0].append(copy.deepcopy(notes[0].find('duration')))
    if case == 'duplicate_step': notes[0].find('pitch').append(copy.deepcopy(notes[0].find('pitch/step')))
    if case == 'duplicate_attributes': part[0].append(copy.deepcopy(part[0].find('attributes')))
    settings['output'] = ET.tostring(root)
    with pytest.raises(ComposerLintError):
        imp.import_score(str(source))


@pytest.mark.parametrize('case', ['voice', 'tuplet', 'unknown_timing', 'missing_tpc', 'missing_part',
    'multistaff', 'duplicate_field', 'hidden', 'ambiguous_root', 'bad_summary', 'unmatched_tie'])
def test_unsupported_or_incomplete_source_refused(recovered, case):
    source, _, _, _ = recovered; root = ET.fromstring(NATIVE)
    staff = root.find('Score/Staff'); voice = staff.find('Measure/voice'); note = voice.find('Chord/Note')
    if case == 'voice': staff[0].append(copy.deepcopy(voice))
    if case == 'tuplet': ET.SubElement(voice.find('Chord'), 'Tuplet').text = '1'
    if case == 'unknown_timing': ET.SubElement(voice, 'location')
    if case == 'missing_tpc': note.remove(note.find('tpc'))
    if case == 'missing_part': root.find('Score').remove(root.find('Score/Part'))
    if case == 'multistaff': root.find('Score/Part').append(copy.deepcopy(root.find('Score/Part/Staff')))
    if case == 'duplicate_field': note.append(copy.deepcopy(note.find('pitch')))
    if case == 'hidden': ET.SubElement(note, 'visible').text = '0'
    if case == 'bad_summary': ET.SubElement(staff[-1], 'multiMeasureRest').text = '2'
    if case == 'unmatched_tie': ET.SubElement(note, 'Spanner', type='Tie')
    native(source, ET.tostring(root))
    if case == 'ambiguous_root':
        with zipfile.ZipFile(source, 'a') as z:
            z.writestr('other.mscx', NATIVE)
    with pytest.raises(ComposerLintError):
        imp.import_score(str(source))


@pytest.mark.parametrize('case', ['wrong_job', 'ambiguous', 'not_finished'])
def test_shutdown_evidence_must_bind_exact_job(tmp_path, monkeypatch, case):
    monkeypatch.setattr(imp, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(imp.Path, 'home', classmethod(lambda cls: tmp_path))
    folder = tmp_path/'Library/Application Support/MuseScore/MuseScore4/logs'; folder.mkdir(parents=True)
    text = ('Started MuseScore Studio 4.7.3, build: test\n'
            f'ConverterController::convertFile | in: {tmp_path}/input.mscz, out: {tmp_path}/out.musicxml\n'
            'ConsoleApp::finish | activeThreadCount: 0\nGoodbye!! code: 0\n')
    if case == 'wrong_job': text = text.replace('input.mscz', 'other.mscz')
    if case == 'not_finished': text = text.replace('Goodbye!! code: 0', 'Goodbye!! code: 1')
    (folder/'MuseScore_2.log').write_text(text)
    if case == 'ambiguous': (folder/'MuseScore_1.log').write_text(text)
    with pytest.raises(ComposerLintError):
        imp.shutdown_log(tmp_path/'input.mscz', tmp_path/'out.musicxml', 'MuseScore4 4.7.3', imp.MuseScoreFailure(-6, DIAGNOSTIC))


@pytest.mark.skipif(not os.environ.get('COMPOSERLINT_TEST_MSCZ_PATH'), reason='opt-in local personal score; never bundled')
def test_local_real_score_conversion_and_analysis():
    source = Path(os.environ['COMPOSERLINT_TEST_MSCZ_PATH']); original = source.read_bytes()
    result = imp.import_score(str(source))
    try:
        report = service.analyze_score(result.working_path)
        assert report.score.source_sha256 == result.musicxml_sha256 and source.read_bytes() == original
        if result.recovery:
            assert result.recovery.measures == sum(len(p.measures) for p in report.score.parts)
        print('Real score import:', result.conversion_status, 'parts:', len(report.score.parts),
              'measures:', sum(len(p.measures) for p in report.score.parts), 'receipt:', result.receipt_path)
    finally:
        shutil.rmtree(Path(result.working_path).parent)
