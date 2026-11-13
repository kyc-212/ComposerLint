"""Consent-gated Windows x64 text setup pilot, using the shared uv.lock."""

import ctypes
from ctypes import wintypes as win
from dataclasses import dataclass, replace
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile

from . import windows_preflight as preflight
from .windows_install_storage import _component, transaction
from .windows_private import read_stable
from .windows_read import _bind
from .windows_workers import SetupProcessCleanupUnconfirmed, run_setup_command


# Per-file elapsed budget for slow, active transfers; the socket timeout is separate.
_DOWNLOAD_DEADLINE_SECONDS = 900


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    filename: str
    url: str
    sha256: str
    size: int
    name: str = ''
    version: str = ''

    def __post_init__(self):
        _component(self.filename)
        if (type(self.size) is not int or not 0 < self.size <= 128 * 1024**2
                or not re.fullmatch('[a-f0-9]{64}', self.sha256)):
            raise SetupError(f'Invalid artifact pin: {self.filename}')
        url = urllib.parse.urlsplit(self.url)
        if (url.scheme != 'https' or url.netloc not in ('github.com', 'files.pythonhosted.org')
                or url.query or url.fragment
                or urllib.parse.unquote(url.path.rsplit('/', 1)[-1]) != self.filename):
            raise SetupError(f'Unexpected artifact source: {self.filename}')


@dataclass(frozen=True)
class Plan:
    destination: Path
    python: Asset
    uv: Asset
    wheels: tuple
    source: tuple
    lock_sha256: str
    identity: str
    renderer: tuple = ()
    controls: tuple = ()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _asset_plan(manifest_bytes, lock_bytes):
    manifest = json.loads(manifest_bytes)
    lock_sha = _sha(lock_bytes)
    if manifest['lock_sha256'] != lock_sha:
        raise SetupError('uv.lock changed. Review the Windows wheel set again before setup.')
    boot = tuple(Asset(**manifest['bootstrap'][key]) for key in ('python', 'uv'))
    origins = (
        'https://github.com/astral-sh/python-build-standalone/releases/download/20260807/',
        'https://github.com/astral-sh/uv/releases/download/0.12.3/',
    )
    for asset, (_, filename, digest), origin in zip(boot, preflight._BOOTSTRAP_PINS, origins, strict=True):
        if ((asset.filename, asset.sha256) != (filename, digest)
                or asset.url != origin + urllib.parse.quote(filename, safe='')):
            raise SetupError('Bootstrap asset differs from the reviewed Windows preflight pin.')
    wheels = tuple(Asset(**value) for value in manifest['wheels'])
    locked = {(p['name'], p['version'], w['url'], w['hash'], w['size'])
              for p in tomllib.loads(lock_bytes.decode('utf-8'))['package']
              if p['source'] == {'registry': 'https://pypi.org/simple'} for w in p.get('wheels', ())}
    if (not wheels or len({w.name for w in wheels}) != len(wheels)
            or len({w.filename.casefold() for w in wheels}) != len(wheels)):
        raise SetupError('The Windows wheel manifest is empty or ambiguous.')
    for w in wheels:
        if (not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', w.name)
                or not re.fullmatch(r'[a-zA-Z0-9.!+]+', w.version)
                or not w.url.startswith('https://files.pythonhosted.org/')
                or not w.filename.endswith(('-win_amd64.whl', '-any.whl'))
                or (w.name, w.version, w.url, 'sha256:' + w.sha256, w.size) not in locked):
            raise SetupError(f'Windows wheel is not in the reviewed lock: {w.filename}')
    return *boot, wheels, lock_sha


def _read_source(path, limit):
    data, stamp = read_stable(path, limit=limit)
    if stamp.links != 1:
        raise SetupError(f'Setup source must be a single regular file: {path}')
    return data


