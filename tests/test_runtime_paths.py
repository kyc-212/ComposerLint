"""Path-policy tests are simulations, not target-platform acceptance."""
from pathlib import Path
import json
import os
import pytest
from composerlint import runtime_paths as paths
from composerlint.renderers import verovio
from composerlint.viewer_source import ViewerFailure


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith('COMPOSERLINT_') or key.startswith('XDG_') or key == 'LOCALAPPDATA':
            monkeypatch.delenv(key)
    monkeypatch.setattr(paths.Path, 'home', lambda: tmp_path)


@pytest.mark.parametrize('platform,config,data', [
    ('darwin', 'Library/Application Support/ComposerLint', 'Library/Application Support/ComposerLint'),
    ('win32', 'AppData/Local/ComposerLint', 'AppData/Local/ComposerLint'),
    ('linux', '.config/ComposerLint', '.local/share/ComposerLint'),
])
def test_defaults_do_not_depend_on_cwd_or_installation(monkeypatch,tmp_path,platform,config,data):
    monkeypatch.setattr(paths.sys,'platform',platform)
    other=tmp_path/'unrelated';other.mkdir();monkeypatch.chdir(other)
    assert paths.config_dir()==tmp_path/config
    assert paths.data_dir()==tmp_path/data
    assert paths.export_dir()==tmp_path/data/'exports'
    assert not (tmp_path/data).exists()  # Discovery has no filesystem side effects.


@pytest.mark.parametrize('key,call', [
    ('COMPOSERLINT_CONFIG_DIR',paths.config_dir),('COMPOSERLINT_DATA_DIR',paths.data_dir),
    ('COMPOSERLINT_EXPORT_DIR',paths.export_dir),('COMPOSERLINT_RENDERER_CONFIG',paths.renderer_config),
    ('COMPOSERLINT_TEMP_DIR',paths.temporary_parent),
])
def test_relative_overrides_refuse(monkeypatch,key,call):
    monkeypatch.setenv(key,'relative/path')
    with pytest.raises(ValueError,match='absolute'):call()


def test_explicit_renderer_missing_never_falls_back(monkeypatch,tmp_path):
    monkeypatch.setenv('COMPOSERLINT_RENDERER_CONFIG',str(tmp_path/'missing.json'))
    with pytest.raises(ViewerFailure,match='verified files'):verovio.renderer_command()


def test_config_relative_pins_survive_cwd_change_and_detect_tampering(monkeypatch,tmp_path):
    import hashlib
    directory=tmp_path/'config';directory.mkdir()
    binary=directory/'renderer';binary.write_bytes(b'pinned')
    config=directory/'renderer.json'
    config.write_text(json.dumps(dict(package_version='6.3.0+composerlint.tie1',python='renderer',
        files_sha256={'renderer':hashlib.sha256(b'pinned').hexdigest()})))
    monkeypatch.setenv('COMPOSERLINT_RENDERER_CONFIG',str(config));monkeypatch.chdir(tmp_path)
    assert verovio.renderer_command()==(str(binary),'6.3.0+composerlint.tie1')
    binary.write_bytes(b'changed')
    with pytest.raises(ViewerFailure):verovio.renderer_command()


def test_temp_parent_and_private_workspace(monkeypatch,tmp_path):
    from composerlint.editing_runtime import EditingRuntime
    monkeypatch.setenv('COMPOSERLINT_TEMP_DIR',str(tmp_path))
    runtime=EditingRuntime()
    try:
        directory=runtime.directory()
        assert directory.parent==tmp_path
        assert directory.stat().st_mode & 0o777 == 0o700
    finally:runtime.close()
    assert not directory.exists()


def test_export_destination_creation_retains_no_follow(monkeypatch,tmp_path):
    from composerlint import editing_export as ex
    base=tmp_path/'data';base.mkdir()
    monkeypatch.setenv('COMPOSERLINT_DATA_DIR',str(base/'ComposerLint'))
    destination=paths.export_dir();monkeypatch.setattr(ex,'EXPORT_ROOT',destination)
    fd=ex.export_directory();os.close(fd)
    assert destination.is_dir() and destination.stat().st_mode & 0o777 == 0o700
    destination.rmdir();destination.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ViewerFailure):ex.export_directory()


def test_explicit_export_parent_must_exist(monkeypatch,tmp_path):
    from composerlint import editing_export as ex
    destination=tmp_path/'missing-parent'/'exports'
    monkeypatch.setenv('COMPOSERLINT_EXPORT_DIR',str(destination));monkeypatch.setattr(ex,'EXPORT_ROOT',destination)
    with pytest.raises(ViewerFailure):ex.export_directory()
    assert not destination.parent.exists()


def test_import_override_receipt_and_original_integrity(monkeypatch,tmp_path):
    import shutil
    from test_score_import import archive
    from composerlint.score_import import import_score, verify_import
    directory=tmp_path/'temporary scores';directory.mkdir()
    source=archive(tmp_path/'音楽.mxl');original=source.read_bytes()
    monkeypatch.setenv('COMPOSERLINT_TEMP_DIR',str(directory))
    result=import_score(str(source));working=Path(result.working_path)
    try:
        assert working.parent.parent==directory
        assert json.loads(Path(result.receipt_path).read_text(encoding='utf-8'))['original_path']==str(source)
        verify_import(working)
        assert source.read_bytes()==original
        source.write_bytes(original+b'changed')
        from composerlint.errors import ComposerLintError
        with pytest.raises(ComposerLintError):verify_import(working)
    finally:shutil.rmtree(working.parent)
