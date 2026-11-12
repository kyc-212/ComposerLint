"""Read-only file inventory for the Windows installation CI checkpoint."""

import ctypes
from ctypes import wintypes as win
import hashlib
import ntpath
import os
from pathlib import Path, PureWindowsPath
import stat


_APP_EXEC_LINK = 0x8000001B
_RECALL_OR_OFFLINE = 0x00441000
_REGISTRY_SCOPES = (
    ('HKEY_CURRENT_USER', r'Environment'),
    ('HKEY_LOCAL_MACHINE', r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
    ('HKEY_CURRENT_USER', r'Software\Python'),
    ('HKEY_LOCAL_MACHINE', r'Software\Python'),
    ('HKEY_LOCAL_MACHINE', r'Software\WOW6432Node\Python'),
    ('HKEY_CURRENT_USER', r'Software\Microsoft\Windows\CurrentVersion\Uninstall'),
    ('HKEY_LOCAL_MACHINE', r'Software\Microsoft\Windows\CurrentVersion\Uninstall'),
    ('HKEY_LOCAL_MACHINE', r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
)


class _FindData(ctypes.Structure):
    _fields_ = [
        ('attributes', win.DWORD),
        ('created', win.FILETIME), ('accessed', win.FILETIME), ('written', win.FILETIME),
        ('size_high', win.DWORD), ('size_low', win.DWORD),
        ('reparse_tag', win.DWORD), ('reserved', win.DWORD),
        ('name', win.WCHAR * 260), ('short_name', win.WCHAR * 14),
    ]


def find_metadata(path):
    # FindFirstFileW reads the directory entry, including its reparse tag,
    # without opening the file's content or resolving an execution alias.
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
    find = kernel.FindFirstFileW
    find.argtypes, find.restype = [win.LPCWSTR, ctypes.POINTER(_FindData)], win.HANDLE
    close = kernel.FindClose
    close.argtypes, close.restype = [win.HANDLE], win.BOOL
    data = _FindData()
    handle = find(str(path), ctypes.byref(data))
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):  # File or path not found.
            return None
        raise OSError(f'Cannot inspect {path}: {ctypes.WinError(error)}')
    if not close(handle):
        raise OSError(f'Cannot close metadata search for {path}: {ctypes.WinError(ctypes.get_last_error())}')
    return {
        'attributes': data.attributes,
        'reparse_tag': data.reparse_tag if data.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT else 0,
        'size': data.size_high << 32 | data.size_low,
        'created_filetime': data.created.dwHighDateTime << 32 | data.created.dwLowDateTime,
        'written_filetime': data.written.dwHighDateTime << 32 | data.written.dwLowDateTime,
    }


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def literal_path(path):
    path = PureWindowsPath(path)
    if (not path.is_absolute() or len(path.drive) != 2 or '..' in path.parts
            or any(char in str(path)[2:] for char in '*?:')):
        raise ValueError(f'Expected an absolute, literal local file path: {path}')
    return path


def snapshot_entry(path, alias_root, *, executable, allow_missing=False,
                   metadata=find_metadata, digest=file_hash):
    path = literal_path(path)
    info = metadata(path)
    if info is None:
        if executable and not allow_missing:
            raise FileNotFoundError(f'Discovered executable disappeared: {path}')
        return {'path': str(path), 'type': 'missing'}
    attributes = info['attributes']
    if attributes & (stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_DEVICE):
        raise OSError(f'Expected a file at {path}, attributes=0x{attributes:08X}')
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        tag = info['reparse_tag']
        if (not executable or path.parent != alias_root or path.name.lower() not in ('python.exe', 'uv.exe')
                or tag != _APP_EXEC_LINK):
            raise OSError(f'Unexpected reparse point at {path}, tag=0x{tag:08X}')
        return {'path': str(path), 'type': 'app_execution_alias', **info}
    if attributes & _RECALL_OR_OFFLINE:
        raise OSError(f'Offline or recalled file refused at {path}, attributes=0x{attributes:08X}')
    sha256 = digest(path)
    if metadata(path) != info:
        raise OSError(f'Host file metadata changed while hashing: {path}')
    return {'path': str(path), 'type': 'regular_file', 'sha256': sha256}


def executable_inventory(raw_path, runner_python, alias_root, *, metadata=find_metadata, digest=file_hash):
    # Record the actual runner interpreter even when it is absent from PATH.
    # Check the known alias directory even when it is absent from PATH, too.
    directories = {alias_root}
    for entry in raw_path.split(';'):
        if entry:
            directories.add(literal_path(ntpath.expandvars(entry.strip('"'))))
    runner_python = literal_path(runner_python)
    candidates = {runner_python} | {
        directory / name for directory in directories for name in ('python.exe', 'uv.exe')
    }
    files = []
    for path in sorted(candidates):
        entry = snapshot_entry(path, alias_root, executable=True, allow_missing=path != runner_python,
                               metadata=metadata, digest=digest)
        if entry['type'] != 'missing':
            files.append(entry)
    return files


def registry_snapshot(scopes=_REGISTRY_SCOPES, *, registry=None):
    if registry is None:
        import winreg as registry

    def contents(handle):
        before = registry.QueryInfoKey(handle)
        subkeys, values, _ = before
        result = {'values': {}, 'subkeys': {}}
        for index in range(values):
            name, data, kind = registry.EnumValue(handle, index)
            result['values'][name] = (kind, data)
        for index in range(subkeys):
            name = registry.EnumKey(handle, index)
            with registry.OpenKey(handle, name) as child:
                result['subkeys'][name] = contents(child)
        if registry.QueryInfoKey(handle) != before:
            raise OSError('Registry key changed while taking the snapshot.')
        return result

    result = {}
    for hive, path in scopes:
        try:
            handle = registry.OpenKey(getattr(registry, hive), path)
        except FileNotFoundError:
            result[(hive, path)] = None
        else:
            with handle:
                result[(hive, path)] = contents(handle)
    return result


def host_snapshot(runner_python, local_app_data):
    alias_root = literal_path(local_app_data) / 'Microsoft' / 'WindowsApps'
    home = literal_path(os.environ['USERPROFILE'])
    roaming = literal_path(os.environ['APPDATA'])
    settings = {
        literal_path(os.environ['GITHUB_PATH']), literal_path(os.environ['GITHUB_ENV']),
        home / '.codex/config.toml', home / '.claude.json', home / '.claude/settings.json',
        roaming / 'Claude/claude_desktop_config.json',
        roaming / 'Code/User/mcp.json', roaming / 'Code/User/settings.json', home / '.cursor/mcp.json',
    }
    if codex_home := os.environ.get('CODEX_HOME'):
        settings.add(literal_path(codex_home) / 'config.toml')
    raw_path = os.environ['PATH']
    return {
        'Process PATH': raw_path,
        'Python/uv files and aliases': executable_inventory(raw_path, runner_python, alias_root),
        'CI and AI client settings files': [
            snapshot_entry(path, alias_root, executable=False) for path in sorted(settings)
        ],
        'user and machine registry scopes (including persisted PATH)': registry_snapshot(),
    }
