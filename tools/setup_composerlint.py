#!/usr/bin/env python3
"""Consent-first, source-only macOS ARM64 setup. Uses only the standard library.

No application or client settings are edited. All mutable installer state lives
under an exclusively owned prefix; the printed MCP environment selects it.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

if sys.platform != 'win32':
    import fcntl

ROOT = Path(__file__).resolve().parents[1]
BUILD = '6.3.0+composerlint.tie1'
SOURCE_HASH = '33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c'
PATCH_HASH = '10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60'
SOURCE_URL = ('https://files.pythonhosted.org/packages/d8/07/'
              '63fe55a328593336a66750de517a8ab6ebe7c0ad1b7e168d98d79d8503fc/verovio-6.3.0.tar.gz')
OFFICIAL_WHEEL = 'verovio-6.3.0-cp310-abi3-macosx_13_0_arm64.whl'
OFFICIAL_HASH = 'c842f5d21d31c3d47e835e07565a9b8623b78feef527affe149ceadd9ecaa55a'
OFFICIAL_URL = ('https://files.pythonhosted.org/packages/6b/0b/'
                '42e560df4051b8c05407162ba117eb14b44f011e84818acc4c2065161d87/' + OFFICIAL_WHEEL)
PATCH = ROOT / 'patches/verovio-6.3.0-deferred-tie-stops.patch'
BUILD_REQUIREMENTS = ROOT / 'requirements-renderer-build.txt'


class SetupError(Exception):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked(path, expected):
    if Path(path).is_symlink() or not Path(path).is_file() or digest(path) != expected:
        raise SetupError(f'Integrity check failed: {path}. Preserve it for inspection; do not re-pin automatically.')


def safe_path(path):
    """Reject symlinked writable destinations, including existing ancestors."""
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise SetupError(f'Symlinked setup destination refused: {parent}')
    return path


def private_dir(path):
    safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise SetupError(f'Setup directory must be owned by you and private (mode 700): {path}')


def write_json(path, value):
    safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.setup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clean_env():
    # Prevent implicit Python downloads, alternate indexes, compiler injection,
    # credentials in logs, or request/client settings from selecting a build.
    env = {k: v for k, v in os.environ.items() if not k.startswith(
        ('UV_', 'PIP_', 'PYTHON', 'COMPOSERLINT_', 'CMAKE_', 'SKBUILD_', 'DYLD_'))
        and k not in {'VIRTUAL_ENV', 'CONDA_PREFIX', 'CC', 'CXX', 'CFLAGS',
                     'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'ARCHFLAGS', 'SDKROOT',
                     'MACOSX_DEPLOYMENT_TARGET', 'VEROVIO_BUILD_VERSION'}}
    env.update(PYTHONDONTWRITEBYTECODE='1', UV_PYTHON_DOWNLOADS='never')
    return env


def run(command, *, env=None, log=None, capture=False, timeout=1800):
    command = [str(item) for item in command]
    if log:
        log.write('\n$ ' + shlex.join(command) + '\n')
        log.flush()
    try:
        process = subprocess.Popen(command, cwd=ROOT, env=env or clean_env(),
                                   stdin=subprocess.DEVNULL, text=True, start_new_session=True,
                                   stdout=subprocess.PIPE if capture else log,
                                   stderr=subprocess.PIPE if capture else subprocess.STDOUT)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f'Could not complete {command[0]}: {exc}') from exc
    if process.returncode:
        detail = (stderr or '')[-1500:] if capture else 'See setup.log for diagnostics.'
        raise SetupError(f'Command failed ({process.returncode}): {shlex.join(command)}\n{detail}')
    return stdout if capture else None


def preflight(full):
    errors = []
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        errors.append('Run setup as your normal user, without sudo.')
    if sys.version_info[:2] != (3, 12) or platform.python_implementation() != 'CPython':
        errors.append('Use CPython 3.12 (COMPOSERLINT_SETUP_PYTHON selects its executable).')
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        errors.append('Only native macOS Apple Silicon is supported; Intel/Rosetta, Linux and Windows are unverified.')
    uv = shutil.which('uv')
    if not uv:
        errors.append('Install uv yourself following https://docs.astral.sh/uv/getting-started/installation/.')
    else:
        print('uv:', run([uv, '--version'], capture=True, timeout=15).strip())
    if full:
        for command in (['/usr/bin/xcode-select', '-p'], ['/usr/bin/xcrun', '--find', 'clang++']):
            try:
                print('Apple tools:', run(command, capture=True, timeout=15).strip())
            except SetupError:
                errors.append('Apple Command Line Tools/SDK missing. Install them manually using xcode-select --install.')
        if not shutil.which('patch'):
            errors.append('The patch command is missing; provision Apple Command Line Tools manually.')
        checked(PATCH, PATCH_HASH)
    print('Platform:', platform.platform(), '| Python:', platform.python_version(), sys.executable)
    for name in ('uv.lock', 'pyproject.toml', 'requirements-viewer.txt',
                 'requirements-renderer-build.txt', 'tests/test_renderer_packaging_equivalence.py',
                 'tests/fixtures/basic.musicxml', 'tools/installation_smoke.py'):
        if not (ROOT / name).is_file():
            errors.append(f'Incomplete source distribution: missing {name}')
    for name in ('composer-import', 'composer-review', 'composer-edit'):
        if not (ROOT / 'skills' / name / 'SKILL.md').is_file():
            errors.append(f'Missing authoritative Skill: {name}')
    if errors:
        raise SetupError('\n'.join(dict.fromkeys(errors)))
    return uv


def recipe(full):
    paths = [ROOT / name for name in ('pyproject.toml', 'uv.lock', 'requirements-viewer.txt',
             'requirements-renderer-build.txt', 'install.sh')]
    for directory in ('src', 'tools', 'skills', 'tests'):
        paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc')
    paths.append(PATCH)
    identity = {'full': full, 'python': str(Path(sys.executable).resolve()),
                'python_hash': digest(Path(sys.executable).resolve()),
                'platform': platform.platform(),
                'files': {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}}
    if full:
        identity['compiler'] = run(['/usr/bin/xcrun', 'clang++', '--version'], capture=True, timeout=15)
        identity['sdk'] = run(['/usr/bin/xcrun', '--show-sdk-version'], capture=True, timeout=15)
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def extract_source(archive, destination):
    """The reviewed sdist needs regular files/directories only; never follow links."""
    with tarfile.open(archive, 'r:gz') as stream:
        members = stream.getmembers()
        if len(members) > 20000 or sum(m.size for m in members) > 256 * 1024 * 1024:
            raise SetupError('Source archive exceeds extraction limits.')
        seen = set()
        for member in members:
            parts = Path(member.name).parts
            if (not parts or parts[0] != 'verovio-6.3.0' or '..' in parts
                    or Path(member.name).is_absolute() or member.name in seen
                    or not (member.isfile() or member.isdir())):
                raise SetupError(f'Unsafe source archive member: {member.name}')
            seen.add(member.name)
        stream.extractall(destination, members=members, filter='data')
    return destination / 'verovio-6.3.0'


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org':
            raise SetupError('Unexpected upstream download redirect refused.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_artifact(prefix, name, url, expected, offline):
    target = prefix / name
    if target.exists() or target.is_symlink():
        checked(target, expected)
        return target
    if offline:
        raise SetupError(f'Offline source archive/artifact missing: {target}. Supply the exact reviewed material, then retry.')
    partial = prefix / (name + '.download')
    safe_path(partial)
    try:
        opener = urllib.request.build_opener(OfficialRedirects)
        with opener.open(url, timeout=60) as response, partial.open('xb') as output:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 16 * 1024 * 1024:
                    raise SetupError('Source download exceeded its size limit.')
                output.write(block)
        checked(partial, expected)
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()
    print('Verified upstream artifact stored at:', target)
    return target


def source_archive(prefix, offline):
    return download_artifact(prefix, 'verovio-6.3.0.tar.gz', SOURCE_URL, SOURCE_HASH, offline)


def inventory(directory):
    return {str(p): digest(p) for p in sorted(directory.rglob('*')) if p.is_file()
            and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def verify_manifest(path):
    if path.is_symlink():
        raise SetupError(f'Symlinked manifest refused: {path}')
    value = json.loads(path.read_text())
    if value['package_version'] != BUILD or len(value['files_sha256']) < 5:
        raise SetupError('Incomplete renderer manifest.')
    for name, expected in value['files_sha256'].items():
        checked(Path(name), expected)
    return value


class Installer:
    def __init__(self, prefix, uv, offline, identity):
        self.prefix, self.uv, self.offline, self.identity = prefix, uv, offline, identity
        self.env = clean_env()
        self.env['UV_CACHE_DIR'] = str(prefix / 'uv-cache')
        self.log = None

    def uv_run(self, *args, env=None):
        command = [self.uv, '--no-config', '--no-progress', '--no-python-downloads']
        if self.offline:
            command.append('--offline')
        return run(command + list(args), env=env or self.env, log=self.log)

    def environment(self, name):
        destination = self.prefix / name
        if not destination.exists():
            self.uv_run('venv', '--python', sys.executable, destination)
        if destination.is_symlink() or not (destination / 'pyvenv.cfg').is_file():
            raise SetupError(f'Invalid managed environment: {destination}; use a fresh --prefix.')
        return destination / 'bin/python'

    def core(self):
        print('Installing/checking locked ComposerLint dependencies…', flush=True)
        python = self.environment('runtime')
        env = dict(self.env, UV_PROJECT_ENVIRONMENT=str(self.prefix / 'runtime'))
        # Inexact intentionally retains separately pinned official Verovio.
        # Hatchling is supplied from uv.lock, avoiding unlocked isolated-build deps.
        self.uv_run('sync', '--locked', '--inexact', '--no-install-project', '--no-build', env=env)
        self.uv_run('sync', '--locked', '--inexact', '--no-editable', '--no-build-isolation', env=env)
        return python

    def renderer(self, python):
        official = self.prefix / 'official'
        private_dir(official)
        download_artifact(official, OFFICIAL_WHEEL, OFFICIAL_URL, OFFICIAL_HASH, self.offline)
        self.uv_run('pip', 'install', '--python', python, '--require-hashes', '--only-binary', ':all:',
                    '--no-index', '--find-links', official, '-r', ROOT / 'requirements-viewer.txt')
        manifest = self.prefix / 'renderer.json'
        receipt = self.prefix / 'renderer-build.json'
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            checked(manifest, previous['manifest_sha256'])
            verify_manifest(manifest)
            if inventory(self.prefix / 'renderer') != previous['runtime_files']:
                raise SetupError('Installed renderer file set changed; use a fresh --prefix for recovery.')
            print('Reusing verified patched renderer; no compilation needed.', flush=True)
            return manifest
        archive = source_archive(self.prefix, self.offline)
        build_python = self.environment('build-tools')
        self.uv_run('pip', 'sync', '--python', build_python, '--require-hashes', '--only-binary', ':all:',
                    '--index-url', 'https://pypi.org/simple', BUILD_REQUIREMENTS)
        print('Compiling the exact patched renderer (this can take several minutes)…', flush=True)
        # Fresh build staging on each interrupted attempt; never patch twice.
        with tempfile.TemporaryDirectory(prefix='source-', dir=self.prefix) as staging:
            source = extract_source(archive, Path(staging))
            before = {p.relative_to(source): digest(p) for p in source.rglob('*') if p.is_file()}
            run(['patch', '--batch', '--forward', '--fuzz=0', '--no-backup-if-mismatch',
                 '-d', source, '-p1', '-i', PATCH], env=self.env, log=self.log)
            after = {p.relative_to(source): digest(p) for p in source.rglob('*') if p.is_file()}
            if before.keys() != after.keys() or [p for p in before if before[p] != after[p]] != [Path('src/iomusxml.cpp')]:
                raise SetupError('Patch changed more than the reviewed source file.')
            artifacts = self.prefix / 'artifacts'
            private_dir(artifacts)
            env = dict(self.env, PATH=str(build_python.parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                       VEROVIO_BUILD_VERSION=BUILD, MACOSX_DEPLOYMENT_TARGET='13',
                       CMAKE_BUILD_PARALLEL_LEVEL='4', CMAKE_GENERATOR='Ninja',
                       CC='/usr/bin/clang', CXX='/usr/bin/clang++')
            run([build_python, '-I', '-B', '-m', 'build', '--no-isolation', '--wheel',
                 '-Cbuild-dir=' + str(Path(staging) / 'build'), '--outdir', artifacts, source],
                env=env, log=self.log)
        wheels = list(artifacts.glob('verovio-*.whl'))
        if len(wheels) != 1 or wheels[0].is_symlink():
            raise SetupError('Expected exactly one locally built renderer wheel.')
        child = self.environment('renderer')
        self.uv_run('pip', 'install', '--python', child, '--no-deps', '--no-index', '--reinstall', wheels[0])
        package = Path(run([child, '-I', '-B', '-c',
                     'import verovio; print(verovio.__path__[0])'], env=self.env, capture=True).strip())
        checked(PATCH, PATCH_HASH)
        files = inventory(package)
        for retained in (wheels[0], PATCH, archive, Path(child).resolve()):
            files[str(retained)] = digest(retained)
        pending = self.prefix / 'renderer.pending.json'
        write_json(pending, dict(package_version=BUILD, python=str(child), files_sha256=files))
        return pending

    def smoke(self, python, manifest):
        print('Checking analysis, MCP discovery and authored renderer behavior…', flush=True)
        env = dict(self.env)
        if manifest:
            env['COMPOSERLINT_RENDERER_CONFIG'] = str(manifest)
        run([python, '-I', '-B', ROOT / 'tools/installation_smoke.py'], env=env, log=self.log, timeout=180)
        if manifest:
            env['COMPOSERLINT_RENDERER_EQUIVALENCE'] = '1'
            run([python, '-I', '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                 ROOT / 'tests/test_renderer_packaging_equivalence.py'], env=env, log=self.log, timeout=180)

    def install(self, full):
        self.log = (self.prefix / 'setup.log').open('a')
        try:
            python = self.core()
            manifest = self.renderer(python) if full else None
            self.smoke(python, manifest)
            if manifest and manifest.name == 'renderer.pending.json':
                final = self.prefix / 'renderer.json'
                if final.exists() or final.is_symlink():
                    raise SetupError('Existing renderer configuration preserved. Use a fresh --prefix.')
                os.replace(manifest, final)
                write_json(self.prefix / 'renderer-build.json', dict(
                    manifest_sha256=digest(final), runtime_files=inventory(self.prefix / 'renderer')))
                manifest = final
            write_json(self.prefix / 'complete.json', dict(recipe=self.identity, full=full))
            connection_instructions(python, manifest)
        finally:
            self.log.close()


def connection_instructions(python, manifest):
    print('\nSetup passed. Configure a local stdio MCP server in Codex:')
    print('Command:', python)
    print('Arguments: -B -m composerlint.server')
    if manifest:
        print('Environment: COMPOSERLINT_RENDERER_CONFIG=' + str(manifest))
    else:
        print('Text-analysis-only setup; notation/editing renderer was not installed.')
    print('Keep your existing configuration/data/export overrides if you use them.')
    print('Open this source folder in Codex, reconnect MCP, and start a fresh chat.')
    print('Verify version 0.2.0 and the actual 18-tool inventory after connection.')
    print('Skills: skills/composer-import, skills/composer-review, skills/composer-edit.')
    print('Project discovery links under .agents/skills must target ../../skills/<name>.')
    print('If extraction omitted links, recreate only those three missing links manually; see docs/INSTALL.md.')
    print('No global client settings or existing renderer configuration were changed.')


def consent(mode, offline, prefix):
    print('\nPlan:', 'FULL viewer/editing setup' if mode == 'full' else 'TEXT ANALYSIS ONLY')
    print('Destination (private environments, artifacts, manifest, logs):', prefix)
    print('Install unchanged uv.lock dependencies, including locked pytest/Hatchling for verification/building.')
    print('Exact package versions/download hashes are listed in uv.lock (no dependency upgrades).')
    print('Package source: https://pypi.org/simple and hash-pinned files.pythonhosted.org artifacts.')
    if mode == 'full':
        print('Install official Verovio 6.3.0 in main environment (requirements-viewer.txt hash).')
        print('Official wheel:', OFFICIAL_URL)
        print('Install pinned build tools in separate environment:')
        print('build 1.6.1, scikit-build-core 1.1.1, CMake 4.4.4, SWIG 4.5.1, Ninja 1.13.2,')
        print('packaging 26.3, pathspec 1.1.1, pyproject-hooks 1.3.3 (requirements-renderer-build.txt hashes).')
        print('Obtain official source:', SOURCE_URL)
        print('Verify source SHA-256:', SOURCE_HASH)
        print('Apply exact existing patch; compile C++ locally; install separate patched renderer.')
        print('Verify ties, warning refusal and source mapping before creating the local renderer manifest.')
        print('Upstream package resources remain local; no binaries/fonts are redistributed by ComposerLint.')
    print('No Python/uv/Apple-tool installation, administrator access, client-setting edits, or export operations.')
    print('Network:', 'FORBIDDEN; missing cached materials fail.' if offline else 'approved missing artifacts may be downloaded.')
    return input('Approve these installations/configuration changes? [y/N] ').strip().lower() in {'y', 'yes'}


@contextlib.contextmanager
def owned_prefix(prefix, identity):
    safe_path(prefix)
    owner = prefix / 'owner.json'
    if prefix.exists():
        if not owner.is_file() or owner.is_symlink() or json.loads(owner.read_text()) != {'recipe': identity}:
            raise SetupError('Existing destination is unrelated or belongs to a different recipe; choose a fresh --prefix.')
    private_dir(prefix)
    fd = os.open(prefix / 'setup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SetupError('Another setup is using this destination; wait for it to finish.') from exc
        if not owner.exists():
            write_json(owner, {'recipe': identity})
        yield
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['full', 'text'], help='otherwise ask interactively')
    parser.add_argument('--check', action='store_true', help='read-only readiness/integrity inspection; no network')
    parser.add_argument('--offline', action='store_true', help='strictly prohibit network acquisition')
    parser.add_argument('--prefix', type=Path, help='new private absolute setup directory (existing parent required)')
    args = parser.parse_args(argv)
    mode = args.mode
    if mode is None and not args.check:
        choice = input('ComposerLint: [1] Full installation [2] Text analysis only [3] Cancel: ').strip()
        mode = {'1': 'full', '2': 'text'}.get(choice)
        if mode is None:
            print('Cancelled; no changes made.')
            return 0
    mode = mode or 'full'
    prefix = None
    try:
        uv = preflight(mode == 'full')
        identity = recipe(mode == 'full')
        base = ROOT / '.composerlint-setup'
        prefix = args.prefix or base / identity[:16]
        if not prefix.is_absolute() or '..' in prefix.parts:
            raise SetupError('--prefix must be absolute without parent traversal.')
        safe_path(prefix)
        if args.check:
            complete = prefix / 'complete.json'
            if not complete.is_file() or complete.is_symlink() or json.loads(complete.read_text()) != dict(recipe=identity, full=mode == 'full'):
                raise SetupError('No completed matching installation here. Run setup (or select its --mode/--prefix).')
            python = prefix / 'runtime/bin/python'
            if mode == 'full':
                previous = json.loads((prefix / 'renderer-build.json').read_text())
                checked(prefix / 'renderer.json', previous['manifest_sha256'])
                verify_manifest(prefix / 'renderer.json')
                if inventory(prefix / 'renderer') != previous['runtime_files']:
                    raise SetupError('Renderer file set changed.')
            run([python, '-I', '-B', '-c', 'from importlib.metadata import version; '
                 'assert version("composerlint")=="0.2.0"; '
                 + ('assert version("verovio")=="6.3.0"' if mode == 'full' else 'print("Core package present")')], capture=True)
            print('Read-only check passed:', prefix)
            return 0
        if not consent(mode, args.offline, prefix):
            print('Cancelled; no changes made.')
            return 0
        os.umask(0o077)
        if args.prefix:
            if not prefix.parent.is_dir():
                raise SetupError('The --prefix parent must already exist.')
        else:
            private_dir(base)
        with owned_prefix(prefix, identity):
            Installer(prefix, uv, args.offline, identity).install(mode == 'full')
        return 0
    except (SetupError, OSError, ValueError, KeyError) as exc:
        print(f'\nSetup stopped: {exc}', file=sys.stderr)
        if prefix:
            print(f'Log (if installation started): {prefix / "setup.log"}', file=sys.stderr)
        print('Retry the same command after resolving the problem. Existing exports/settings are preserved.\n'
              'Manual fallback: docs/INSTALL.md and patches/README.md.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print('\nCancelled/interrupted. Retry the same command; no success is claimed.', file=sys.stderr)
        sys.exit(130)
