#!/usr/bin/env python3
"""Consent-first shared setup: macOS ARM64 and the Ubuntu 24.04 x64 CI pilot.

No application or client settings are edited. All mutable installer state lives
under an exclusively owned prefix; the printed MCP environment selects it.
"""
import argparse
import contextlib
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
# Load only the shared stdlib boundaries: importing composerlint would require
# the dependencies which this backend has not installed yet.
_spec = importlib.util.spec_from_file_location('_composerlint_platform', ROOT / 'src/composerlint/platform_support.py')
host = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host)
BUILD = '6.3.0+composerlint.tie1'
SOURCE_HASH = '33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c'
PATCH_HASH = '10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60'
SOURCE_URL = ('https://files.pythonhosted.org/packages/d8/07/'
              '63fe55a328593336a66750de517a8ab6ebe7c0ad1b7e168d98d79d8503fc/verovio-6.3.0.tar.gz')
_mac_recipe = host.setup_recipe(target='darwin', machine='arm64')
OFFICIAL_WHEEL = _mac_recipe.official_wheel
OFFICIAL_HASH = _mac_recipe.official_sha256
OFFICIAL_URL = _mac_recipe.official_url
PATCH = ROOT / 'patches/verovio-6.3.0-deferred-tie-stops.patch'
BUILD_REQUIREMENTS = ROOT / 'requirements-renderer-build.txt'
EVENT = None


def status(stage, message):
    if EVENT:
        EVENT('progress', stage=stage, message=message)
    else:
        print(message, flush=True)


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
    try:
        return host.safe_path(path)
    except OSError as exc:
        raise SetupError(str(exc)) from exc


def private_dir(path):
    try:
        host.private_dir(path)
    except OSError as exc:
        raise SetupError(str(exc)) from exc


def write_json(path, value):
    safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.setup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
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
        ('UV_', 'PIP_', 'PYTHON', 'COMPOSERLINT_', 'CMAKE_', 'SKBUILD_', 'DYLD_', 'LD_'))
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
        process = host.popen_owned(command, cwd=ROOT, env=env or clean_env(),
                                   stdin=subprocess.DEVNULL, text=True,
                                   stdout=subprocess.PIPE if capture else log,
                                   stderr=subprocess.PIPE if capture else subprocess.STDOUT)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            host.terminate_owned(process)
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
        raise SetupError('Use CPython 3.12 (COMPOSERLINT_SETUP_PYTHON selects its executable).')
    try:
        selected = host.require_setup()
    except host.UnsupportedPlatformOperation as exc:
        raise SetupError(str(exc)) from exc
    uv = shutil.which('uv')
    if not uv:
        errors.append('Install uv yourself following https://docs.astral.sh/uv/getting-started/installation/.')
    else:
        print('uv:', run([uv, '--version'], capture=True, timeout=15).strip())
    if selected.target == 'linux-x86_64':
        verify_linux_inputs()
        if platform.python_version() != host.linux_pins()['python']['version']:
            errors.append('Linux pilot requires the exact reviewed Python patch version.')
        if uv and run([uv, '--version'], capture=True, timeout=15).split()[:2] != ['uv', host.linux_pins()['uv']['version']]:
            errors.append('Linux pilot requires the exact reviewed uv version.')
    if full:
        if selected.target == 'macos-arm64':
            for command in (['/usr/bin/xcode-select', '-p'], ['/usr/bin/xcrun', '--find', 'clang++']):
                try:
                    print('Apple tools:', run(command, capture=True, timeout=15).strip())
                except SetupError:
                    errors.append('Apple Command Line Tools/SDK missing. Install them manually using xcode-select --install.')
        else:
            compiler = host.linux_pins()['compiler']
            for executable in (compiler['cc'], compiler['cxx']):
                if run([executable, '-dumpfullversion'], capture=True, timeout=15).strip() != compiler['version']:
                    errors.append('Runner compiler differs from the reviewed Linux pilot identity; no automatic replacement.')
        if not shutil.which('patch'):
            errors.append('The patch command is missing; provision Apple Command Line Tools manually.' if selected.target == 'macos-arm64'
                          else 'The reviewed runner must already supply patch; no system installation was attempted.')
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
    selected = host.require_setup()
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
        if selected.target == 'macos-arm64':
            identity['compiler'] = run(['/usr/bin/xcrun', 'clang++', '--version'], capture=True, timeout=15)
            identity['sdk'] = run(['/usr/bin/xcrun', '--show-sdk-version'], capture=True, timeout=15)
        else:
            identity['compiler'] = run([host.linux_pins()['compiler']['cxx'], '--version'], capture=True, timeout=15)
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


def download_artifact(prefix, name, url, expected, offline, *, max_bytes=16 * 1024 * 1024):
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
                if total > max_bytes:
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


