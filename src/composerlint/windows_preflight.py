"""Read-only Windows terminal preflight, independent of setup and runtime dispatch."""

from contextlib import ExitStack
import ctypes
from ctypes import wintypes as win
import os
from pathlib import PureWindowsPath
import re
import sys
from typing import NamedTuple
from uuid import UUID

from .platform_support import local_path_text
from .windows_private import _READ_CONTROL, _SecurityAPI, _StorageAPI
from .windows_read import (
    _OPEN_REPARSE_POINT, _READ_ATTRIBUTES, _SHARE_READ, _SHARE_WRITE, _TRAVERSE, _bind,
)


_AMD64 = 0x8664
_MACHINES = {0x014c: 'x86', _AMD64: 'x64', 0xaa64: 'ARM64'}
_DISK_DEVICE = re.compile(r'\\Device\\HarddiskVolume[0-9]+', re.IGNORECASE)
_DRIVE_ROOT = re.compile(r'[A-Za-z]:[\\/]')
_NTFS_FLAGS = 0x00000008 | 0x00000080  # persistent ACLs and reparse points
_LOCAL_APP_DATA = UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le
_APP_DIRECTORIES = ('ComposerLint', 'setup')
_ADD_FILE, _ADD_SUBDIRECTORY = 0x0002, 0x0004
_TOOLS = ('python.exe', 'uv.exe')

_BOOTSTRAP_PINS = (
    ('Private CPython 3.12.13 (Windows x64, build 20260807)',
     'cpython-3.12.13+20260807-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
     '18bcc65b17921806b72cdc88bcf000bf67a2c99a8fc381fe1629f2b9ba56858d'),
    ('Private uv 0.12.3 (Windows x64)', 'uv-x86_64-pc-windows-msvc.zip',
     'b23350c79e8ad0192b8124af13a0f17e8d4e4549524785e1aef389ae5a06990e'),
)


class Diagnostic(NamedTuple):
    status: str
    detail: str


class Report:
    def __init__(self):
        self.destination = None
        self.checks = {
            'Windows x64': Diagnostic('Not checked', 'Native machine and process architecture.'),
            'Destination path': Diagnostic('Not checked', 'Windows Local AppData and path syntax.'),
            'Local NTFS volume': Diagnostic('Not checked', 'Local drive, filesystem and capabilities.'),
            'Destination access': Diagnostic('Not checked', 'Safe traversal and current-user directory access.'),
            'Existing private directories': Diagnostic('Not checked', 'Owner SID and protected DACL.'),
            'Host Python (optional)': Diagnostic('Not checked', 'Local PATH discovery only.'),
            'Host uv (optional)': Diagnostic('Not checked', 'Local PATH discovery only.'),
            'Installation operations': Diagnostic(
                'Not checked', 'Directory creation, locking, writes, and package installation are deferred.'),
        }

    @property
    def compatible(self):
        required = ('Windows x64', 'Destination path', 'Local NTFS volume', 'Destination access')
        return (all(self.checks[name].status == 'Passed' for name in required)
                and self.checks['Existing private directories'].status != 'Failed')


def _architecture():
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
    current = _bind(kernel, 'GetCurrentProcess', win.HANDLE)
    query = _bind(kernel, 'IsWow64Process2', win.BOOL, win.HANDLE,
                  ctypes.POINTER(win.USHORT), ctypes.POINTER(win.USHORT))
    process, native = win.USHORT(), win.USHORT()
    if not query(current(), ctypes.byref(process), ctypes.byref(native)):
        raise ctypes.WinError(ctypes.get_last_error())
    return native.value, process.value


def _local_app_data():
    shell = ctypes.WinDLL('shell32.dll', winmode=0x0800)
    ole = ctypes.WinDLL('ole32.dll', winmode=0x0800)
    lookup = _bind(shell, 'SHGetKnownFolderPath', win.LONG, ctypes.c_void_p,
                   win.DWORD, win.HANDLE, ctypes.POINTER(ctypes.c_void_p))
    free = _bind(ole, 'CoTaskMemFree', None, ctypes.c_void_p)
    folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(_LOCAL_APP_DATA)
    path = ctypes.c_void_p()
    try:
        result = lookup(folder_id, 0x4000, None, ctypes.byref(path))  # KF_FLAG_DONT_VERIFY
        if result or not path:
            raise OSError(f'Windows Local AppData lookup failed (HRESULT 0x{result & 0xFFFFFFFF:08X}).')
        return ctypes.wstring_at(path)
    finally:
        free(path)


