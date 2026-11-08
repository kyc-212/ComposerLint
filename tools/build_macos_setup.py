#!/usr/bin/env python3
"""Build an ARM64 setup app and ZIP (optional DMG); never publish implicitly."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def run(*args, cwd=ROOT):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)

def inventory(root):
    """Bind bytes, symlinks and executable permissions across ZIP extraction."""
    entries = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries[name] = ('link', str(path.readlink()))
        elif path.is_file():
            entries[name] = ('file', hashlib.sha256(path.read_bytes()).hexdigest(),
                             stat.S_IMODE(path.stat().st_mode))
        elif path.is_dir():
            entries[name] = ('directory', stat.S_IMODE(path.stat().st_mode))
        else:
            raise ValueError(f'Unexpected bundle entry: {name}')
    return entries

def package_zip(app, destination):
    if destination.exists():
        raise ValueError(f'Refusing to replace existing archive: {destination}')
    run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, destination)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC integrity check failed')
        for entry in archive.infolist():
            parts = Path(entry.filename).parts
            if not parts or parts[0] not in (app.name, '__MACOSX') or '..' in parts:
                raise ValueError(f'Unexpected ZIP path: {entry.filename}')
    with tempfile.TemporaryDirectory(prefix='setup-zip-check-', dir=destination.parent) as directory:
        run('/usr/bin/ditto', '-x', '-k', destination, directory)
        if inventory(app) != inventory(Path(directory) / app.name):
            raise ValueError('Extracted bundle differs in bytes, links or permissions')
        run('/usr/bin/codesign', '--verify', '--strict', Path(directory) / app.name)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n')
    return digest

def build(output, identity=None, make_dmg=False):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = output / 'ComposerLint Setup.app'
    if app.exists():
        raise SystemExit('Output app already exists; choose a fresh --output directory.')
    contents = app / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    resources = contents / 'Resources'
    resources.mkdir()
    # Hatch's reviewed source allowlist excludes all local renderer/private state.
    with tempfile.TemporaryDirectory(prefix='setup-package-', dir=output) as directory:
        directory = Path(directory)
        run(sys.executable, '-I', '-B', '-m', 'hatchling', 'build', '-t', 'sdist', '-d', directory)
        archive = next(directory.glob('*.tar.gz'))
        with tarfile.open(archive) as stream:
            stream.extractall(directory / 'extracted', filter='data')
        source = next((directory / 'extracted').iterdir())
        shutil.move(source, resources / 'Source')
    source = resources / 'Source'
    for name in ('composer-import', 'composer-review', 'composer-edit'):
        link = source / '.agents/skills' / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to('../../skills/' + name)
    entries = {}
    for path in sorted(source.rglob('*')):
        name = str(path.relative_to(source))
        if path.is_symlink():
            entries[name] = {'link': str(path.readlink())}
        elif path.is_file():
            entries[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = json.dumps(entries, sort_keys=True, indent=2) + '\n'
    (resources / 'source-manifest.json').write_text(manifest)
    shutil.copy2(ROOT / 'macos/bootstrap-pins.json', resources / 'bootstrap-pins.json')
    run('xcrun', 'swiftc', '-module-cache-path', output / 'module-cache',
        ROOT / 'macos/MakeIcon.swift', '-o', output / 'make-icon')
    with tempfile.TemporaryDirectory(prefix='setup-icon-', dir=output) as directory:
        iconset = Path(directory) / 'ComposerLint.iconset'
        run(output / 'make-icon', iconset)
        run('/usr/bin/iconutil', '-c', 'icns', '-o', resources / 'ComposerLint.icns', iconset)
    info = dict(CFBundleIdentifier='org.composerlint.setup', CFBundleName='ComposerLint Setup',
                CFBundleDisplayName='ComposerLint Setup', CFBundleExecutable='ComposerLintSetup',
                CFBundleIconFile='ComposerLint.icns',
                CFBundlePackageType='APPL', CFBundleShortVersionString='0.2.0', CFBundleVersion='1',
                LSMinimumSystemVersion='14.0', NSHighResolutionCapable=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info, sort_keys=True))
    run('xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library', '-O',
        '-target', 'arm64-apple-macosx14.0', '-module-cache-path', output / 'module-cache',
        ROOT / 'macos/ComposerLintSetup.swift', '-o', contents / 'MacOS/ComposerLintSetup')
    if identity:
        run('codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity, app)
    else:
        run('codesign', '--force', '--options', 'runtime', '--sign', '-', app)
    run('codesign', '--verify', '--strict', '--verbose=2', app)
    archive = output / 'ComposerLint-Setup-macOS-arm64.zip'
    digest = package_zip(app, archive)
    result = dict(app=str(app), zip=str(archive), zip_sha256=digest,
                  source_manifest_sha256=hashlib.sha256(manifest.encode()).hexdigest(),
                  signing='Developer ID requested' if identity else 'ad hoc; no verified developer identity',
                  notarized=False, publication_approved=False)
    if make_dmg:
        dmg = output / 'ComposerLint-Setup-arm64.dmg'
        with tempfile.TemporaryDirectory(prefix='setup-dmg-', dir=output) as directory:
            shutil.copytree(app, Path(directory) / app.name, symlinks=True)
            (Path(directory) / 'Read Me.txt').write_text(
                'Open ComposerLint Setup.app. It installs for your user after explicit consent.\n'
                'Apple tool licences/security prompts require your action. No Terminal commands needed.\n'
                + ('Developer ID signing requested; notarization must be completed separately.\n' if identity else
                   'LOCAL DEVELOPMENT BUILD: ad hoc signed, NOT Developer ID signed or notarized.\nDo not publish as a trusted public installer.\n'))
            run('hdiutil', 'create', '-fs', 'HFS+', '-format', 'UDZO', '-volname', 'ComposerLint Setup', '-srcfolder', directory, dmg)
        result.update(dmg=str(dmg), dmg_sha256=hashlib.sha256(dmg.read_bytes()).hexdigest())
    (output / 'build.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--identity', help='Explicit Developer ID Application identity; default is ad hoc signing without Developer ID')
    parser.add_argument('--dmg', action='store_true', help='Also build the retained optional DMG format')
    args = parser.parse_args()
    build(args.output, args.identity, args.dmg)
