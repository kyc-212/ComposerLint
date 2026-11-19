"""Prepare and verify the local Windows Full setup ZIP and separate renderer ZIP."""

import argparse
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_windows_renderer import ROOT, recipe

SETUP_ZIP = 'ComposerLint-Setup-Windows-x64-Full.zip'
RENDERER_ZIP = 'ComposerLint-Renderer-Windows-x64.zip'
LAUNCHER = 'windows/terminal-package/Setup.ps1'
GUIDE = 'windows/terminal-package/START-HERE.md'
RENDERER_RELEASE_NOTES = 'windows/RENDERER-RELEASE.md'
STANDALONE_PATHS = ('tools/windows_renderer_native/prove.py', 'tools/windows_renderer_native/README.md',
                    'tests/fixtures/v02_m2/grandstaff.musicxml')
SOURCE_PATHS = (
    'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml', 'uv.lock',
    'requirements-renderer-build.txt', 'requirements-viewer.txt',
    'LICENSES', 'src/composerlint', 'skills', 'patches',
    'docs/INSTALL.md', 'docs/V0.2-M5-release-checklist.md',
    'docs/licensing/renderer-redistribution.md', 'docs/licensing/renderer-artifact-audit.json',
    'docs/licensing/source-candidate-review.md', 'docs/licensing/public-source-manifest.json',
    'docs/licensing/windows-renderer-audit.md', 'docs/V0.2-cross-platform-foundation.md',
    'macos/README.md', LAUNCHER, GUIDE,
    'tools/setup_composerlint.py', 'tools/ci_windows_mcp.py',
    'tools/preflight_windows.py', 'tools/windows-setup-assets.json',
    'tools/windows-renderer-assets.json', 'tools/build_windows_renderer.py',
    'tests/test_renderer_packaging_equivalence.py', 'tests/test_v02_renderer_ties.py',
    'tests/fixtures/basic.musicxml', 'tests/fixtures/m3/cadence_authentic_end.musicxml',
    'tests/fixtures/v02_m2/multipage.musicxml',
)
DOCUMENTS = ('README.md', 'docs/INSTALL.md', 'START-HERE.md')
SUFFIXES = {'.py', '.ps1', '.md', '.txt', '.toml', '.lock', '.json', '.js',
            '.html', '.css', '.musicxml', '.patch', '.rst', '.apache', '.bsd'}


def safe_name(name):
    if len(name) > 200 or any(
            not re.fullmatch(r'[A-Za-z0-9_+.-]+', part) or part.endswith('.')
            or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', part.split('.')[0])
            for part in name.split('/')):
        raise ValueError(f'Unsafe Windows ZIP name: {name}')


def inventory(entries):
    return ''.join(f'{recipe.sha(data)} *{name}\n' for name, data in sorted(entries.items())).encode('ascii')


def source_entries(commit, *, standalone=False):
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Expected a full reviewed commit SHA.')
    git = ['git', '-C', str(ROOT)]
    # Historical checkpoint commits predate the renderer release notes.
    release_notes = subprocess.check_output([*git, 'ls-tree', '--name-only',
                                             commit, '--', RENDERER_RELEASE_NOTES])
    paths = (*SOURCE_PATHS, RENDERER_RELEASE_NOTES) if release_notes else SOURCE_PATHS
    if standalone:
        paths += STANDALONE_PATHS
    archived = subprocess.check_output([*git, 'archive', '--format=tar', commit, '--', *paths])
    entries = {}
    with tarfile.open(fileobj=BytesIO(archived), mode='r:') as source:
        for member in source:
            if member.isdir():
                continue
            name = member.name
            safe_name(name)
            basename = PurePosixPath(name).name
            if (not member.isfile() or name in entries or member.size > 4 * 1024**2
                    or not (PurePosixPath(name).suffix.lower() in SUFFIXES
                            or basename.startswith(('LICENSE', 'COPYING'))
                            or basename in ('AUTHORS', 'NOTICE'))):
                raise ValueError(f'Unexpected Windows setup source member: {name}')
            with source.extractfile(member) as stream:
                data = stream.read()
            if b'\x00' in data or data.startswith((b'MZ', b'PK\x03\x04', b'\x7fELF')):
                raise ValueError(f'Binary member in the source-only Windows setup ZIP: {name}')
            data.decode('utf-8')
            entries[name] = data
    entries['Setup.ps1'] = entries.pop(LAUNCHER)
    entries['START-HERE.md'] = entries.pop(GUIDE)
    return entries