def prepare(root, destination):
    manifest = _read_source(root / 'tools' / 'windows-setup-assets.json', 128 * 1024)
    lock = _read_source(root / 'uv.lock', 4 * 1024**2)
    python, uv, wheels, lock_sha = _asset_plan(manifest, lock)
    paths = [root / name for name in ('pyproject.toml', 'README.md', 'LICENSE')]
    package = root / 'src' / 'composerlint'
    def reject_walk_error(error):
        raise error

    for directory, subdirs, files in os.walk(package, followlinks=False, onerror=reject_walk_error):
        if Path(directory).lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise SetupError(f'Setup source reparse directory refused: {directory}')
        # An ignored bytecode cache is never part of the staged source. Any
        # other reparse directory is refused before enumeration can descend.
        subdirs[:] = sorted(name for name in subdirs if name != '__pycache__')
        for name in subdirs:
            candidate = Path(directory, name)
            if candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise SetupError(f'Setup source reparse directory refused: {candidate}')
        paths.extend(Path(directory, name) for name in files if not name.endswith('.pyc'))
    if package / '__init__.py' not in paths or len(paths) > 1024:
        raise SetupError('The reviewed ComposerLint source package is missing or unexpectedly large.')
    source = [(('uv.lock',), lock)]
    for path in sorted(paths):
        parts = tuple(_component(part) for part in path.relative_to(root).parts)
        source.append((parts, _read_source(path, 4 * 1024**2)))
    identity = _sha(json.dumps(
        [str(destination), _sha(manifest), [(parts, _sha(data)) for parts, data in source]],
        ensure_ascii=True, separators=(',', ':')).encode())
    return Plan(Path(destination), python, uv, wheels, tuple(source), lock_sha, identity)


