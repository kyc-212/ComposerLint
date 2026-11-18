"""Find and run a local MuseScore Studio 4 converter on Windows."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

from .platform_support import local_path_text


@dataclass(frozen=True)
class MuseScore:
    executable: Path
    version: str


def file_version(path):
    # MuseScore documents that --version is unavailable on Windows.
    api = ctypes.WinDLL('version.dll', use_last_error=True, winmode=0x800)
    size = api.GetFileVersionInfoSizeW
    size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    size.restype = wintypes.DWORD
    read = api.GetFileVersionInfoW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    read.restype = wintypes.BOOL
    query = api.VerQueryValueW
    query.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID),
                      ctypes.POINTER(wintypes.UINT)]
    query.restype = wintypes.BOOL

    name = str(path)
    length = size(name, None)
    if not 0 < length <= 1024 * 1024:
        return None
    buffer = ctypes.create_string_buffer(length)
    value, count = wintypes.LPVOID(), wintypes.UINT()
    if not read(name, 0, length, buffer) or not query(buffer, '\\', ctypes.byref(value), ctypes.byref(count)):
        return None
    if count.value < 13 * ctypes.sizeof(wintypes.DWORD):
        return None
    magic, _, high, low = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD * 4)).contents
    if magic != 0xFEEF04BD or high >> 16 != 4:
        return None
    return '.'.join(map(str, (high >> 16, high & 0xffff, low >> 16, low & 0xffff)))


def candidates():
    folders = ('MuseScore 4', 'MuseScore Studio 4', 'MuseScore Studio')
    bases = [Path(os.environ[key]) for key in ('ProgramW6432', 'ProgramFiles') if os.environ.get(key)]
    if os.environ.get('LOCALAPPDATA'):
        bases.append(Path(os.environ['LOCALAPPDATA']) / 'Programs')
    for base in dict.fromkeys(bases):
        for folder in folders:
            yield base / folder / 'bin' / 'MuseScore4.exe'
    if candidate := shutil.which('MuseScore4.exe'):
        yield Path(candidate)


def find_musescore():
    configured = os.environ.get('COMPOSERLINT_MUSESCORE')
    for candidate in (Path(configured),) if configured else candidates():
        try:
            path = Path(local_path_text(str(candidate), target='win32'))
        except ValueError:
            continue
        if path.name.lower() != 'musescore4.exe' or not path.is_file():
            continue
        try:
            version = file_version(path)
        except OSError:
            continue
        if version:
            return MuseScore(path, version)
    if configured:
        raise ValueError('COMPOSERLINT_MUSESCORE must point to a local MuseScore Studio 4 MuseScore4.exe.')
    return None


def convert(converter, source, output, stage):
    """Use an owned process and a private profile for the converter."""
    from .windows_workers import run_setup_command, system_root

    system = system_root()
    profile = stage.path / 'profile'
    stage.mkdir(('profile',))
    environment = {key: str(system) for key in ('SystemRoot', 'WINDIR')}
    environment['PATH'] = str(system / 'System32')
    environment.update({key: str(profile) for key in ('APPDATA', 'LOCALAPPDATA', 'USERPROFILE',
                                                      'HOME', 'TMP', 'TEMP')})
    return run_setup_command(
        [str(converter.executable), '-c', str(profile), '-o', str(output), str(source)],
        cwd=str(stage.path), env=environment, timeout=45, capture_result=True,
    )


def launch_installer(asset, data):
    """Launch the verified upstream MSI interactively under Windows Installer."""
    from .runtime_paths import temporary_parent
    from .windows_install_storage import private_workspace
    from .windows_workers import system_root

    with private_workspace(temporary_parent(), 'composerlint-musescore-') as stage:
        with stage.create((asset.filename,)) as stream:
            stream.write(data)
        with stage.held_file((asset.filename,)) as installer:
            return subprocess.run(
                [str(system_root() / 'System32' / 'msiexec.exe'), '/i', str(installer), '/norestart'],
                stdin=subprocess.DEVNULL, check=False,
            ).returncode
