"""Read-only file inventory for the Windows installation CI checkpoint."""

import argparse
import ctypes
from ctypes import wintypes as win
import hashlib
import json
from pathlib import Path, PureWindowsPath
import stat


_APP_EXEC_LINK = 0x8000001B


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


def snapshot_entry(path, alias_root, *, executable, metadata=find_metadata, digest=file_hash):
    path = PureWindowsPath(path)
    if (not path.is_absolute() or len(path.drive) != 2 or '..' in path.parts
            or any(char in str(path)[2:] for char in '*?:')):
        raise ValueError(f'Expected an absolute, literal local file path: {path}')
    info = metadata(path)
    if info is None:
        if executable:
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
    return {'path': str(path), 'type': 'regular_file', 'sha256': digest(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('local_app_data', type=PureWindowsPath)
    parser.add_argument('--settings', nargs='+', required=True)
    parser.add_argument('--executables', nargs='+', required=True)
    args = parser.parse_args()
    alias_root = args.local_app_data / 'Microsoft' / 'WindowsApps'
    print(json.dumps([
        snapshot_entry(path, alias_root, executable=executable)
        for executable, paths in ((False, args.settings), (True, args.executables))
        for path in sorted(set(paths), key=str.casefold)
    ], sort_keys=True))


if __name__ == '__main__':
    main()
