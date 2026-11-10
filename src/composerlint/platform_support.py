"""Small stdlib-only OS boundaries, also loaded directly by the setup backend.

The Linux setup reuses bounded POSIX operations on Ubuntu 24.04 x86-64 and
provisionally on Arch x86-64. Ubuntu CI evidence does not verify Arch desktops.
Native human approval remains macOS-only; Windows operations remain unavailable.
No environment variable or request can enable an unverified security adapter.
"""
from contextlib import contextmanager
import errno
import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import selectors
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
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


def linux_arch_host():
    """Experimental desktop target, separate from the verified Ubuntu CI host."""
    if platform_name() != 'linux' or platform.machine() != 'x86_64' or os.name != 'posix':
        return False
    try:
        return platform.freedesktop_os_release().get('ID') == 'arch'
    except OSError:
        return False


def system_tool(*names):
    """Existing system tools only; never select caller-injected PATH wrappers."""
    for name in names:
        path = shutil.which(name, path='/usr/bin:/bin')
        if path:
            # Preserve argv[0]: clang++ may be a symlink to clang, but selecting
            # its canonical target would lose the C++ standard-library linker.
            return str(Path(path).absolute())
    return None


def compiler_capability(language):
    """Compile AND link authored code. This is not renderer acceptance."""
    sources = {
        'c11': ('c', ('cc', 'gcc', 'clang'),
                '#include <stdatomic.h>\n_Static_assert(sizeof(int)>=4,"int");\n'
                'int main(void){atomic_int n=1;return atomic_load(&n)-1;}\n'),
        'c++20': ('cpp', ('c++', 'g++', 'clang++', 'g++-13'),
                 '#include <concepts>\n#include <span>\n#include <filesystem>\n'
                 'template<std::integral T> T first(std::span<T> x){return x.front();}\n'
                 'int main(){int x[]={0}; auto p=std::filesystem::current_path();'
                 'return first<int>(x)+(p.empty()?1:0);}\n'),
    }
    suffix, names, source = sources[language]
    failures = []
    seen = set()
    for name in names:
        executable = system_tool(name)
        if not executable or executable in seen:
            continue
        seen.add(executable)
        try:
            version = run_owned([executable, '--version'], capture_output=True, timeout=10,
                                text=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
            if version.returncode:
                continue
            with open(executable, 'rb') as stream:
                identity = hashlib.file_digest(stream, 'sha256').hexdigest()
            with tempfile.TemporaryDirectory(prefix='composerlint-compiler-') as temporary:
                source_path = Path(temporary) / ('probe.' + suffix)
                source_path.write_text(source, encoding='ascii')
                result = run_owned([executable, '-std=' + language, str(source_path), '-o',
                                    str(Path(temporary) / 'probe')], capture_output=True, timeout=30,
                                   text=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'TMPDIR': temporary})
                if result.returncode or not (Path(temporary) / 'probe').is_file():
                    failures.append(Path(executable).name + ': compile/link refused')
                    continue
            return dict(available=True, verified=True, language=language, path=executable,
                        version=version.stdout.splitlines()[0][:256], sha256=identity)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(Path(executable).name + ': ' + type(exc).__name__)
    return dict(available=bool(seen), verified=False, language=language, path=None,
                version='', message='; '.join(failures) or 'No existing system compiler found.')


def linux_elf_runtime():
    """Read this running interpreter's ELF loader; no distribution paths assumed."""
    try:
        with open('/proc/self/exe', 'rb') as stream:
            header = stream.read(64)
            if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62:
                return False
            offset = struct.unpack_from('<Q', header, 32)[0]
            size, count = struct.unpack_from('<HH', header, 54)
            if size != 56 or not 0 < count <= 128:
                return False
            for index in range(count):
                stream.seek(offset + index * size)
                entry = stream.read(size)
                if struct.unpack_from('<I', entry)[0] == 3:  # PT_INTERP
                    location = struct.unpack_from('<Q', entry, 8)[0]
                    length = struct.unpack_from('<Q', entry, 32)[0]
                    if not 1 < length <= 4096:
                        return False
                    stream.seek(location)
                    loader = stream.read(length).rstrip(b'\0').decode('utf-8')
                    return loader.startswith('/') and os.access(loader, os.X_OK)
    except (OSError, UnicodeError, struct.error):
        pass
    return False


