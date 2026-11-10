"""Graphical bootstrap/protocol bridge; the shared Installer remains the only installer.

Uses system Python >=3.12 only to prepare the reviewed private Python/uv. No GTK
bindings, client settings, score access, package-manager calls or export approval.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request

MAX_EVENT = 128 * 1024
SKILLS = ('composer-import', 'composer-review', 'composer-edit')


def digest(path):
    with regular(path) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('A regular, non-symlink resource is required.')
        # Nonblocking admission prevents FIFO hangs. Once the held descriptor is
        # known regular, blocking reads also satisfy Python 3.14 file_digest.
        os.set_blocking(fd, True)
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            yield stream
    finally:
        if fd is not None:
            os.close(fd)


def safe_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute local path without parent traversal is required.')
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Symlinked setup destinations are refused.')
    return path


def private_dir(path):
    path = safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Setup folders must be private and owned by the current user.')
    return path


def inventory(directory, *, links=False):
    safe_path(directory)
    result = {}
    for path in sorted(directory.rglob('*')):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            if not links or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError('An unexpected or escaping resource link was refused.')
            result[name] = {'link': os.readlink(path)}
        elif path.is_file():
            result[name] = {'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
        elif not path.is_dir():
            raise ValueError('Unexpected special resource file.')
    return result


def verify_bundle(bundle, expected):
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Missing compiled resource identity.')
    manifest = bundle / 'resources/manifest.json'
    if digest(manifest) != expected:
        raise ValueError('Setup resources changed; use a fresh verified ZIP.')
    with regular(manifest) as stream:
        entries = json.load(stream)
    actual = {}
    for subtree in ('resources', 'Source'):
        for name, value in inventory(bundle / subtree).items():
            full = subtree + '/' + name
            if full != 'resources/manifest.json':
                actual[full] = value
    if actual != entries:
        raise ValueError('Bundled source/resources differ from the reviewed manifest.')
    source = bundle / 'Source'
    for name in SKILLS:
        if not (source / '.agents/skills' / name / 'SKILL.md').is_file():
            raise ValueError('Bundled Skill discovery resources are incomplete.')
    spec = importlib.util.spec_from_file_location('graphical_shared_setup', source / 'tools/setup_composerlint.py')
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    setup.verify_linux_inputs()
    return entries, setup


def data_root():
    value = os.environ.get('XDG_DATA_HOME')
    if value and not Path(value).is_absolute():
        raise ValueError('XDG_DATA_HOME must be absolute.')
    return safe_path((Path(value) if value else Path.home() / '.local/share') / 'ComposerLint')


def probe(setup):
    info = setup.host.linux_capabilities(renderer=True, graphical=True)
    pins = setup.host.linux_pins()
    tools = info['tools']
    compiler = tools['cxx']
    info['message'] = (
        ('\n'.join(info['errors']) + '\n\n' if info['errors'] else '') +
        f"Target: {info['distro']} {info['distro_version']} / {info['architecture']} / {info['session']}\n"
        f"GNU ABI: glibc {info['abi']['glibc'] or 'unavailable'}; ELF loader: {info['abi'].get('elf_loader', False)}; runtime libraries: {info['abi']['libraries']}.\n"
        f"Display: {info['display'].get('message', 'not checked')}\n"
        f"Renderer C++20 compile/link: {'passed' if compiler['verified'] else compiler.get('message', 'unavailable')}.\n"
        f"Compiler: {compiler['version'] or 'not selected'}. patch: {tools['patch']['available']}; findmnt: {tools['findmnt']['available']}.\n"
        f"System Python: {info['system_python']}. Private runtime: Python {pins['python']['version']}, uv {pins['uv']['version']}.\n\n"
        "Full: analysis, notation and verified temporary editing. Text only: analysis without rendering; no compiler needed.\n"
        "Compiler probes do not establish renderer compatibility: the exact patched build and all 10 controls must pass.\n"
        "Linux human-approved export remains disabled. Ubuntu is the primary automated target; Arch/Niri desktop acceptance is pending.\n"
        "Only Ubuntu 24.04 x86-64 and experimental Arch x86-64 are admitted. GNU ABI/library checks are preliminary; pinned binaries and imports are verified after approved downloads.\n"
        "No system packages, root access, shell-profile edits or AI-client settings are changed. Missing prerequisites must be reviewed and installed separately by the computer owner.")
    return info


def bootstrap_plan(bundle, manifest_hash, root, mode, pins):
    artifacts = {key: pins[key] for key in ('python', 'uv')}
    description = f"Prepare private tools and a stable source copy under:\n{root}\n\n"
    for name, item in artifacts.items():
        description += f"{name} {item['version']}\n{item['url']}\nSHA-256: {item['sha256']}\n\n"
    description += ("Download only missing, hash-verified archives from official Astral releases. "
        "Reuse retained files only after rechecking their integrity.\n"
        "Check local filesystem privacy, locks and exclusive publication in a fresh temporary probe folder.\n"
        "Copy reviewed source and Skills into stable per-user storage, so removing this extracted app will not break the installation.\n"
        "A second approval will cover dependencies and the renderer. This consent never authorizes score export.")
    identity = hashlib.sha256(json.dumps(dict(manifest=manifest_hash, root=str(root), mode=mode,
        artifacts=artifacts), sort_keys=True).encode()).hexdigest()
    return dict(plan_id=identity, description=description, artifacts=artifacts)


def check_private_filesystem(root, host):
    """Same conservative admission and active probes as the terminal backend."""
    return host.require_installation_filesystem(root, exercise=True)


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {
                'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ValueError('Unreviewed bootstrap redirect refused.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def bootstrap(artifact, root, emit):
    base = private_dir(root / 'setup/bootstrap' / artifact['sha256'])
    archive = base / 'archive.tar.gz'
    if archive.exists() or archive.is_symlink():
        if archive.is_symlink() or archive.stat().st_size != artifact['size'] or digest(archive) != artifact['sha256']:
            raise ValueError('Retained bootstrap archive changed; no automatic replacement.')
    else:
        parsed = urllib.parse.urlsplit(artifact['url'])
        if parsed.scheme != 'https' or parsed.hostname != 'github.com':
            raise ValueError('Unreviewed bootstrap origin.')
        partial = base / 'archive.download'
        # Unknown leftovers are preserved; never truncate a pre-existing file/link.
        created = False
        try:
            with partial.open('xb') as stream:
                created = True
                with urllib.request.build_opener(Redirects).open(artifact['url'], timeout=30) as response:
                    total, last = 0, 0
                    started = time.monotonic()
                    while chunk := response.read(1024 * 1024):
                        if time.monotonic() - started > 15 * 60:
                            raise ValueError('Prerequisite download exceeded 15 minutes; retry after checking the connection.')
                        total += len(chunk)
                        if total > min(artifact['size'], 128 * 1024 * 1024):
                            raise ValueError('Download exceeded its reviewed size.')
                        stream.write(chunk)
                        if total - last >= 4 * 1024 * 1024:
                            emit('progress', stage='bootstrap', message=f"Downloading {artifact['version']}: {total // 1048576} MiB")
                            last = total
            if total != artifact['size'] or digest(partial) != artifact['sha256']:
                raise ValueError('Downloaded prerequisite failed size/hash verification.')
            os.link(partial, archive, follow_symlinks=False)
        finally:
            if created:
                partial.unlink(missing_ok=True)
    verified = base / 'verified'
    files, receipt = verified / 'files', verified / 'files.json'
    if verified.exists() or verified.is_symlink():
        safe_path(verified)
        with regular(receipt) as stream:
            expected = json.load(stream)
        if not expected or inventory(files, links=True) != expected:
            raise ValueError('Retained private tool changed; preserve it and choose a fresh setup location.')
    else:
        with tempfile.TemporaryDirectory(prefix='extract-', dir=base) as temporary:
            container = Path(temporary)
            stage = container / 'files'
            stage.mkdir(mode=0o700)
            with tarfile.open(archive, 'r:gz') as stream:
                members = stream.getmembers()
                names = set()
                if len(members) > 50000 or sum(m.size for m in members) > 512 * 1024 * 1024:
                    raise ValueError('Bootstrap archive exceeds extraction limits.')
                for member in members:
                    path = PurePosixPath(member.name)
                    if (path.is_absolute() or '..' in path.parts or not path.parts
                            or path.parts[0] != artifact['archive_root'] or '\\' in member.name
                            or member.name in names or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                        raise ValueError('Unsafe bootstrap archive member.')
                    names.add(member.name)
                stream.extractall(stage, members=members, filter='data')
            records = inventory(stage, links=True)
            if not records:
                raise ValueError('Empty extracted prerequisite.')
            # Files and their receipt become visible together. An interrupted
            # extraction never creates an apparently reusable cache.
            with (container / 'files.json').open('x') as stream:
                json.dump(records, stream, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            container.rename(verified)
    executable = files / artifact['executable']
    if not executable.resolve().is_relative_to(files.resolve()) or not executable.is_file():
        raise ValueError('Private executable is missing or escapes its verified directory.')
    return executable


def prepare_source(bundle, entries, root, identity):
    parent = private_dir(root / 'setup/sources')
    destination = parent / identity
    expected = {name.removeprefix('Source/'): value for name, value in entries.items() if name.startswith('Source/')}
    if destination.exists() or destination.is_symlink():
        safe_path(destination)
        if inventory(destination) != expected:
            raise ValueError('Retained source changed; refusing to overwrite it.')
        return destination
    with tempfile.TemporaryDirectory(prefix='source-', dir=parent) as temporary:
        stage = Path(temporary)
        for name, record in expected.items():
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name:
                raise ValueError('Unsafe source manifest member.')
            target = stage / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with regular(bundle / 'Source' / name) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(record['mode'])
        if inventory(stage) != expected:
            raise ValueError('Copied source failed integrity verification.')
        stage.rename(destination)
    return destination


def emit(kind, **fields):
    print(json.dumps(dict(protocol=1, type=kind, **fields)), flush=True)


def reply(timeout=600):
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise ValueError('Approval expired; review a fresh plan.')
        line = sys.stdin.buffer.readline(8193)
    if len(line) > 8192:
        raise ValueError('Oversized approval message.')
    return json.loads(line) if line.strip() else {'action': 'cancel'}


def stop_backend(process, host):
    if process.poll() is None:
        process.terminate()  # Backend SIGTERM handler reaps its build groups.
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            host.terminate_owned(process)
            process.wait(timeout=5)


def bridge(source, python, uv, root, mode, host):
    base = root / 'setup/installations'
    environment = {key: os.environ[key] for key in ('HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE') if key in os.environ}
    environment.update(PATH=str(uv.parent) + ':/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1')
    process = host.popen_owned([str(python), '-I', '-B', str(source / 'tools/setup_composerlint.py'),
        '--json', '--mode', mode, '--installation-base', str(base)], env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    plan, approved, completed = None, False, False
    buffers = {process.stdout: bytearray(), process.stderr: bytearray(), sys.stdin.buffer: bytearray()}
    deadline = time.monotonic() + 40 * 60
    try:
        with selectors.DefaultSelector() as selector:
            for stream in buffers:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Setup exceeded its bounded deadline.')
                for event, _ in selector.select(min(remaining, 1)):
                    stream = event.fileobj
                    chunk = os.read(stream.fileno(), 4096)
                    if not chunk:
                        if stream is process.stdout and buffers[stream]:
                            raise ValueError('Truncated backend protocol event.')
                        selector.unregister(stream)
                        if stream is sys.stdin.buffer and not completed:
                            raise KeyboardInterrupt()
                        continue
                    buffer = buffers[stream]
                    buffer.extend(chunk)
                    if stream is process.stderr:
                        # Raw backend logs remain local and are not diagnostic exports.
                        sys.stderr.buffer.write(buffer); sys.stderr.buffer.flush(); buffer.clear()
                        continue
                    if len(buffer) > MAX_EVENT:
                        raise ValueError('Oversized protocol event.')
                    while b'\n' in buffer:
                        line, _, rest = buffer.partition(b'\n'); buffer[:] = rest
                        value = json.loads(line)
                        if stream is sys.stdin.buffer:
                            if value.get('action') == 'cancel':
                                raise KeyboardInterrupt()
                            if not plan or approved or value != {'action':'approve', 'plan_id':plan['plan_id']}:
                                raise ValueError('Approval does not match the active backend plan.')
                            process.stdin.write(line + b'\n'); process.stdin.flush(); approved = True
                        else:
                            if value.get('protocol') != 1:
                                raise ValueError('Unexpected backend protocol.')
                            kind = value.get('type')
                            if kind == 'plan':
                                if plan or not re.fullmatch('[0-9a-f]{64}', value['plan_id']) or value['mode'] != mode or Path(value['prefix']) != base / value['plan_id'][:16]:
                                    raise ValueError('Unexpected backend installation plan.')
                                plan = value
                            elif kind == 'complete':
                                expected_manifest = str(Path(plan['prefix']) / 'renderer.json') if mode == 'full' else None
                                if not approved or completed or value['source'] != str(source) or value['python'] != str(Path(plan['prefix']) / 'runtime/bin/python') or value['arguments'] != ['-B','-m','composerlint.server'] or value['manifest'] != expected_manifest or value['log'] != str(Path(plan['prefix']) / 'setup.log'):
                                    raise ValueError('Unexpected backend completion.')
                                completed = True
                                value['instructions'] = connection(value, source, mode)
                            elif kind not in ('progress', 'error', 'cancelled'):
                                raise ValueError('Unknown backend event.')
                            emit(kind, **{k:v for k,v in value.items() if k not in ('protocol','type')})
                if process.poll() is not None and not any(key.fileobj in (process.stdout, process.stderr) for key in selector.get_map().values()):
                    break
        code = process.wait(timeout=10)
        if code or not completed:
            raise ValueError('Backend did not verify a completed installation; retained work is preserved.')
    finally:
        stop_backend(process, host)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def connection(value, source, mode):
    result = f"Command:\n{value['python']}\n\nArguments:\n-B -m composerlint.server\n"
    if value['manifest']:
        result += '\nServer environment:\nCOMPOSERLINT_RENDERER_CONFIG=' + value['manifest'] + '\n'
    result += (f"\nStable source and Skills:\n{source}\n\n"
        "Open this folder in your local AI client and configure the stdio server above. Reconnect and verify ComposerLint 0.2.0 / 18 tools.\n"
        "Project Skills: composer-import, composer-review, composer-edit (.agents/skills). Client-specific discovery and live browser selection need separate testing.\n\n"
        "The downloaded setup folder may now be removed. Keep the stable installation folder. No private client settings were changed.\n"
        "Linux permanent MusicXML export remains disabled.\n")
    result += ("Verified temporary editing requires the full verified renderer."
        if mode == 'full' else
        "Text analysis only: this choice did not install a renderer. Notation and editing previews require a separately verified renderer; existing settings were preserved.")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--mode', choices=('full','text'), default='full')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    def interrupt(*unused):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    # Cover preflight/compiler/display probes as well as approved installation.
    parent = os.getppid()
    def parent_watch():
        while True:
            time.sleep(.5)
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGTERM); return
    threading.Thread(target=parent_watch, daemon=True).start()
    try:
        if sys.version_info < (3,12):
            raise ValueError('System Python 3.12+ is needed for graphical prerequisite preparation.')
        entries, setup = verify_bundle(args.bundle, args.manifest_sha256)
        info = probe(setup)
        if args.probe:
            emit('probe', **info)
            return 0
        if not info['supported'] or args.mode == 'full' and not info['full_available']:
            raise ValueError(info['message'])
        root = data_root()
        pins = setup.host.linux_pins()
        plan = bootstrap_plan(args.bundle, args.manifest_sha256, root, args.mode, pins)
        emit('bootstrap_plan', **plan)
        answer = reply()
        if answer.get('action') == 'cancel':
            raise KeyboardInterrupt()
        if answer != dict(action='approve_bootstrap', plan_id=plan['plan_id']):
            raise ValueError('Prerequisite approval differs from the displayed plan.')
        private_dir(root); setup_dir = private_dir(root / 'setup')
        with setup.host.exclusive_lock(setup_dir / 'graphical.lock'):
            filesystem = check_private_filesystem(root, setup.host)
            emit('progress', stage='filesystem', message=f'Private {filesystem} filesystem checks passed.')
            emit('progress', stage='source', message='Verifying and preparing stable source and Skills…')
            source = prepare_source(args.bundle, entries, root, args.manifest_sha256)
            python = bootstrap(pins['python'], root, emit)
            uv = bootstrap(pins['uv'], root, emit)
            for command, expected in (([str(python),'-I','-B','-c','import platform;print(platform.python_version(),platform.machine())'], '3.12.13 x86_64'), ([str(uv),'--version'], 'uv 0.12.3')):
                cp = setup.host.run_owned(command, capture_output=True, text=True, timeout=15)
                actual = cp.stdout.strip() if cp.returncode == 0 else ''
                if actual != expected and not (expected.startswith('uv ') and actual.startswith(expected+' ')):
                    raise ValueError('Private prerequisite executable identity differs from the reviewed pin.')
            bridge(source, python, uv, root, args.mode, setup.host)
        return 0
    except KeyboardInterrupt:
        emit('cancelled', message='Setup cancelled. Existing working installations are preserved; retry rechecks retained work.')
        return 130
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        emit('error', message=str(error), error_code=type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
