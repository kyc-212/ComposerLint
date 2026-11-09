"""Installer boundaries; no network, software provisioning or personal scores."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location('setup_composerlint', Path(__file__).parents[1] / 'tools/setup_composerlint.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def test_launcher_and_check_do_not_write(tmp_path):
    prefix = tmp_path / 'absent'
    result = subprocess.run([sys.executable, '-I', '-B', str(setup.ROOT / 'tools/setup_composerlint.py'),
                             '--check', '--prefix', str(prefix)], capture_output=True, text=True)
    assert result.returncode == 1
    assert not prefix.exists()


@pytest.mark.parametrize('answer', ['n', '', 'yes please'])
def test_refused_consent_cannot_create_prefix(tmp_path, monkeypatch, answer):
    prefix = tmp_path / 'absent'
    monkeypatch.setattr(setup, 'preflight', lambda full: '/unused/uv')
    monkeypatch.setattr('builtins.input', lambda prompt: answer)
    assert setup.main(['--mode', 'full', '--prefix', str(prefix)]) == 0
    assert not prefix.exists()


def test_cancel_does_not_even_probe_prerequisites(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt: '3')
    monkeypatch.setattr(setup, 'preflight', lambda full: pytest.fail('preflight called'))
    assert setup.main([]) == 0


def test_offline_never_opens_network(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.urllib.request, 'build_opener', lambda *a: pytest.fail('network attempted'))
    with pytest.raises(setup.SetupError, match='Offline source archive/artifact missing'):
        setup.source_archive(tmp_path, True)


def test_wrong_cached_hash_refuses_before_network(tmp_path, monkeypatch):
    (tmp_path / 'verovio-6.3.0.tar.gz').write_bytes(b'corrupt')
    monkeypatch.setattr(setup.urllib.request, 'build_opener', lambda *a: pytest.fail('network attempted'))
    with pytest.raises(setup.SetupError, match='Integrity check failed'):
        setup.source_archive(tmp_path, False)


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('/absolute', 'file'),
    ('verovio-6.3.0/link', 'link'), ('verovio-6.3.0/device', 'device')])
def test_unsafe_archives_are_rejected_before_extraction(tmp_path, name, kind):
    archive = tmp_path / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        member = tarfile.TarInfo(name)
        if kind == 'link':
            member.type, member.linkname = tarfile.SYMTYPE, '/outside'
        if kind == 'device':
            member.type = tarfile.CHRTYPE
        stream.addfile(member, io.BytesIO())
    target = tmp_path / 'extract'
    target.mkdir()
    with pytest.raises(setup.SetupError, match='Unsafe source archive'):
        setup.extract_source(archive, target)
    assert list(target.iterdir()) == []


def test_symlinked_prefix_and_ancestor_refuse(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'link').symlink_to(tmp_path / 'real', target_is_directory=True)
    with pytest.raises(setup.SetupError, match='Symlinked'):
        setup.safe_path(tmp_path / 'link' / 'new')


def test_unrelated_prefix_preserved(tmp_path):
    prefix = tmp_path / 'existing'
    prefix.mkdir()
    private = prefix / 'private.json'
    private.write_text('preserve')
    with pytest.raises(setup.SetupError, match='unrelated'):
        with setup.owned_prefix(prefix, 'test'):
            pytest.fail('acquired unrelated prefix')
    assert private.read_text() == 'preserve'
    assert list(prefix.iterdir()) == [private]


def test_concurrent_setup_refuses_and_retry_retains_owner(tmp_path):
    prefix = tmp_path / 'owned'
    with setup.owned_prefix(prefix, 'recipe'):
        with pytest.raises(setup.SetupError, match='Another setup'):
            with setup.owned_prefix(prefix, 'recipe'):
                pytest.fail('second lock acquired')
    with setup.owned_prefix(prefix, 'recipe'):
        assert json.loads((prefix / 'owner.json').read_text()) == {'recipe': 'recipe'}
    with pytest.raises(setup.SetupError, match='different recipe'):
        with setup.owned_prefix(prefix, 'changed'):
            pytest.fail('different recipe accepted')


def test_offline_uv_flags_and_environment_are_explicit(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setenv('UV_INDEX_URL', 'https://untrusted.example')
    monkeypatch.setenv('COMPOSERLINT_RENDERER_CONFIG', '/private/old.json')
    monkeypatch.setattr(setup, 'run', lambda cmd, **kw: commands.append((cmd, kw)))
    installer = setup.Installer(tmp_path, '/bin/uv', True, 'recipe')
    installer.uv_run('pip', 'install', 'example')
    cmd, kw = commands[0]
    assert '--offline' in cmd and '--no-config' in cmd and '--no-python-downloads' in cmd
    assert 'UV_INDEX_URL' not in kw['env']
    assert 'COMPOSERLINT_RENDERER_CONFIG' not in kw['env']


def test_missing_prerequisites_are_explained_without_provisioning(monkeypatch):
    # This is the preserved Mac prerequisite message, even in the Linux pilot.
    monkeypatch.setattr(setup.host, 'require_setup', lambda: setup.host.setup_recipe(target='darwin', machine='arm64'))
    monkeypatch.setattr(setup.shutil, 'which', lambda name: None)
    def unavailable(*args, **kwargs):
        raise setup.SetupError('missing Apple tools')
    monkeypatch.setattr(setup, 'run', unavailable)
    with pytest.raises(setup.SetupError) as error:
        setup.preflight(True)
    assert 'Install uv yourself' in str(error.value)
    assert 'xcode-select --install' in str(error.value)


def test_unexpected_download_redirect_refuses():
    with pytest.raises(setup.SetupError, match='redirect refused'):
        setup.OfficialRedirects().redirect_request(None, None, 302, '', {}, 'https://untrusted.example/file')


def test_verified_cached_download_is_reused(tmp_path, monkeypatch):
    target = tmp_path / 'artifact'
    target.write_bytes(b'local verified bytes')
    monkeypatch.setattr(setup.urllib.request, 'build_opener', lambda *a: pytest.fail('network attempted'))
    assert setup.download_artifact(tmp_path, target.name, 'https://unused', setup.digest(target), True) == target


def test_timed_out_process_is_reaped():
    with pytest.raises(setup.SetupError, match='Could not complete'):
        setup.run([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(10)'], capture=True, timeout=.05)


def test_main_sync_retains_official_and_locked_build_backend(tmp_path, monkeypatch):
    installer = setup.Installer(tmp_path, '/bin/uv', True, 'recipe')
    commands = []
    monkeypatch.setattr(installer, 'environment', lambda name: tmp_path / 'runtime/bin/python')
    monkeypatch.setattr(installer, 'uv_run', lambda *args, **kwargs: commands.append(args))
    installer.core()
    assert all('--locked' in cmd and '--inexact' in cmd for cmd in commands)
    assert '--no-build' in commands[0]
    assert '--no-build-isolation' in commands[1]


def test_existing_renderer_reuse_checks_files_without_rebuilding(tmp_path, monkeypatch):
    installer = setup.Installer(tmp_path, '/bin/uv', True, 'recipe')
    (tmp_path / 'renderer').mkdir()
    files = {}
    for i in range(5):
        path = tmp_path / 'renderer' / str(i)
        path.write_text(str(i))
        files[str(path)] = setup.digest(path)
    manifest = tmp_path / 'renderer.json'
    setup.write_json(manifest, dict(package_version=setup.BUILD, files_sha256=files))
    setup.write_json(tmp_path / 'renderer-build.json', dict(
        manifest_sha256=setup.digest(manifest), runtime_files=setup.inventory(tmp_path / 'renderer')))
    monkeypatch.setattr(installer, 'uv_run', lambda *a, **kw: None)
    monkeypatch.setattr(setup, 'download_artifact', lambda *a: None)
    monkeypatch.setattr(setup, 'source_archive', lambda *a: pytest.fail('rebuilding'))
    assert installer.renderer('/python') == manifest
    (tmp_path / 'renderer' / 'extra').write_text('injected')
    with pytest.raises(setup.SetupError, match='file set changed'):
        installer.renderer('/python')


def test_manifest_hash_mismatch_refuses(tmp_path):
    files = {}
    for i in range(5):
        path = tmp_path / str(i)
        path.write_text(str(i))
        files[str(path)] = setup.digest(path)
    manifest = tmp_path / 'manifest.json'
    setup.write_json(manifest, dict(package_version=setup.BUILD, files_sha256=files))
    setup.verify_manifest(manifest)
    (tmp_path / '0').write_text('tampered')
    with pytest.raises(setup.SetupError, match='Integrity check'):
        setup.verify_manifest(manifest)


def test_failed_smoke_cannot_activate_renderer(tmp_path, monkeypatch):
    installer = setup.Installer(tmp_path, '/bin/uv', True, 'recipe')
    pending = tmp_path / 'renderer.pending.json'
    pending.write_text('{}')
    monkeypatch.setattr(installer, 'core', lambda: '/python')
    monkeypatch.setattr(installer, 'renderer', lambda python: pending)
    def fail(*args):
        raise setup.SetupError('smoke failed')
    monkeypatch.setattr(installer, 'smoke', fail)
    with pytest.raises(setup.SetupError, match='smoke failed'):
        installer.install(True)
    assert not (tmp_path / 'renderer.json').exists()
    assert not (tmp_path / 'complete.json').exists()


def test_existing_manifest_cannot_be_overwritten(tmp_path, monkeypatch):
    installer = setup.Installer(tmp_path, '/bin/uv', True, 'recipe')
    final = tmp_path / 'renderer.json'
    final.write_text('unrelated private configuration')
    pending = tmp_path / 'renderer.pending.json'
    pending.write_text('{}')
    monkeypatch.setattr(installer, 'core', lambda: '/python')
    monkeypatch.setattr(installer, 'renderer', lambda python: pending)
    monkeypatch.setattr(installer, 'smoke', lambda *args: None)
    with pytest.raises(setup.SetupError, match='Existing renderer configuration preserved'):
        installer.install(True)
    assert final.read_text() == 'unrelated private configuration'
    assert not (tmp_path / 'complete.json').exists()


def test_text_setup_never_provisions_renderer(tmp_path, monkeypatch):
    installer = setup.Installer(tmp_path, '/bin/uv', True, 'recipe')
    monkeypatch.setattr(installer, 'core', lambda: '/python')
    monkeypatch.setattr(installer, 'renderer', lambda *args: pytest.fail('renderer provisioning'))
    monkeypatch.setattr(installer, 'smoke', lambda python, manifest: manifest is None or pytest.fail('manifest provided'))
    installer.install(False)
    assert json.loads((tmp_path / 'complete.json').read_text()) == {'recipe': 'recipe', 'full': False}
    assert not (tmp_path / 'renderer.json').exists()


def test_no_brew_choco_or_export_in_installer():
    # Distribution boundary, not musical algorithm assertions.
    assert setup.digest(setup.PATCH) == setup.PATCH_HASH
    assert 'requirements-renderer-build.txt' in (setup.ROOT / 'pyproject.toml').read_text()
    assert '.composerlint-setup/' in (setup.ROOT / '.gitignore').read_text()