def setup_entries(manifest_bytes):
    manifest = recipe.full_manifest(manifest_bytes)
    entries = source_entries(manifest['ci']['commit'])
    entries[recipe.FULL_MANIFEST] = manifest_bytes
    return add_package_inventory(entries)


def standalone_setup_entries(commit):
    entries = source_entries(commit, standalone=True)
    if not {'src/composerlint/windows_renderer_native.py', *STANDALONE_PATHS} <= entries.keys():
        raise ValueError('The reviewed commit does not contain standalone Windows setup.')
    return add_package_inventory(entries)


def add_package_inventory(entries):
    for name in DOCUMENTS:
        for link in re.findall(r'\]\(([^\s)]+)\)', entries[name].decode('utf-8')):
            if '://' in link or link.startswith('#'):
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link.split('#')[0]))
            if target not in entries:
                raise ValueError(f'Missing offline link in {name}: {link}')
    entries['FILES.sha256'] = inventory(entries)
    return entries


def checked_zip(entries):
    for name in entries:
        safe_name(name)
    if len(entries) != len({name.casefold() for name in entries}):
        raise ValueError('Case-colliding Windows ZIP names.')
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        for name, content in sorted(entries.items()):
            info = ZipInfo(name, (2026, 11, 15, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=6)
    with ZipFile(data) as archive:
        if archive.namelist() != sorted(entries) or archive.testzip() is not None:
            raise ValueError('Invalid ZIP inventory or CRC.')
        for info in archive.infolist():
            if (not stat.S_ISREG(info.external_attr >> 16) or info.flag_bits or info.extra
                    or archive.read(info) != entries[info.filename]):
                raise ValueError(f'ZIP member failed byte or metadata verification: {info.filename}')
    return data.getvalue()


def read_local_file(path, limit):
    if sys.platform == 'win32':
        from build_windows_renderer import setup

        return setup._read_source(path.absolute(), limit)

    def identity(info):
        return (info.st_mode, info.st_dev, info.st_ino, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
        raise ValueError(f'Expected one bounded, regular file: {path.name}')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise ValueError(f'File changed before reading: {path.name}')
        data = stream.read(limit + 1)
        if (len(data) != before.st_size or identity(os.fstat(stream.fileno())) != identity(before)
                or identity(path.lstat()) != identity(before)):
            raise ValueError(f'File changed while reading: {path.name}')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    route = parser.add_mutually_exclusive_group(required=True)
    route.add_argument('--manifest', type=Path)
    route.add_argument('--standalone', action='store_true')
    parser.add_argument('--reviewed-manifest-sha256')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--commit', help='Reviewed commit for the standalone setup package')
    parser.add_argument('--output', type=Path, required=True, help='New private local directory')
    args = parser.parse_args()
    if args.standalone:
        if not args.commit or args.bundle or args.reviewed_manifest_sha256:
            parser.error('Standalone packaging needs only --commit and --output.')
        payloads = {SETUP_ZIP: checked_zip(standalone_setup_entries(args.commit))}
    else:
        if not args.bundle or not args.reviewed_manifest_sha256 or args.commit:
            parser.error('Legacy packaging requires --manifest, --reviewed-manifest-sha256 and --bundle.')
        manifest_bytes = read_local_file(args.manifest, recipe.MANIFEST_LIMIT)
        if recipe.sha(manifest_bytes) != args.reviewed_manifest_sha256:
            raise ValueError('Manifest bytes differ from the independently reviewed digest.')
        manifest = recipe.full_manifest(manifest_bytes)
        if args.bundle.is_symlink() or {p.name for p in args.bundle.iterdir()} != recipe.BUNDLE_LIMITS.keys():
            raise ValueError('Expected exactly the five Windows renderer artifacts.')
        renderer = recipe.verify_full_artifacts(
            manifest, lambda name, limit: read_local_file(args.bundle / name, limit))
        payloads = {SETUP_ZIP: checked_zip(setup_entries(manifest_bytes)),
                    RENDERER_ZIP: checked_zip(renderer)}
    args.output.mkdir(mode=0o700)
    for name, data in payloads.items():
        with (args.output / name).open('xb') as stream:
            stream.write(data)
        with (args.output / (name + '.sha256')).open('x', encoding='ascii') as stream:
            stream.write(f'{recipe.sha(data)} *{name}\n')
        print(f'SHA-256 {recipe.sha(data)}  {name}')


if __name__ == '__main__':
    main()