def linux_abi_capability():
    """Necessary GNU/x64 runtime checks; exact private binaries are checked later."""
    libraries = {}
    # ABI sonames are shared GNU runtime contracts, not distro installation paths.
    for name, library in (('c', 'libc.so.6'), ('m', 'libm.so.6'),
                          ('stdc++', 'libstdc++.so.6'), ('gcc_s', 'libgcc_s.so.1')):
        try:
            ctypes.CDLL(library)
            libraries[name] = True
        except OSError:
            libraries[name] = False
    version = ''
    try:
        libc = ctypes.CDLL('libc.so.6')
        libc.gnu_get_libc_version.restype = ctypes.c_char_p
        version = libc.gnu_get_libc_version().decode('ascii')
    except (AttributeError, OSError, UnicodeError):
        pass
    baseline = bool(re.fullmatch(r'\d+\.\d+', version)) and tuple(map(int, version.split('.'))) >= (2, 17)
    loader = linux_elf_runtime()
    return dict(available=baseline and libraries['c'] and libraries['m'] and ctypes.sizeof(ctypes.c_void_p) == 8 and loader,
                glibc=version, libraries=libraries, elf_loader=loader,
                message='GNU x86-64 / glibc >= 2.17 is a necessary baseline, not proof for the pinned binaries; their execution and imports are checked after verified extraction.')


def linux_display_capability():
    """Isolated GTK connection, not merely DISPLAY/WAYLAND_DISPLAY presence."""
    session = 'Wayland' if os.environ.get('WAYLAND_DISPLAY') else 'X11' if os.environ.get('DISPLAY') else 'none'
    if session == 'none':
        return dict(available=False, session=session, message='No graphical session advertised.')
    code = ('import ctypes,sys; g=ctypes.CDLL("libgtk-4.so.1"); '
            'ctypes.CDLL("libjson-glib-1.0.so.0"); '
            'g.gtk_init_check.restype=ctypes.c_int; '
            'ok=(g.gtk_get_major_version(),g.gtk_get_minor_version()) >= (4,12) and g.gtk_init_check(); '
            'g.gdk_display_get_default.restype=ctypes.c_void_p; '
            'o=ctypes.CDLL("libgobject-2.0.so.0"); '
            'o.g_type_name_from_instance.argtypes=[ctypes.c_void_p]; '
            'o.g_type_name_from_instance.restype=ctypes.c_char_p; '
            'print(o.g_type_name_from_instance(g.gdk_display_get_default()).decode("ascii") if ok else "unavailable"); '
            'sys.exit(0 if ok else 1)')
    env = {key: os.environ[key] for key in ('HOME', 'LANG', 'DISPLAY', 'WAYLAND_DISPLAY',
           'XDG_RUNTIME_DIR', 'XAUTHORITY') if key in os.environ}
    env['PATH'] = '/usr/bin:/bin'
    try:
        result = run_owned([sys.executable, '-I', '-B', '-c', code], capture_output=True,
                           text=True, timeout=10, env=env)
        backend = {'GdkWaylandDisplay': 'Wayland', 'GdkX11Display': 'X11'}.get(result.stdout.strip())
        connected = result.returncode == 0 and backend is not None
    except (OSError, subprocess.TimeoutExpired):
        connected = False
    return dict(available=connected, session=backend if connected else session,
                message='GTK display connection passed.' if connected else 'GTK could not connect to the advertised display; check GTK4, session access and runtime libraries.')


def linux_capabilities(*, renderer=False, frontend=False, graphical=False):
    """Shared report; distro names describe test scope, not compiler selection.

    Only private temporary compiler files are created. Destination write probes
    belong after installation consent. Backend/headless use never requires GTK.
    """
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        release = {}
    target = platform_name() == 'linux' and (linux_pilot_host() or linux_arch_host())
    abi = linux_abi_capability() if target else dict(available=False, glibc='', libraries={})
    tools = {key: dict(available=bool(system_tool(name)), version='')
             for key, name in (('cc', 'cc'), ('patch', 'patch'), ('findmnt', 'findmnt'), ('system_uv', 'uv'))}
    tools['cxx'] = compiler_capability('c++20') if renderer and target else dict(available=False, verified=False, version='')
    if frontend and target:
        tools['cc'] = compiler_capability('c11')
    display = linux_display_capability() if graphical and target else dict(available=False, session='not checked')
    supported = target and os.geteuid() != 0 and abi['available'] and tools['findmnt']['available']
    if graphical:
        supported = supported and display['available']
    errors = []
    if not target:
        errors.append('Target is outside Ubuntu 24.04 x86-64 / experimental Arch x86-64.')
    if os.geteuid() == 0:
        errors.append('Run as a normal user, without sudo.')
    if not abi['available']:
        errors.append('GNU x64 ABI, ELF loader or required runtime libraries could not be verified.')
    if not tools['findmnt']['available']:
        errors.append('findmnt (util-linux) is missing; filesystem admission cannot be verified.')
    if graphical and not display['available']:
        errors.append(display.get('message', 'GTK display connection unavailable.'))
    return dict(schema=1, supported=bool(supported), distro=release.get('ID', 'unknown'),
                distro_version=release.get('VERSION_ID', 'rolling'), architecture=platform.machine(),
                system_python=platform.python_version(), abi=abi, display=display, errors=errors,
                session=display['session'], tools=tools,
                full_available=bool(supported and tools['cxx']['verified'] and tools['cc']['available'] and tools['patch']['available'] and all(abi['libraries'].values())),
                frontend_available=bool(supported and frontend and tools['cc'].get('verified')),
                export_available=False)