def _direct_volume_path(mapping):
    if not _DISK_DEVICE.fullmatch(mapping):
        raise OSError('Drive is remote, substituted, or lacks a directly identifiable local disk volume.')
    # Use the queried NT device directly. Re-resolving the drive letter when
    # opening it would allow a changed mapping to redirect the check.
    return '\\\\?\\GLOBALROOT' + mapping + '\\'


class _ReadOnlyDisk(_StorageAPI):
    def __init__(self):
        super().__init__()
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        self.dos_device = _bind(kernel, 'QueryDosDeviceW', win.DWORD,
                                win.LPCWSTR, win.LPWSTR, win.DWORD)

    def open_root(self, drive, stack):
        mapping = ctypes.create_unicode_buffer(32768)
        if not self.dos_device(drive, mapping, len(mapping)):
            raise ctypes.WinError(ctypes.get_last_error())
        path = _direct_volume_path(mapping.value)
        handle = self.create_file(path, _READ_ATTRIBUTES, _SHARE_READ | _SHARE_WRITE,
                                  None, 3, 0x02000000 | _OPEN_REPARSE_POINT, None)  # OPEN_EXISTING
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(self.close, handle)
        self.check_type(handle, directory=True)
        return handle

    def open_existing(self, parent, name, stack, *, directory, access=0, share_write=False):
        share = _SHARE_READ | (_SHARE_WRITE if share_write else 0)
        access |= _READ_ATTRIBUTES | (_TRAVERSE if directory else 0)
        options = 1 if directory else 0x40 | _OPEN_REPARSE_POINT
        handle = self.open_component(parent, name, access=access, share=share,
                                     disposition=1, options=options, refuse_reparse=True)  # FILE_OPEN
        stack.callback(self.close, handle)
        self.check_type(handle, directory=directory)
        return handle


def _checked_path(raw):
    return PureWindowsPath(local_path_text(raw, target='win32'))


def _check_destination(report, app_data, disk, security):
    checks = report.checks
    stage = 'Local NTFS volume'
    try:
        with ExitStack() as stack:
            root = disk.open_root(app_data.drive, stack)
            filesystem, flags = disk.volume_capabilities(root)
            if filesystem.upper() != 'NTFS' or flags & _NTFS_FLAGS != _NTFS_FLAGS:
                raise OSError(f'Found {filesystem or "unknown filesystem"}. Local NTFS with ACL and reparse-point support is required.')
            checks[stage] = Diagnostic('Passed', 'Local NTFS supports persistent ACLs and reparse points.')

            stage = 'Destination access'
            chain = [(root, disk.identity(root))]
            parent, name = root, None
            components = app_data.parts[1:]
            for index, name in enumerate(components):
                parent = chain[-1][0]
                handle = disk.open_existing(parent, name, stack, directory=True,
                                            share_write=index == len(components) - 1)
                chain.append((handle, disk.identity(handle)))
            # Existing application-owned directories must already be private.
            # Missing directories are only planned. No native create disposition
            # is used, even for the access check below.
            sid = security.user_sid()
            existing = []
            missing = None
            for component in _APP_DIRECTORIES:
                try:
                    handle = disk.open_existing(chain[-1][0], component, stack,
                                                directory=True, access=_READ_CONTROL, share_write=True)
                except FileNotFoundError:
                    missing = component
                    break
                parent, name = chain[-1][0], component
                chain.append((handle, disk.identity(handle)))
                existing.append(handle)
            rights = _ADD_SUBDIRECTORY if missing else _ADD_SUBDIRECTORY | _ADD_FILE
            handle = disk.open_existing(parent, name, stack, directory=True,
                                        access=rights, share_write=True)
            if disk.identity(handle) != chain[-1][1]:
                raise OSError('The destination directory changed during the access check.')
            disk.verify_chain(chain)
            if security.user_sid() != sid:
                raise PermissionError('The effective Windows user changed during preflight.')
            checks[stage] = Diagnostic(
                'Passed',
                f'Windows granted directory access needed to create {missing} under its existing parent.'
                if missing else 'Windows granted file and subdirectory creation rights on the existing destination.',
            )
            stage = 'Existing private directories'
            if existing:
                for handle in existing:
                    security.require_private(handle, sid)
                checks[stage] = Diagnostic('Passed', 'Existing application directories have the expected owner and DACL.')
            else:
                checks[stage] = Diagnostic(
                    'Not checked', 'The application directory is absent. Its owner and DACL can be checked after creation.')
    except (OSError, ValueError) as error:
        checks[stage] = Diagnostic('Failed', str(error))


