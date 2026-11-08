"""Actual macOS ZIP roundtrip; no third-party downloads or software installation."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('macos_packaging', Path(__file__).parents[1] / 'tools/build_macos_setup.py')
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS ditto archive behavior')

def test_zip_roundtrip_preserves_source_links_and_executable(tmp_path, monkeypatch):
    app = tmp_path / 'ComposerLint Setup.app'
    executable = app / 'Contents/MacOS/ComposerLintSetup'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'packaging fixture, not an executable')
    executable.chmod(0o755)
    source = app / 'Contents/Resources/Source'
    (source / 'skills/composer-review').mkdir(parents=True)
    (source / 'skills/composer-review/SKILL.md').write_text('fixture')
    (source / '.agents/skills').mkdir(parents=True)
    (source / '.agents/skills/composer-review').symlink_to('../../skills/composer-review')
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        # Signature verification is exercised on the genuine built app, not this fixture.
        if args[0] != '/usr/bin/codesign':
            subprocess.run([str(a) for a in args], check=True)
    monkeypatch.setattr(packaging, 'run', run)
    archive = tmp_path / 'ComposerLint-Setup-macOS-arm64.zip'
    digest = packaging.package_zip(app, archive)
    assert archive.with_suffix('.zip.sha256').read_text() == f'{digest}  {archive.name}\n'
    assert any(call[0] == '/usr/bin/codesign' for call in calls)
    with zipfile.ZipFile(archive) as stream:
        assert stream.testzip() is None
        entry = stream.getinfo('ComposerLint Setup.app/Contents/MacOS/ComposerLintSetup')
        assert (entry.external_attr >> 16) & 0o777 == 0o755
    with pytest.raises(ValueError, match='replace existing'):
        packaging.package_zip(app, archive)

def test_inventory_detects_permission_and_link_changes(tmp_path):
    script = tmp_path / 'install.sh'
    script.write_text('fixture')
    script.chmod(0o755)
    first = packaging.inventory(tmp_path)
    script.chmod(0o644)
    assert packaging.inventory(tmp_path) != first
    link = tmp_path / 'skill'
    link.symlink_to('review')
    before = packaging.inventory(tmp_path)
    link.unlink()
    link.symlink_to('edit')
    assert packaging.inventory(tmp_path) != before
