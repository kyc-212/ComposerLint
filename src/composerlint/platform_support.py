"""Small stdlib-only OS boundaries, also loaded directly by the setup backend.

The Linux CI pilot reuses bounded POSIX operations on Ubuntu 24.04 x86-64.
Native human approval remains macOS-only; Windows operations remain unavailable.
No environment variable or request can enable an unverified security adapter.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import NamedTuple


class UnsupportedPlatformOperation(OSError):
    pass


def platform_name():
    return sys.platform


def linux_pilot_host():
    if platform_name() != 'linux' or platform.machine() != 'x86_64' or os.name != 'posix':
        return False
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return False
    return release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '24.04'


_LINUX_POSIX_OPERATIONS = frozenset({
    'Private filesystem access', 'Protected score-file reading', 'Exclusive setup locking',
    'Safe MusicXML export', 'Protected export reading', 'Exclusive export staging',
    'Exclusive export publication', 'Private editing workspaces', 'Owned workspace cleanup',
    'Owned process-tree lifecycle', 'Owned process-tree termination',
    'Worker parent-death monitoring', 'Worker pipe transport',
})


def require_security(operation):
    if platform_name() != 'darwin' and not (operation in _LINUX_POSIX_OPERATIONS and linux_pilot_host()):
        raise UnsupportedPlatformOperation(
            f'{operation} is unavailable on {platform_name()}: native security '
            'adapter verification is pending for this capability/target.')


def local_path_text(value, *, target=None):
    """Validate local path syntax without touching disk or rewriting source names."""
    raw = os.fspath(value)
    if not isinstance(raw, str) or '\x00' in raw:
        raise ValueError('A local text file path without NUL is required.')
    target = platform_name() if target is None else target
    if target != 'win32':
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', raw) or raw.startswith('//'):
            raise ValueError('nonlocal sources are not supported')
        return raw
    # Initial Windows scope: fully qualified local drives, never UNC/device paths.
    path = PureWindowsPath(raw)
    if not re.match(r'^[A-Za-z]:[\\/]', raw) or path.drive != raw[:2] or not path.root:
        raise ValueError('Use an absolute local Windows drive path; URLs, UNC and drive-relative paths are unsupported.')
    parts = re.split(r'[\\/]', raw[3:])
    reserved = re.compile(r'^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$', re.I)
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith(('.', ' '))
                or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or reserved.match(part.split('.', 1)[0].rstrip(' '))):
            raise ValueError('Unsafe or ambiguous Windows path component.')
    return raw


def safe_path(path):
    """Existing Mac setup policy: reject links in every writable ancestor."""
    require_security('Private filesystem access')
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise OSError(f'Symlinked setup destination refused: {parent}')
    return path


def private_dir(path):
    safe_path(path)
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise OSError(f'Setup directory must be owned by you and private (mode 700): {path}')


def open_regular(path):
    """No-follow, nonblocking read; refuse FIFOs/devices before reading."""
    require_security('Protected score-file reading')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, 'A regular non-symlink file is required.')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def exclusive_lock(path):
    require_security('Exclusive setup locking')
    import fcntl
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise OSError('Setup lock must be a private regular file owned by you.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def export_directory(root, creatable):
    """Walk held directory descriptors from /, rejecting every symlink."""
    require_security('Safe MusicXML export')
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts:
        raise OSError('Export requires an absolute path without parent traversal.')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    current = Path('/')
    try:
        for part in root.parts[1:]:
            current = current / part
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if current not in creatable and current != root:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _basename(name):
    if not isinstance(name, str) or name in ('', '.', '..') or any(c in name for c in '/\\\x00'):
        raise OSError('A single safe filename is required for descriptor-relative access.')


def read_at(fd, name, limit=10 * 1024 * 1024 + 1):
    require_security('Protected export reading')
    _basename(name)
    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError('Export is not a regular file.')
        return stream.read(limit)


def write_at(fd, name, raw):
    require_security('Exclusive export staging')
    _basename(name)
    with os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def publish_exclusive(fd, stage, name):
    require_security('Exclusive export publication')
    _basename(stage)
    _basename(name)
    os.link(stage, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)


def popen_owned(command, **kwargs):
    require_security('Owned process-tree lifecycle')
    if isinstance(command, (str, bytes)) or kwargs.get('shell') or kwargs.get('start_new_session') is False:
        raise ValueError('Owned workers require an argument array, no shell, and an isolated session.')
    kwargs['start_new_session'] = True
    if kwargs.get('text') and 'encoding' not in kwargs:
        kwargs['encoding'] = 'utf-8'
    process = subprocess.Popen(command, **kwargs)
    process._composerlint_owned_group = True
    return process


def terminate_owned(process):
    """Kill only a session created here, including descendants retaining pipes."""
    require_security('Owned process-tree termination')
    if not getattr(process, '_composerlint_owned_group', False) or process.pid == os.getpgrp():
        raise ValueError('Refusing termination of a process group not owned by this adapter.')
    # Once reaped, its PID/group number could be reused. Do not signal a stale
    # number. Before reap, even an exited leader retains its PID while remaining
    # descendants may hold pipes, so cancellation can still close that group.
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_owned(command, *, input=None, timeout=None, capture_output=False, **kwargs):
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process = popen_owned(command, **kwargs)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
    except BaseException:
        terminate_owned(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def watch_parent(*, group=False):
    """POSIX child-side death watch, usable before application imports."""
    require_security('Worker parent-death monitoring')
    parent = os.getppid()
    def watch():
        while True:
            time.sleep(.5)
            if os.getppid() != parent:
                if group and os.getpgrp() == os.getpid():
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                else:
                    os.kill(os.getpid(), signal.SIGKILL)
    threading.Thread(target=watch, daemon=True).start()


class PipeLimitError(ValueError):
    pass


def exchange_line(process, payload, deadline, *, max_bytes=64 * 1024 * 1024):
    """Existing bounded Unix pipe transport; Windows transport is not enabled."""
    require_security('Worker pipe transport')
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    offset = 0
    output, errors = bytearray(), bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('owned passage worker', 0)
            for event, _ in selector.select(remaining):
                stream = event.fileobj
                if stream is process.stdin:
                    offset += os.write(stream.fileno(), payload[offset:])
                    if offset == len(payload):
                        selector.unregister(stream)
                    continue
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('Owned passage worker exited: ' + errors.decode('utf-8', errors='replace')[-4000:])
                if stream is process.stderr:
                    errors.extend(chunk)
                    errors[:] = errors[-4000:]
                    continue
                output.extend(chunk)
                if len(output) > max_bytes:
                    raise PipeLimitError('Passage worker exceeded existing transport budget.')
                if output.endswith(b'\n'):
                    return bytes(output)


def approval_method():
    return 'native_osascript_dialog' if platform_name() == 'darwin' else None


def confirm_export(message):
    if approval_method() is None:
        return False
    # Message is an argument, never executable script source. Reject stays default.
    script = '''on run argv
set answer to display dialog (item 1 of argv) with title "ComposerLint: approve exact candidate" buttons {"Reject", "Approve & Export"} default button "Reject" cancel button "Reject" giving up after 300
if gave up of answer then return "expired"
return button returned of answer
end run'''
    try:
        result = subprocess.run(['/usr/bin/osascript', '-e', script, message],
            capture_output=True, text=True, encoding='utf-8', timeout=310)
        return result.returncode == 0 and result.stdout.strip() == 'Approve & Export'
    except (OSError, subprocess.TimeoutExpired):
        return False


class SetupRecipe(NamedTuple):
    target: str
    installable: bool
    python_relative: str
    official_wheel: str | None = None
    official_url: str | None = None
    official_sha256: str | None = None


def setup_recipe(*, target=None, machine=None):
    target = platform_name() if target is None else target
    machine = platform.machine() if machine is None else machine
    if target == 'darwin' and machine == 'arm64':
        wheel = 'verovio-6.3.0-cp310-abi3-macosx_13_0_arm64.whl'
        return SetupRecipe('macos-arm64', True, 'bin/python', wheel,
            'https://files.pythonhosted.org/packages/6b/0b/42e560df4051b8c05407162ba117eb14b44f011e84818acc4c2065161d87/' + wheel,
            'c842f5d21d31c3d47e835e07565a9b8623b78feef527affe149ceadd9ecaa55a')
    if target == 'win32' and machine.lower() in ('amd64', 'x86_64'):
        return SetupRecipe('windows-x64', False, 'Scripts/python.exe')
    if target == 'linux' and machine == 'x86_64':
        pins = linux_pins()['official_renderer']
        return SetupRecipe('linux-x86_64', linux_pilot_host(), 'bin/python',
                           pins['filename'], pins['url'], pins['sha256'])
    return SetupRecipe('unverified-' + target + '-' + machine, False,
                       'Scripts/python.exe' if target == 'win32' else 'bin/python')


def require_setup():
    recipe = setup_recipe()
    if not recipe.installable:
        raise UnsupportedPlatformOperation(
            f'Only native macOS Apple Silicon and the Ubuntu 24.04 x86-64 pilot are enabled; {recipe.target} '
            'is unverified. Native adapters, artifact pins and prerequisites require separate approval.')
    return recipe


def environment_python(directory):
    return Path(directory) / setup_recipe().python_relative


def renderer_build_environment(build_python, build):
    recipe = require_setup()
    if recipe.target == 'linux-x86_64':
        compiler = linux_pins()['compiler']
        return dict(PATH=str(Path(build_python).parent) + ':/usr/bin:/bin',
                    VEROVIO_BUILD_VERSION=build, CMAKE_BUILD_PARALLEL_LEVEL='2',
                    CMAKE_GENERATOR='Ninja', CC=compiler['cc'], CXX=compiler['cxx'])
    return dict(PATH=str(Path(build_python).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                VEROVIO_BUILD_VERSION=build, MACOSX_DEPLOYMENT_TARGET='13',
                CMAKE_BUILD_PARALLEL_LEVEL='4', CMAKE_GENERATOR='Ninja',
                CC='/usr/bin/clang', CXX='/usr/bin/clang++')


def linux_pins():
    return json.loads(Path(__file__).with_name('linux-pins.json').read_text(encoding='utf-8'))