def verify_linux_inputs():
    pins = host.linux_pins()
    for name, expected in pins['files_sha256'].items():
        checked(ROOT / name, expected)
    if pins['patch_sha256'] != PATCH_HASH or pins['renderer_source']['sha256'] != SOURCE_HASH:
        raise SetupError('Linux pilot source/patch identity differs from the reviewed renderer.')
    requirements = re.split(r'\n(?=[A-Za-z][A-Za-z0-9_-]*==)', BUILD_REQUIREMENTS.read_text(encoding='utf-8'))
    for artifact in pins['build_tools']:
        block = next((text for text in requirements if text.startswith(artifact['name']+'=='+artifact['version']+' ')), '')
        if artifact['sha256'] not in block:
            raise SetupError('Linux build artifact is absent from existing pinned requirements.')


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
        return host.environment_python(destination)

    def core(self):
        host.require_setup()
        status('dependencies', 'Installing/checking locked ComposerLint dependencies…')
        python = self.environment('runtime')
        env = dict(self.env, UV_PROJECT_ENVIRONMENT=str(self.prefix / 'runtime'))
        # Inexact intentionally retains separately pinned official Verovio.
        # Hatchling is supplied from uv.lock, avoiding unlocked isolated-build deps.
        self.uv_run('sync', '--locked', '--inexact', '--no-install-project', '--no-build', env=env)
        self.uv_run('sync', '--locked', '--inexact', '--no-editable', '--no-build-isolation', env=env)
        return python

    def renderer(self, python):
        selected = host.require_setup()
        status('official_renderer', 'Verifying/installing official Verovio 6.3.0…')
        official = self.prefix / 'official'
        private_dir(official)
        download_artifact(official, selected.official_wheel, selected.official_url, selected.official_sha256, self.offline)
        viewer_requirements = (ROOT / 'src/composerlint/requirements-viewer-linux.txt'
                               if selected.target == 'linux-x86_64' else ROOT / 'requirements-viewer.txt')
        self.uv_run('pip', 'install', '--python', python, '--require-hashes', '--only-binary', ':all:',
                    '--no-index', '--find-links', official, '-r', viewer_requirements)
        manifest = self.prefix / 'renderer.json'
        receipt = self.prefix / 'renderer-build.json'
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            checked(manifest, previous['manifest_sha256'])
            verify_manifest(manifest)
            if inventory(self.prefix / 'renderer') != previous['runtime_files']:
                raise SetupError('Installed renderer file set changed; use a fresh --prefix for recovery.')
            status('renderer_reuse', 'Reusing verified patched renderer; no compilation needed.')
            return manifest
        archive = source_archive(self.prefix, self.offline)
        status('build_tools', 'Provisioning hash-pinned renderer build tools…')
        build_python = self.environment('build-tools')
        if selected.target == 'linux-x86_64':
            tools = self.prefix / 'build-artifacts'
            private_dir(tools)
            for artifact in host.linux_pins()['build_tools']:
                download_artifact(tools, artifact['filename'], artifact['url'], artifact['sha256'],
                                  self.offline, max_bytes=128 * 1024 * 1024)
            self.uv_run('pip', 'sync', '--python', build_python, '--require-hashes', '--only-binary', ':all:',
                        '--no-index', '--find-links', tools, BUILD_REQUIREMENTS)
        else:
            self.uv_run('pip', 'sync', '--python', build_python, '--require-hashes', '--only-binary', ':all:',
                        '--index-url', 'https://pypi.org/simple', BUILD_REQUIREMENTS)
        status('compiling', 'Compiling the exact patched renderer (this can take several minutes)…')
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
            env = dict(self.env, **host.renderer_build_environment(build_python, BUILD))
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
        status('verification', 'Checking analysis, MCP discovery and authored renderer behavior…')
        env = dict(self.env)
        if manifest:
            env['COMPOSERLINT_RENDERER_CONFIG'] = str(manifest)
        run([python, '-I', '-B', ROOT / 'tools/installation_smoke.py'], env=env, log=self.log, timeout=180)
        if manifest:
            env['COMPOSERLINT_RENDERER_EQUIVALENCE'] = '1'
            reports = (['--junitxml=' + str(self.prefix / 'renderer-controls.xml')]
                       if host.require_setup().target == 'linux-x86_64' else [])
            run([python, '-I', '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *reports,
                 ROOT / 'tests/test_renderer_packaging_equivalence.py'], env=env, log=self.log, timeout=180)

    def install(self, full):
        host.require_setup()
        self.log = (self.prefix / 'setup.log').open('a', encoding='utf-8')
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