_LINUX_POSIX_OPERATIONS = frozenset({
    'Private filesystem access', 'Protected score-file reading', 'Exclusive setup locking',
    'Safe MusicXML export', 'Protected export reading', 'Exclusive export staging',
    'Exclusive export publication', 'Private editing workspaces', 'Owned workspace cleanup',
    'Owned process-tree lifecycle', 'Owned process-tree termination',
    'Worker parent-death monitoring', 'Worker pipe transport',
})


def require_security(operation):
    if platform_name() != 'darwin' and not (operation in _LINUX_POSIX_OPERATIONS and (linux_pilot_host() or linux_arch_host())):
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


def require_installation_filesystem(path, *, exercise=False):
    """Linux installation storage policy; never substitute a different location."""
    if platform_name() != 'linux':
        return
    require_security('Private filesystem access')
    try:
        findmnt = system_tool('findmnt')
        if not findmnt:
            raise FileNotFoundError('findmnt')
        result = subprocess.run([findmnt,'-n','-o','FSTYPE','-T',str(path)],
                                capture_output=True,text=True,timeout=10)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise UnsupportedPlatformOperation('Linux installation needs an existing findmnt (util-linux) to verify its local filesystem.') from exc
    if result.returncode or result.stdout.strip() not in ('ext4','btrfs'):
        raise UnsupportedPlatformOperation('Linux installation requires local ext4 or Btrfs; network, FAT, overlay and unknown filesystems are refused.')
    filesystem = result.stdout.strip()
    if exercise:
        private_dir(path)
        with tempfile.TemporaryDirectory(prefix='filesystem-probe-', dir=path) as temporary:
            directory = Path(temporary)
            private_dir(directory)
            with exclusive_lock(directory / 'lock'):
                try:
                    with exclusive_lock(directory / 'lock'):
                        raise UnsupportedPlatformOperation('Exclusive filesystem locking is unavailable.')
                except BlockingIOError:
                    pass
            fd = export_directory(directory, {directory})
            try:
                write_at(fd, 'stage', b'ComposerLint filesystem probe')
                publish_exclusive(fd, 'stage', 'published')
                if read_at(fd, 'published') != b'ComposerLint filesystem probe':
                    raise UnsupportedPlatformOperation('Descriptor-relative filesystem verification failed.')
                try:
                    publish_exclusive(fd, 'stage', 'published')
                    raise UnsupportedPlatformOperation('Existing-name collision protection is unavailable.')
                except FileExistsError:
                    pass
                os.fsync(fd)
            finally:
                os.close(fd)
            (directory / 'link').symlink_to('published')
            os.mkfifo(directory / 'fifo')
            for name in ('link', 'fifo'):
                try:
                    with open_regular(directory / name):
                        raise UnsupportedPlatformOperation('Unsafe-file protection is unavailable.')
                except UnsupportedPlatformOperation:
                    raise
                except OSError:
                    pass
    return filesystem


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
        return SetupRecipe('linux-x86_64', linux_pilot_host() or linux_arch_host(), 'bin/python',
                           pins['filename'], pins['url'], pins['sha256'])
    return SetupRecipe('unverified-' + target + '-' + machine, False,
                       'Scripts/python.exe' if target == 'win32' else 'bin/python')


def require_setup():
    recipe = setup_recipe()
    if not recipe.installable:
        raise UnsupportedPlatformOperation(
            f'Only native macOS Apple Silicon, Ubuntu 24.04 x86-64 and experimental Arch x86-64 are enabled; {recipe.target} '
            'is unverified. Native adapters, artifact pins and prerequisites require separate approval.')
    return recipe


def environment_python(directory):
    return Path(directory) / setup_recipe().python_relative


def renderer_build_environment(build_python, build):
    recipe = require_setup()
    if recipe.target == 'linux-x86_64':
        report = linux_capabilities(renderer=True)
        if not report['full_available']:
            raise UnsupportedPlatformOperation('Linux renderer requires the shared capability checks, including C++20 compilation and linking.')
        return dict(PATH=str(Path(build_python).parent) + ':/usr/bin:/bin',
                    VEROVIO_BUILD_VERSION=build, CMAKE_BUILD_PARALLEL_LEVEL='2',
                    CMAKE_GENERATOR='Ninja', CC=system_tool('cc', 'gcc', 'clang'), CXX=report['tools']['cxx']['path'])
    return dict(PATH=str(Path(build_python).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                VEROVIO_BUILD_VERSION=build, MACOSX_DEPLOYMENT_TARGET='13',
                CMAKE_BUILD_PARALLEL_LEVEL='4', CMAKE_GENERATOR='Ninja',
                CC='/usr/bin/clang', CXX='/usr/bin/clang++')


def linux_pins():
    return json.loads(Path(__file__).with_name('linux-pins.json').read_text(encoding='utf-8'))