def _discover_host_tools(disk, path_text):
    found = {}
    visited = set()
    for entry in path_text.split(';'):
        if entry.startswith('"') and entry.endswith('"'):
            entry = entry[1:-1]
        try:
            # A plain drive root is also a valid PATH directory.
            path = (PureWindowsPath(entry) if _DRIVE_ROOT.fullmatch(entry) else
                    _checked_path(entry[:-1] if entry.endswith(('\\', '/')) else entry))
        except ValueError:
            continue
        if path in visited:
            continue
        visited.add(path)
        try:
            with ExitStack() as stack:
                parent = disk.open_root(path.drive, stack)
                for component in path.parts[1:]:
                    parent = disk.open_existing(parent, component, stack, directory=True)
                for name in _TOOLS:
                    if name not in found:
                        try:
                            disk.open_existing(parent, name, stack, directory=False)
                        except OSError:
                            continue
                        found[name] = path / name
        except (OSError, ValueError):
            continue
        if len(found) == len(_TOOLS):
            break
    return found


def inspect():
    report = Report()
    checks = report.checks
    if sys.platform != 'win32':
        checks['Windows x64'] = Diagnostic('Failed', 'This preflight requires a native Windows x64 process.')
        return report
    try:
        native, process = _architecture()
    except (OSError, AttributeError) as error:
        checks['Windows x64'] = Diagnostic('Failed', f'Unable to verify native architecture ({error}).')
        return report
    if native != _AMD64 or process:
        native_name = _MACHINES.get(native, f'0x{native:04X}')
        process_name = _MACHINES.get(process, f'0x{process:04X}') if process else 'native'
        checks['Windows x64'] = Diagnostic('Failed', f'Windows reports {native_name} hardware and a {process_name} process.')
        return report
    checks['Windows x64'] = Diagnostic('Passed', 'Windows reports x64 hardware and a native process.')
    app_data = None
    try:
        app_data = _checked_path(_local_app_data())
    except (OSError, ValueError, AttributeError) as error:
        checks['Destination path'] = Diagnostic('Failed', str(error))
    else:
        report.destination = app_data.joinpath(*_APP_DIRECTORIES)
        checks['Destination path'] = Diagnostic('Passed', 'Absolute Windows Local AppData path.')
    try:
        disk = _ReadOnlyDisk()
    except (OSError, AttributeError) as error:
        if app_data is not None:
            checks['Local NTFS volume'] = Diagnostic('Failed', f'Windows capability lookup failed ({error}).')
        return report
    if app_data is not None:
        try:
            _check_destination(report, app_data, disk, _SecurityAPI())
        except (OSError, AttributeError) as error:
            checks['Local NTFS volume'] = Diagnostic('Failed', f'Windows capability lookup failed ({error}).')
    found = _discover_host_tools(disk, os.environ.get('PATH', ''))
    for name, label in zip(_TOOLS, ('Host Python (optional)', 'Host uv (optional)')):
        path = found.get(name)
        checks[label] = (Diagnostic('Passed', f'Found {path}. Version and execution were not checked.')
                         if path else Diagnostic('Failed', f'No safely inspectable local PATH candidate for {name}.'))
    return report


def print_report(report):
    print('ComposerLint Windows terminal preflight')
    for label, (status, detail) in report.checks.items():
        print(f'  [{status}] {label} — {detail}')
    result = 'Read-only compatibility checks passed.' if report.compatible else 'Read-only compatibility checks did not pass.'
    print(f'\n{result}')
    print('\nProposed text-only installation (future approval required)')
    destination = report.destination or r'Local AppData\ComposerLint\setup'
    print(f'  Destination — {destination}')
    print('  A private environment for ComposerLint 0.2.0 and the exact dependencies in uv.lock.')
    print('  Expected downloads with an empty cache')
    for label, filename, digest in _BOOTSTRAP_PINS:
        print(f'    {label} — {filename}')
        print(f'      SHA-256 {digest}')
    print('    Hash-pinned Windows wheels selected by uv.lock. The ComposerLint package comes from the reviewed source.')
    print('  Optional host tools are discovery results. The proposed setup uses the private pins above.')
    print('  This stage leaves installation, renderer-backed notation and editing, MCP client registration,')
    print('  global AI-assistant settings changes, and permanent Windows Approve & Export unavailable.')
    print('  This command performs local inspection only.')