def describe_plan(mode, offline, prefix):
    selected = host.require_setup()
    print('\nPlan:', 'FULL viewer/editing setup' if mode == 'full' else 'TEXT ANALYSIS ONLY')
    print('Destination (private environments, artifacts, manifest, logs):', prefix)
    print('Install unchanged uv.lock dependencies, including locked pytest/Hatchling for verification/building.')
    print('Exact package versions/download hashes are listed in uv.lock (no dependency upgrades).')
    print('Package source: https://pypi.org/simple and hash-pinned files.pythonhosted.org artifacts.')
    if mode == 'full':
        print('Install official Verovio 6.3.0 in main environment (requirements-viewer.txt hash).')
        print('Official wheel:', selected.official_url)
        print('Install pinned build tools in separate environment:')
        print('build 1.6.1, scikit-build-core 1.1.1, CMake 4.4.4, SWIG 4.5.1, Ninja 1.13.2,')
        print('packaging 26.3, pathspec 1.1.1, pyproject-hooks 1.3.3 (requirements-renderer-build.txt hashes).')
        print('Obtain official source:', SOURCE_URL)
        print('Verify source SHA-256:', SOURCE_HASH)
        print('Apply exact existing patch; compile C++ locally; install separate patched renderer.')
        print('Verify ties, warning refusal and source mapping before creating the local renderer manifest.')
        print('Upstream package resources remain local; no binaries/fonts are redistributed by ComposerLint.')
    print('No Python/uv/Apple-tool installation, administrator access, client-setting edits, or export operations.'
          if selected.target == 'macos-arm64' else
          'No system prerequisite installation, administrator access, client-setting edits, or export operations.')
    print('Network:', 'FORBIDDEN; missing cached materials fail.' if offline else 'approved missing artifacts may be downloaded.')


def consent(mode, offline, prefix):
    describe_plan(mode, offline, prefix)
    return input('Approve these installations/configuration changes? [y/N] ').strip().lower() in {'y', 'yes'}


def protocol_main(mode, offline, base):
    """JSON-lines v1. No writes/downloads before a matching approval on stdin.

    Installer consent only. This endpoint has no MusicXML approval/export powers.
    """
    global EVENT
    output = sys.stdout
    def emit(kind, **fields):
        output.write(json.dumps(dict(protocol=1, type=kind, **fields)) + '\n')
        output.flush()
    previous_event = EVENT
    EVENT = emit
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    prior_signal = signal.signal(signal.SIGTERM, interrupted)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            uv = preflight(mode == 'full')
            identity = recipe(mode == 'full')
            if not base.is_absolute() or '..' in base.parts or not base.parent.is_dir():
                raise SetupError('Installation base must have an existing absolute parent.')
            base = safe_path(base)
            prefix = base / identity[:16]
            with contextlib.redirect_stdout(io.StringIO()) as text:
                describe_plan(mode, offline, prefix)
            plan = dict(plan_id=identity, mode=mode, prefix=str(prefix), description=text.getvalue())
            emit('plan', **plan)
            line = sys.stdin.readline(8193)
            if len(line) > 8192:
                raise SetupError('Oversized consent message.')
            answer = json.loads(line) if line.strip() else {}
            if answer.get('action') == 'cancel' or not answer:
                emit('cancelled', message='Cancelled before installation; no changes made.')
                return 0
            if answer != dict(action='approve', plan_id=identity):
                raise SetupError('Consent does not match the reviewed installation plan.')
            if recipe(mode == 'full') != identity:
                raise SetupError('Installation recipe changed after review; review a fresh plan.')
            os.umask(0o077)
            private_dir(base)
            with owned_prefix(prefix, identity):
                Installer(prefix, uv, offline, identity).install(mode == 'full')
            emit('complete', python=str(host.environment_python(prefix / 'runtime')),
                 manifest=str(prefix / 'renderer.json') if mode == 'full' else None,
                 source=str(ROOT), arguments=['-B', '-m', 'composerlint.server'],
                 log=str(prefix / 'setup.log'))
        return 0
    except KeyboardInterrupt:
        emit('cancelled', message='Setup cancelled. No successful installation is claimed; retry to revalidate retained work.')
        return 130
    except (SetupError, OSError, ValueError, KeyError) as exc:
        emit('error', message=str(exc), fallback='docs/INSTALL.md')
        return 1
    finally:
        EVENT = previous_event
        signal.signal(signal.SIGTERM, prior_signal)


@contextlib.contextmanager
def owned_prefix(prefix, identity):
    safe_path(prefix)
    owner = prefix / 'owner.json'
    if prefix.exists():
        if not owner.is_file() or owner.is_symlink() or json.loads(owner.read_text()) != {'recipe': identity}:
            raise SetupError('Existing destination is unrelated or belongs to a different recipe; choose a fresh --prefix.')
    private_dir(prefix)
    try:
        with host.exclusive_lock(prefix / 'setup.lock'):
            if not owner.exists():
                write_json(owner, {'recipe': identity})
            yield
    except BlockingIOError as exc:
        raise SetupError('Another setup is using this destination; wait for it to finish.') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['full', 'text'], help='otherwise ask interactively')
    parser.add_argument('--check', action='store_true', help='read-only readiness/integrity inspection; no network')
    parser.add_argument('--offline', action='store_true', help='strictly prohibit network acquisition')
    parser.add_argument('--prefix', type=Path, help='new private absolute setup directory (existing parent required)')
    parser.add_argument('--json', action='store_true', help='JSON-lines v1 frontend; waits for plan-bound consent on stdin')
    parser.add_argument('--installation-base', type=Path, help='frontend-owned parent for stable recipe environments')
    args = parser.parse_args(argv)
    if args.json:
        if not args.mode or not args.installation_base or args.prefix or args.check:
            parser.error('--json requires --mode and --installation-base, without --prefix/--check')
        return protocol_main(args.mode, args.offline, args.installation_base)
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
            python = host.environment_python(prefix / 'runtime')
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
