#!/usr/bin/env python3
"""Build the native GTK4 preview ZIP on Linux. Never install tools or publish.

Mac can exercise archive/controller tests; it cannot emit a pretend Linux app.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'ComposerLint Setup'
ARCHIVE = 'ComposerLint-Setup-Linux-x86_64.zip'


def run(args, *, capture=False):
    cp = subprocess.run([str(a) for a in args], cwd=ROOT, check=True,
                        capture_output=capture, text=True)
    return cp.stdout.strip() if capture else None


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(directory):
    entries = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linux bundles must contain real resources, not discovery symlinks.')
        if path.is_file():
            entries[path.relative_to(directory).as_posix()] = dict(
                sha256=sha(path), mode=stat.S_IMODE(path.stat().st_mode))
        elif not path.is_dir():
            raise ValueError('Special bundle file refused.')
    return entries


def verify_elf(path):
    with path.open('rb') as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:7] != b'\x7fELF\x02\x01\x01'
            or struct.unpack_from('<H', header, 16)[0] not in (2,3)
            or struct.unpack_from('<H', header, 18)[0] != 62
            or not path.stat().st_mode & 0o111):
        raise ValueError('Expected an executable Linux x86-64 ELF, not a source or Mac binary.')


def package_zip(app, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError('Existing package preserved; choose a fresh output folder.')
    before = inventory(app)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, record in before.items():
            info = zipfile.ZipInfo(app.name + '/' + name, date_time=(2026,1,1,0,0,0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | record['mode']) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (app / name).read_bytes())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip():
            raise ValueError('ZIP CRC failure.')
        seen = set()
        for entry in archive.infolist():
            name = PurePosixPath(entry.filename)
            if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                    or name.parts[0] != app.name or entry.filename in seen
                    or stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG):
                raise ValueError('Unsafe ZIP member.')
            seen.add(entry.filename)
    # Test a real independent extractor, including executable bits and spaces.
    unzip = shutil.which('unzip')
    if not unzip:
        raise ValueError('Archive verification requires an existing unzip; none was installed.')
    with tempfile.TemporaryDirectory(prefix='linux-zip-roundtrip-', dir=destination.parent) as directory:
        run([unzip, '-q', destination, '-d', directory])
        if inventory(Path(directory) / app.name) != before:
            raise ValueError('Extracted ZIP differs in bytes or executable permissions.')
    digest = sha(destination)
    destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n')
    return digest


def prepare_resources(app):
    resources = app / 'resources'
    resources.mkdir(parents=True)
    # Reuse the existing reviewed source allowlist, never copy the workspace.
    with tempfile.TemporaryDirectory(prefix='linux-source-', dir=app.parent) as directory:
        directory = Path(directory)
        run([sys.executable, '-I', '-B', '-m', 'hatchling', 'build', '-t', 'sdist', '-d', directory])
        archive = next(directory.glob('*.tar.gz'))
        with tarfile.open(archive) as stream:
            stream.extractall(directory / 'unpacked', filter='data')
        source = next((directory / 'unpacked').iterdir())
        shutil.move(source, app / 'Source')
    for name in ('composer-import','composer-review','composer-edit'):
        target = app / 'Source/.agents/skills' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app / 'Source/skills' / name, target)
        for markdown in target.rglob('*.md'):
            original = app / 'Source/skills' / name / markdown.relative_to(target)
            def rewrite(match):
                destination = match.group(2)
                if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',destination) or destination.startswith('#'):
                    return match.group(0)
                local,mark,fragment = destination.partition('#')
                resolved = (original.parent / local).resolve()
                source_root = (app/'Source').resolve()
                if not resolved.is_relative_to(source_root) or not resolved.is_file():
                    raise ValueError('A bundled Skill link is missing or escapes the source distribution.')
                skills_root = source_root/'skills'
                if resolved.is_relative_to(skills_root):
                    resolved = app/'Source/.agents/skills'/resolved.relative_to(skills_root)
                relative = Path(os.path.relpath(resolved,markdown.parent)).as_posix()
                return match.group(1)+'('+relative+(mark+fragment if mark else '')+')'
            markdown.write_text(re.sub(r'(!?\[[^\]]*\])\(([^)]+)\)',rewrite,markdown.read_text()))
    linux_source = app / 'Source/linux'
    linux_source.mkdir()
    for name in ('setup.c','setup_test.c','setup_support.py','setup.css','composerlint.svg','README.md','RELEASE-NOTES.md','THIRD-PARTY.md'):
        shutil.copyfile(ROOT / 'linux' / name, linux_source / name)
    shutil.copyfile(ROOT / 'tools/build_linux_setup.py', app / 'Source/tools/build_linux_setup.py')
    for name in ('setup_support.py','setup.css','composerlint.svg'):
        shutil.copyfile(linux_source / name, resources / name)
    readme = (linux_source / 'README.md').read_text()
    (app / 'READ ME FIRST.md').write_text(readme.replace('(RELEASE-NOTES.md)', '(Source/linux/RELEASE-NOTES.md)').replace('(THIRD-PARTY.md)', '(Source/linux/THIRD-PARTY.md)'))
    entries = {}
    for subtree in ('resources','Source'):
        for name, value in inventory(app / subtree).items():
            entries[subtree + '/' + name] = value
    manifest = resources / 'manifest.json'
    manifest.write_text(json.dumps(entries, sort_keys=True, indent=2)+'\n')
    return sha(manifest), sha(resources / 'setup_support.py')


def build(output):
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise ValueError('A native Linux x86-64 build host is required. No cross toolchain or VM was acquired.')
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        raise ValueError('Build as a normal user, without root.')
    for tool in ('pkg-config','ldd','unzip'):
        if not shutil.which(tool):
            raise ValueError(f'Missing build prerequisite {tool}; no system packages were installed.')
    spec = importlib.util.spec_from_file_location('linux_build_host',ROOT/'src/composerlint/platform_support.py')
    host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
    host.require_setup()
    capabilities = host.linux_capabilities(frontend=True)
    if not capabilities['frontend_available']:
        raise ValueError('Frontend requires the shared GNU/Linux checks and an existing compiler that compiles and links C11. No replacement was acquired.')
    compiler = capabilities['tools']['cc']
    run(['pkg-config','--atleast-version=4.12','gtk4'])
    run(['pkg-config','--atleast-version=1.6','json-glib-1.0'])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = output / NAME
    if app.exists() or app.is_symlink():
        raise ValueError('Existing app preserved; choose a fresh output folder.')
    manifest_hash, support_hash = prepare_resources(app)
    with tempfile.TemporaryDirectory(prefix='gtk-build-', dir=output) as temporary:
        temporary = Path(temporary)
        (temporary / 'bundle_identity.h').write_text(
            f'#define MANIFEST_SHA256 "{manifest_hash}"\n#define SUPPORT_SHA256 "{support_hash}"\n'
            f'#define STYLE_SHA256 "{sha(app / "resources/setup.css")}"\n'
            f'#define ICON_SHA256 "{sha(app / "resources/composerlint.svg")}"\n')
        flags = run(['pkg-config','--cflags','--libs','gtk4','json-glib-1.0'], capture=True)
        import shlex
        run([compiler['path'],'-std=c11','-O2','-Wall','-Wextra','-Werror=implicit-function-declaration',
             '-Werror=incompatible-pointer-types','-Wl,-z,relro,-z,now','-I',temporary,
             ROOT/'linux/setup.c','-o',app/NAME,*shlex.split(flags)])
    (app / NAME).chmod(0o755)
    verify_elf(app / NAME)
    dependencies = run(['ldd',app/NAME], capture=True)
    if 'not found' in dependencies:
        raise ValueError('The built frontend has unresolved runtime libraries.')
    report = dict(schema=1, machine=platform.machine(), platform=platform.platform(),
        gtk=run(['pkg-config','--modversion','gtk4'], capture=True),
        json_glib=run(['pkg-config','--modversion','json-glib-1.0'], capture=True),
        compiler=compiler, capabilities=capabilities,
        source_manifest_sha256=manifest_hash, executable_sha256=sha(app/NAME),
        linked_libraries=dependencies, gui_acceptance='pending native desktop testing',
        arch_niri_acceptance='unverified', linux_export='disabled')
    (app / 'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    archive = output / ARCHIVE
    digest = package_zip(app,archive)
    print(json.dumps(dict(zip=str(archive),sha256=digest,report=report),indent=2))
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    build(args.output)


if __name__ == '__main__':
    main()
