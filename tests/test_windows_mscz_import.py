"""Windows import contract with simulated NTFS and external converter boundaries."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from composerlint import score_import, windows_musescore, windows_score_import


FIXTURE = Path(__file__).parent / 'fixtures' / 'basic.musicxml'


@pytest.fixture
def windows_import(tmp_path, monkeypatch):
    source = tmp_path / 'original.mscz'
    with ZipFile(source, 'w') as archive:
        archive.writestr('original.mscx', b'<museScore/>')
    initial = source.read_bytes()
    workspaces = []

    @contextmanager
    def private_workspace(parent, prefix):
        directory = tmp_path / f'{prefix}{len(workspaces)}'
        directory.mkdir()
        workspaces.append(directory)
        stage = SimpleNamespace(
            path=directory, audit=lambda: None,
            create=lambda parts: directory.joinpath(*parts).open('xb'),
            held_file=lambda parts: directory.joinpath(*parts).open('rb'),
        )
        try:
            yield stage
        finally:
            shutil.rmtree(directory)

    def read_stable(path, *, limit):
        assert not path.is_symlink()
        content = path.read_bytes()
        if len(content) > limit:
            raise ValueError('oversized')
        return content, None

    class CleanupUnconfirmed(RuntimeError):
        pass

    monkeypatch.setattr(score_import, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setitem(sys.modules, 'composerlint.windows_install_storage', SimpleNamespace(private_workspace=private_workspace))
    monkeypatch.setitem(sys.modules, 'composerlint.windows_private', SimpleNamespace(read_stable=read_stable))
    monkeypatch.setitem(sys.modules, 'composerlint.windows_workers', SimpleNamespace(SetupProcessCleanupUnconfirmed=CleanupUnconfirmed))
    monkeypatch.setattr(windows_musescore, 'find_musescore', lambda: windows_musescore.MuseScore(
        Path(r'C:\Program Files\MuseScore 4\bin\MuseScore4.exe'), '4.7.5.0'))
    with ExitStack() as imports:
        monkeypatch.setattr(windows_score_import, '_imports', imports)
        yield SimpleNamespace(source=source, initial=initial, workspaces=workspaces, cleanup_error=CleanupUnconfirmed)


def test_windows_mscz_import_preserves_source_and_receipt(windows_import, monkeypatch):
    case = windows_import

    def convert(converter, copied, output, stage):
        assert copied != case.source and copied.read_bytes() == case.initial
        assert stage.path == copied.parent == output.parent
        output.write_bytes(FIXTURE.read_bytes())
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    monkeypatch.setattr(windows_musescore, 'convert', convert)
    result = score_import.import_score(str(case.source))
    working = Path(result.working_path)
    assert result.source_format == 'mscz' and result.converter_version == '4.7.5.0'
    assert result.conversion_method == 'musescore_cli_musicxml'
    assert working.read_bytes() == FIXTURE.read_bytes()
    assert case.source.read_bytes() == case.initial
    assert not case.workspaces[0].exists()
    assert {p.name for p in working.parent.iterdir()} == {'score.musicxml', 'import.json'}
    score_import.verify_import(working)
    case.source.write_bytes(case.initial + b'changed')
    with pytest.raises(score_import.ComposerLintError, match='changed'):
        score_import.verify_import(working)


@pytest.mark.parametrize(('outcome', 'reason'), [
    ('missing', 'MuseScore Studio 4 is required'),
    ('timeout', 'timed out'),
    ('exit', 'exited with status 1'),
    ('no-output', 'missing'),
    ('malformed', 'MusicXML'),
    ('cleanup', 'cleanup could not be confirmed'),
])
def test_windows_mscz_failures_never_admit_a_working_score(windows_import, monkeypatch, outcome, reason):
    case = windows_import
    if outcome == 'missing':
        monkeypatch.setattr(windows_musescore, 'find_musescore', lambda: None)

    def convert(converter, copied, output, stage):
        if outcome == 'timeout':
            raise TimeoutError()
        if outcome == 'cleanup':
            raise case.cleanup_error()
        if outcome == 'exit':
            output.write_bytes(FIXTURE.read_bytes())
        elif outcome == 'malformed':
            output.write_bytes(b'<wrong/>')
        return SimpleNamespace(returncode=int(outcome == 'exit'), stdout=b'', stderr=b'')

    monkeypatch.setattr(windows_musescore, 'convert', convert)
    with pytest.raises(score_import.ComposerLintError, match=reason):
        score_import.import_score(str(case.source))
    assert case.source.read_bytes() == case.initial
    assert all(not path.exists() for path in case.workspaces)


def test_windows_mxl_still_imports_without_a_converter(windows_import, monkeypatch):
    monkeypatch.setattr(windows_musescore, 'find_musescore', lambda: pytest.fail('MXL requires no converter'))
    source = windows_import.source.with_suffix('.mxl')
    with ZipFile(source, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>')
        archive.writestr('score.musicxml', FIXTURE.read_bytes())
    result = score_import.import_score(str(source))
    assert result.source_format == 'mxl'
    score_import.verify_import(result.working_path)


def test_find_musescore_discovery_and_explicit_path(tmp_path, monkeypatch):
    def installed(folder):
        path = folder / 'bin' / 'MuseScore4.exe'
        path.parent.mkdir(parents=True)
        path.touch()
        return path

    program_files = tmp_path / 'Program Files'
    standard = installed(program_files / 'MuseScore 4')
    custom = installed(tmp_path / 'Custom Studio')
    monkeypatch.delenv('COMPOSERLINT_MUSESCORE', raising=False)
    monkeypatch.delenv('ProgramW6432', raising=False)
    monkeypatch.setenv('ProgramFiles', str(program_files))
    monkeypatch.setattr(windows_musescore, 'local_path_text', lambda path, **kwargs: path)
    monkeypatch.setattr(windows_musescore, 'file_version', lambda path: '4.7.5.0' if path == standard else '4.6.5.0')
    assert windows_musescore.find_musescore().executable == standard
    monkeypatch.setenv('COMPOSERLINT_MUSESCORE', str(custom))
    assert windows_musescore.find_musescore().version == '4.6.5.0'
    monkeypatch.setenv('COMPOSERLINT_MUSESCORE', str(tmp_path / 'missing.exe'))
    with pytest.raises(ValueError, match='COMPOSERLINT_MUSESCORE'):
        windows_musescore.find_musescore()