class _AssetRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        origin, target = (urllib.parse.urlsplit(url) for url in (req.full_url, newurl))
        allowed = {'github.com': {'release-assets.githubusercontent.com'},
                   'release-assets.githubusercontent.com': {'release-assets.githubusercontent.com'},
                   'files.pythonhosted.org': {'files.pythonhosted.org'}}
        if (target.scheme != 'https' or target.netloc not in allowed.get(origin.netloc, ())
                or target.fragment):
            raise SetupError('Unexpected artifact redirect refused.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def acquire(asset, *, opener=None):
    """Return bytes only after comparing length and the reviewed SHA-256."""
    opener = opener or urllib.request.build_opener(_AssetRedirects())
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_SECONDS
    try:
        request = urllib.request.Request(asset.url, headers={'Accept-Encoding': 'identity'})
        with opener.open(request, timeout=20) as response:
            content = bytearray()
            digest = hashlib.sha256()
            while True:
                if time.monotonic() >= deadline:
                    raise SetupError(f'Download timed out: {asset.filename}')
                chunk = response.read1(min(1024**2, asset.size + 1 - len(content)))
                # A blocking read can cross the deadline, including the EOF read.
                if time.monotonic() >= deadline:
                    raise SetupError(f'Download timed out: {asset.filename}')
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > asset.size:
                    raise SetupError(f'Download exceeded the reviewed size: {asset.filename}')
                digest.update(chunk)
    except (OSError, http.client.HTTPException) as error:
        # Do not print a signed redirect URL, token, or proxy credentials.
        raise SetupError(f'Could not acquire {asset.filename} ({type(error).__name__}).') from error
    if len(content) != asset.size or digest.hexdigest() != asset.sha256:
        raise SetupError(f'Artifact integrity check failed: {asset.filename}')
    return bytes(content)


def _archive_path(name, directory):
    if directory and name.endswith('/'):
        name = name[:-1]
    parts = tuple(_component(part) for part in name.split('/'))
    if len(parts) > 64:
        raise SetupError('Archive path is too deep.')
    return parts


def unpack(stage, data, kind, *, patch=None):
    """Extract only reviewed layouts and regular files; never call extractall."""
    seen, total = set(), 0
    has_executable = False

    def member(parts, directory, size, open_member):
        nonlocal total, has_executable
        key = tuple(p.casefold() for p in parts)
        if key in seen or len(seen) >= 25_000:
            raise SetupError('Archive contains duplicate Windows paths or too many entries.')
        seen.add(key)
        total += size
        if not 0 <= size <= 256 * 1024**2 or total > 512 * 1024**2:
            raise SetupError('Archive exceeds the expanded size limit.')
        if directory:
            if size:
                raise SetupError('An archive directory unexpectedly has content.')
            stage.mkdir(parts)
            return
        with open_member() as source, stage.create(parts) as dest:
            remaining = size
            while remaining:
                block = source.read(min(1024**2, remaining))
                if not block:
                    raise SetupError('Truncated archive member.')
                dest.write(block)
                remaining -= len(block)
            if source.read(1):
                raise SetupError('Archive member exceeds its declared size.')
        has_executable |= parts in (('python', 'python.exe'), ('uv', 'uv.exe'))

    if kind in ('python', 'renderer'):
        with tarfile.open(fileobj=io.BytesIO(data), mode='r|gz') as archive:
            for item in archive:
                directory = item.isdir()
                if not directory and (item.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or item.issparse()):
                    raise SetupError('Python archive links, sparse entries and special files are refused.')
                parts = _archive_path(item.name, directory)
                expected_root = 'python' if kind == 'python' else 'verovio-6.3.0'
                if parts[0] != expected_root or (len(parts) == 1 and not directory):
                    raise SetupError(f'Unexpected {kind} archive layout.')
                if kind == 'renderer' and parts == (expected_root, 'src', 'iomusxml.cpp'):
                    from .windows_renderer import apply_reviewed_patch
                    patched = apply_reviewed_patch(archive.extractfile(item).read(), patch)
                    member(parts, False, len(patched), lambda: io.BytesIO(patched))
                    has_executable = True
                    continue
                member(parts, directory, item.size, lambda: archive.extractfile(item))
    elif kind == 'uv':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                directory = item.is_dir()
                if stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise SetupError('uv archive links and special files are refused.')
                parts = _archive_path(item.filename, directory)
                if parts[0] == 'uv-x86_64-pc-windows-msvc':
                    parts = parts[1:]
                if not parts and directory:
                    continue
                if len(parts) != 1 or directory or parts[0] not in (
                        'uv.exe', 'uvx.exe', 'uvw.exe', 'LICENSE-APACHE', 'LICENSE-MIT', 'README.md'):
                    raise SetupError('Unexpected uv archive layout.')
                member(('uv', *parts), False, item.file_size, lambda: archive.open(item))
    else:
        raise SetupError('Unknown Windows bootstrap archive type.')
    if not has_executable:
        raise SetupError(f'The {kind} archive did not supply the expected executable.')


def _system_root():
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
    query = _bind(kernel, 'GetWindowsDirectoryW', win.UINT, win.LPWSTR, win.UINT)
    buffer = ctypes.create_unicode_buffer(32768)
    count = query(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise OSError('Could not resolve the system Windows directory.')
    return Path(buffer.value)


def _write(stage, parts, data):
    with stage.create(parts) as stream:
        stream.write(data)


_VERIFY = r'''
import json, re, sys
from importlib.metadata import distributions
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
versions = json.loads(sys.argv[1])
locked = {}
for line in Path(sys.argv[2]).read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    req = Requirement(line)
    if req.marker is None or req.marker.evaluate():
        match = re.fullmatch(r'==([^*]+)', str(req.specifier))
        if not match or req.url or req.extras or req.name in locked:
            raise RuntimeError('Lock export is not an exact wheel plan.')
        locked[req.name] = match[1]
if locked != versions:
    differing = {name: (locked.get(name), versions.get(name)) for name in sorted(locked.keys() | versions.keys())
                 if locked.get(name) != versions.get(name)}
    raise RuntimeError('Windows lock closure differs (lock, manifest): ' + str(differing))
expected = versions | {'composerlint': '0.2.0'}
installed = {}
for dist in distributions():
    name = canonicalize_name(dist.metadata['Name'])
    if name in installed:
        raise RuntimeError('Duplicate installed distribution: ' + name)
    installed[name] = dist.version
if expected != installed:
    differing = {name: (expected.get(name), installed.get(name)) for name in sorted(expected.keys() | installed.keys())
                 if expected.get(name) != installed.get(name)}
    raise RuntimeError('Installed distributions differ (expected, installed): ' + str(differing))
import composerlint, numpy, pydantic_core, win32api
print('Verified locked distributions, core import and native extension loading.')
'''


def checked_command(arguments, *, cwd, env, timeout):
    try:
        result = run_setup_command(arguments, cwd=cwd, env=env, timeout=timeout, capture_result=True)
    except SetupProcessCleanupUnconfirmed:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise SetupError(f'Could not run {subprocess.list2cmdline(arguments)}: {error}') from error
    if result.returncode:
        stdout, stderr = (data.decode('utf-8', 'backslashreplace').strip() or '(empty)'
                          for data in (result.stdout, result.stderr))
        raise SetupError(f'Command exited with 0x{result.returncode & 0xffffffff:08X}: '
                         f'{subprocess.list2cmdline(arguments)}\nstdout: {stdout}\nstderr: {stderr}')
    return result.stdout


def populate(plan, stage, status, *, fetch=acquire, command=checked_command, windows_root=_system_root):
    # Exercise inherited directory and file access before the first transfer.
    for directory in ('home', 'tmp', 'wheels', 'dist', 'cache'):
        stage.mkdir(('work', directory))
    source = stage.path / 'work' / 'source'
    for parts, data in plan.source:
        _write(stage, ('work', 'source', *parts), data)

    work = stage.path / 'work'
    home, temp = work / 'home', work / 'tmp'
    system = windows_root()
    env = {key: str(value) for key, value in {
        'SystemRoot': system, 'WINDIR': system, 'PATH': system / 'System32',
        'HOME': home, 'USERPROFILE': home, 'LOCALAPPDATA': home, 'APPDATA': home,
        'TMP': temp, 'TEMP': temp, 'MPLCONFIGDIR': home, 'UV_CACHE_DIR': work / 'cache',
        'UV_PYTHON_INSTALL_DIR': home / 'python-versions',
        'UV_PYTHON_BIN_DIR': home / 'python-shims', 'UV_TOOL_DIR': home / 'uv-tools',
        'UV_TOOL_BIN_DIR': home / 'uv-bin',
    }.items()}
    env.update(UV_PYTHON_DOWNLOADS='never', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')

    def verified_asset(asset):
        data = fetch(asset)
        print(f'Verified {asset.filename} bytes={asset.size} SHA-256={asset.sha256}', flush=True)
        return data

    status('bootstrap', 'Acquiring and verifying the pinned private Python and uv.')
    for asset, kind in ((plan.python, 'python'), (plan.uv, 'uv')):
        try:
            unpack(stage, verified_asset(asset), kind)
        except (tarfile.TarError, zipfile.BadZipFile, EOFError) as error:
            raise SetupError(f'Cannot unpack the verified {kind} archive: {error}') from error

    def run(parts, *args, cwd=work, timeout=180):
        with stage.held_file(parts) as executable:
            return command([str(item) for item in (executable, *args)],
                           cwd=str(cwd), env=env, timeout=timeout)

    python = ('python', 'python.exe')
    runtime = ('runtime', 'Scripts', 'python.exe')
    uv = ('uv', 'uv.exe')
    base_python = stage.path.joinpath(*python)
    target_python = stage.path.joinpath(*runtime)

    def uv_run(*args, cwd=work):
        return run(uv, '--offline', '--no-config', '--no-progress', '--no-python-downloads',
                   *args, cwd=cwd)

    result = run(python, '-I', '-S', '-B', '-c',
                 'import struct,sys; print(sys.implementation.name, sys.version.split()[0], struct.calcsize("P"))')
    if result.strip() != b'cpython 3.12.13 8':
        raise SetupError(f'Expected private CPython 3.12.13 x64, got {result.strip()!r}.')
    uv_version = uv_run('--version').strip()
    if uv_version.split()[:2] != [b'uv', b'0.12.3']:
        raise SetupError(f'Expected private uv 0.12.3, got {uv_version!r}.')

    status('dependencies', 'Verifying and staging the exact Windows wheels from uv.lock.')
    export = uv_run('export', '--locked', '--python', base_python,
                    '--no-emit-project', '--no-header', '--no-annotate', '--no-hashes',
                    '--format', 'requirements-txt',
                    cwd=source)
    _write(stage, ('work', 'export.txt'), export)
    for wheel in plan.wheels:
        _write(stage, ('work', 'wheels', wheel.filename), verified_asset(wheel))
    requirements = ''.join(f'{w.name}=={w.version} --hash=sha256:{w.sha256}\n' for w in plan.wheels)
    _write(stage, ('work', 'windows-requirements.txt'), requirements.encode('ascii'))
    uv_run('venv', '--python', base_python, stage.path / 'runtime')
    pip = ('pip', 'install', '--python', target_python, '--no-index', '--no-deps',
           '--link-mode', 'copy', '--no-build')
    uv_run(*pip, '--require-hashes', '--find-links', work / 'wheels',
           '-r', work / 'windows-requirements.txt')
    status('verification', 'Building only ComposerLint from the reviewed local source and checking the private environment.')
    # Hatchling came from the same hash-verified wheel set. No isolated build
    # environment, build dependency resolution or index access is possible.
    local_wheel = 'composerlint-0.2.0-py3-none-any.whl'
    result = run(runtime, '-I', '-B', '-c',
                 'from hatchling.build import build_wheel; import sys; print(build_wheel(sys.argv[1]))',
                 work / 'dist', cwd=source)
    if result.strip().decode('ascii') != local_wheel:
        raise SetupError('Local ComposerLint wheel did not match the expected name.')
    with stage.held_file(('work', 'dist', local_wheel)) as wheel_path:
        uv_run(*pip, wheel_path)
    uv_run('pip', 'check', '--python', target_python)
    run(runtime, '-I', '-B', '-c', _VERIFY,
        json.dumps({w.name: w.version for w in plan.wheels}), work / 'export.txt')
    receipt = {'schema': 1, 'target': 'windows-x64-text-pilot', 'plan': plan.identity,
               'lock_sha256': plan.lock_sha256, 'python': str(target_python)}
    if plan.renderer:
        receipt.update(install_renderer(plan, stage, run, uv_run, pip, base_python, env, status))
    status('cleanup', 'Verifying private staging cleanup before publishing the installation receipt.')
    stage.clear_work()
    stage.commit(receipt)
    return target_python


_CONTROL_FILES = (
    'tools/ci_windows_mcp.py',
    'tests/test_renderer_packaging_equivalence.py', 'tests/test_v02_renderer_ties.py',
    'tests/fixtures/v02_m2/multipage.musicxml', 'tests/fixtures/basic.musicxml',
    'tests/fixtures/m3/cadence_authentic_end.musicxml',
)
RENDERER_PYTEST_OPTIONS = ('-p', 'no:cacheprovider', '-p', 'no:tmpdir')
RENDERER_CONTROL_SELECTION = ('-k', 'not test_windows_render_holds_pins_through_worker_exit')


def with_renderer(plan, root, bundle, candidate_sha256):
    from . import windows_renderer as renderer
    bundle = Path(preflight.local_path_text(bundle, target='win32'))
    pins = (
        (renderer.OFFICIAL_WHEEL, renderer.OFFICIAL_SHA256),
        (renderer.PATCHED_WHEEL, candidate_sha256),
        (renderer.SOURCE_ARCHIVE, renderer.SOURCE_SHA256),
        (renderer.PATCH, renderer.PATCH_SHA256),
    )
    files = tuple((name, renderer.checked_bytes(bundle / name, expected, 32 * 1024**2))
                  for name, expected in pins)
    provenance = _read_source(bundle / 'build-provenance.json', 16 * 1024)
    build = json.loads(provenance)
    for key, expected in (
            ('source_sha256', renderer.SOURCE_SHA256), ('patch_sha256', renderer.PATCH_SHA256),
            ('preimage_sha256', renderer.PREIMAGE), ('postimage_sha256', renderer.POSTIMAGE),
            ('wheel_sha256', candidate_sha256)):
        if build.get(key) != expected:
            raise SetupError(f'Renderer build provenance {key}: expected {expected!r}, got {build.get(key)!r}')
    files += (('build-provenance.json', provenance),)
    controls = tuple((tuple(name.split('/')), _read_source(root / name, 4 * 1024**2))
                     for name in _CONTROL_FILES)
    identity = _sha(json.dumps([plan.identity, [(name, _sha(data)) for name, data in files],
                               [(parts, _sha(data)) for parts, data in controls]]).encode())
    return replace(plan, identity=identity, renderer=files, controls=controls)


def install_renderer(plan, stage, run, uv_run, pip, base_python, env, status):
    import xml.etree.ElementTree as ET
    from . import windows_renderer as renderer

    status('renderer', 'Installing the pinned official wheel and separate patched Windows renderer candidate.')
    artifacts = stage.path / 'renderer-artifacts'
    for name, raw in plan.renderer:
        _write(stage, ('renderer-artifacts', name), raw)
    for parts, raw in plan.controls:
        _write(stage, ('work', 'controls', *parts), raw)
    child = stage.path / 'renderer'
    child_python = child / 'Scripts' / 'python.exe'
    uv_run(*pip, artifacts / renderer.OFFICIAL_WHEEL)
    uv_run('venv', '--python', base_python, child)
    uv_run('pip', 'install', '--python', child_python, '--no-index', '--no-deps',
           '--link-mode', 'copy', '--no-build', artifacts / renderer.PATCHED_WHEEL)
    for parts, version in ((('runtime', 'Scripts', 'python.exe'), '6.3.0'),
                           (('renderer', 'Scripts', 'python.exe'), renderer.BUILD)):
        identity_output = run(parts, '-I', '-B', '-X', 'utf8', '-c', renderer.IDENTITY_PROBE, version,
                              stage.path.joinpath(*parts), base_python)
        print(identity_output.decode('utf-8', 'backslashreplace'), end='', flush=True)

    packages = child / 'Lib' / 'site-packages'
    retained = [base_python, child_python, child / 'pyvenv.cfg']
    retained.extend(base_python.parent.glob('*.dll'))
    retained.extend(artifacts / name for name, _ in plan.renderer)
    retained.extend(p for p in packages.rglob('*') if p.is_file())
    if not any(path.suffix.lower() == '.pyd' for path in retained):
        raise SetupError('Patched Windows renderer native module is absent.')
    files = {}
    for path in sorted(retained):
        rel = path.relative_to(stage.path)
        with stage.held_file(rel.parts) as held, held.open('rb') as stream:
            files[rel.as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    config = stage.path / 'renderer.json'
    _write(stage, (config.name,), json.dumps({
        'package_version': renderer.BUILD, 'python': child_python.relative_to(stage.path).as_posix(),
        'files_sha256': files,
    }, sort_keys=True).encode('utf-8'))

    env.update(COMPOSERLINT_RENDERER_CONFIG=str(config), COMPOSERLINT_RENDERER_EQUIVALENCE='1',
               COMPOSERLINT_TEMP_DIR=env['TEMP'], PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    runtime = ('runtime', 'Scripts', 'python.exe')
    controls = stage.path / 'work' / 'controls'
    report = stage.path / 'work' / 'renderer-controls.xml'
    status('renderer_controls', 'Checking all ten official and patched renderer equivalence controls.')
    run(runtime, '-I', '-B', '-X', 'utf8', '-m', 'pytest', '-v', *RENDERER_PYTEST_OPTIONS, *RENDERER_CONTROL_SELECTION,
        '--junitxml=' + str(report), controls / 'tests/test_renderer_packaging_equivalence.py',
        cwd=controls, timeout=240)
    counts = control_counts(ET.parse(report).getroot())
    print('Renderer controls:', counts, flush=True)
    status('mcp', 'Checking the installed MCP server, loopback notation and temporary edit lifecycle.')
    output = run(runtime, '-I', '-B', '-X', 'utf8', controls / 'tools/ci_windows_mcp.py', '--full',
                 cwd=controls, timeout=240)
    print(output.decode('utf-8', 'backslashreplace'), end='', flush=True)
    return dict(target='windows-x64-full-pilot', renderer_config=str(config),
                renderer_sha256=files['renderer-artifacts/' + renderer.PATCHED_WHEEL],
                renderer_controls=counts, mcp_tools=18)


def control_counts(tree):
    suites = [tree] if tree.tag == 'testsuite' else tree.findall('testsuite')
    counts = {key: sum(int(s.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if counts != dict(tests=10, failures=0, errors=0, skipped=0):
        raise SetupError(f'All 10 renderer controls must pass: {counts}')
    return counts


def install(root, *, status, confirm=input, inspect=preflight.inspect, make_plan=prepare,
            storage=transaction, build=populate, renderer_bundle=None, renderer_sha256=None):
    report = inspect()
    if not report.compatible:
        preflight.print_report(report)
        raise SetupError('Read-only Windows preflight refused installation.')
    plan = make_plan(root, report.destination)
    assets = (plan.python, plan.uv, *plan.wheels)
    if renderer_bundle is not None:
        plan = with_renderer(plan, root, renderer_bundle, renderer_sha256)
        print('Windows x64 full setup pilot')
        for name, data in plan.renderer:
            print(f'  {name}\n    SHA-256 {_sha(data)}')
    else:
        print('Windows x64 text setup pilot')
    print('Private destination:', plan.destination)
    print('Reviewed setup plan:', plan.identity)
    print(f'Will acquire {len(assets)} SHA-256-pinned files ({sum(a.size for a in assets):,} bytes).')
    for asset in (plan.python, plan.uv):
        print(f'  {asset.filename}\n    SHA-256 {asset.sha256}')
    print(f'  {len(plan.wheels)} wheels selected from uv.lock; exact pins in tools/windows-setup-assets.json.')
    print('No system PATH, global Python, registry settings, or AI-assistant configuration changes.')
    if not plan.renderer:
        print('Text capability only. Renderer and notation editing require full setup.')
    print('Windows Approve & Export is disabled. Client configuration is manual.')
    try:
        consent = confirm('Type INSTALL to download these files and install privately, or press Enter to cancel: ')
    except EOFError:
        consent = ''
    if consent != 'INSTALL':
        print('Cancelled; no downloads or changes made.')
        return 0
    current = inspect()
    if not current.compatible or current.destination != report.destination:
        raise SetupError('Windows destination or capabilities changed after consent. Review the plan again.')
    with storage(plan.destination) as stage:
        runtime = build(plan, stage, status)
    if plan.renderer:
        print('Private Windows full environment verified:', runtime)
        print('MCP environment: COMPOSERLINT_RENDERER_CONFIG=' + str(runtime.parents[2] / 'renderer.json'))
    else:
        print('Private Windows text environment verified:', runtime)
        print('MCP startup and Windows desktop acceptance require separate verification.')
    print('MCP command:', runtime)
    print('MCP arguments: -I -B -X utf8 -m composerlint.server')
    return 0
