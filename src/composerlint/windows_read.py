"""Read-only NTFS pilot. Application entry points do not use it yet."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as win
import os
from pathlib import PureWindowsPath
import re
import sys

from .platform_support import UnsupportedPlatformOperation, local_path_text


_READ_DATA = 0x0001
_TRAVERSE = 0x0020
_READ_ATTRIBUTES = 0x0080
_SYNCHRONIZE = 0x00100000
_SHARE_READ = 0x0001
_SHARE_WRITE = 0x0002
_DIRECTORY = 0x0010
_REPARSE_POINT = 0x0400
_OPEN_REPARSE_POINT = 0x00200000
_VOLUME_ROOT = re.compile(r'\\\\\?\\Volume\{[0-9a-f-]{36}\}\\', re.IGNORECASE)


class _UnicodeString(ctypes.Structure):
    _fields_ = [('length', win.USHORT), ('maximum_length', win.USHORT),
                ('buffer', win.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('length', win.ULONG), ('root_directory', win.HANDLE),
                ('object_name', ctypes.POINTER(_UnicodeString)),
                ('attributes', win.ULONG), ('security_descriptor', ctypes.c_void_p),
                ('security_quality_of_service', ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    # The first slot is a union of NTSTATUS and a pointer.
    _fields_ = [('status_or_pointer', ctypes.c_void_p), ('information', ctypes.c_size_t)]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [('attributes', win.DWORD), ('tag', win.DWORD)]


def _bind(library, name, result, *args):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = args
    return function


class _FileAPI:
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        ntdll = ctypes.WinDLL('ntdll.dll', winmode=0x0800)
        self.close = _bind(kernel, 'CloseHandle', win.BOOL, win.HANDLE)
        self.file_type = _bind(kernel, 'GetFileType', win.DWORD, win.HANDLE)
        self.drive_type = _bind(kernel, 'GetDriveTypeW', win.UINT, win.LPCWSTR)
        self.create_file = _bind(kernel, 'CreateFileW', win.HANDLE, win.LPCWSTR,
                                 win.DWORD, win.DWORD, ctypes.c_void_p, win.DWORD,
                                 win.DWORD, win.HANDLE)
        self.final_path = _bind(kernel, 'GetFinalPathNameByHandleW', win.DWORD,
                                win.HANDLE, win.LPWSTR, win.DWORD, win.DWORD)
        self.volume_info = _bind(kernel, 'GetVolumeInformationByHandleW', win.BOOL,
                                 win.HANDLE, win.LPWSTR, win.DWORD, win.LPDWORD,
                                 win.LPDWORD, win.LPDWORD, win.LPWSTR, win.DWORD)
        self.file_info = _bind(kernel, 'GetFileInformationByHandleEx', win.BOOL,
                               win.HANDLE, ctypes.c_int, ctypes.c_void_p, win.DWORD)
        self.open_relative = _bind(
            ntdll, 'NtCreateFile', win.LONG, ctypes.POINTER(win.HANDLE), win.ULONG,
            ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatusBlock),
            ctypes.c_void_p, win.ULONG, win.ULONG, win.ULONG, win.ULONG,
            ctypes.c_void_p, win.ULONG)
        self.status_to_error = _bind(ntdll, 'RtlNtStatusToDosError', win.ULONG, win.LONG)

    def check_type(self, handle, *, directory):
        if self.file_type(handle) != 1:  # FILE_TYPE_DISK
            raise OSError('A local disk handle is required.')
        info = _FileAttributeTagInfo()
        if not self.file_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & _REPARSE_POINT:
            raise OSError('Windows reparse points are refused.')
        if bool(info.attributes & _DIRECTORY) != directory:
            raise OSError('Expected a Windows directory.' if directory else
                          'Expected a regular Windows file.')

    def open_root(self, drive, stack):
        root = drive + '\\'
        if self.drive_type(root) not in (2, 3):  # DRIVE_REMOVABLE or DRIVE_FIXED
            raise UnsupportedPlatformOperation('A local Windows drive is required.')
        handle = self.create_file(root, _READ_ATTRIBUTES, _SHARE_READ | _SHARE_WRITE,
                                  None, 3, 0x02000000 | _OPEN_REPARSE_POINT, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(self.close, handle)
        self.check_type(handle, directory=True)

        # Confirm that the DOS drive resolves to the actual volume root. A SUBST
        # drive can appear fixed while naming a subdirectory.
        path = ctypes.create_unicode_buffer(128)
        size = self.final_path(handle, path, len(path), 1)  # VOLUME_NAME_GUID
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        if size >= len(path) or not _VOLUME_ROOT.fullmatch(path.value):
            raise UnsupportedPlatformOperation('The drive must name a local volume root.')
        flags = win.DWORD()
        filesystem = ctypes.create_unicode_buffer(32)
        if not self.volume_info(handle, None, 0, None, None, ctypes.byref(flags),
                                filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        required = 0x00000008 | 0x00000080  # persistent ACLs and reparse-point support
        if filesystem.value.upper() != 'NTFS' or flags.value & required != required:
            raise UnsupportedPlatformOperation('NTFS with persistent ACLs and reparse points is required.')
        return handle

    def open_component(self, parent, name, *, access, share, disposition, options,
                       descriptor=None, refuse_reparse=False):
        # NtCreateFile counts UTF-16 bytes, including both halves of surrogate pairs.
        encoded_size = len(name.encode('utf-16-le'))
        if encoded_size > 0xfffc:
            raise ValueError('Windows path component exceeds the native name limit.')
        component = _UnicodeString(encoded_size, encoded_size + 2, name)
        object_flags = 0x40 | (0x1000 if refuse_reparse else 0)
        attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent,
                                       ctypes.pointer(component), object_flags, descriptor, None)
        result = win.HANDLE()
        status_block = _IoStatusBlock()
        status = self.open_relative(ctypes.byref(result), access | _SYNCHRONIZE,
                                    ctypes.byref(attributes), ctypes.byref(status_block),
                                    None, 0, share, disposition,
                                    options | 0x20, None, 0)  # FILE_SYNCHRONOUS_IO_NONALERT
        if status < 0:
            raise ctypes.WinError(self.status_to_error(status))
        return result.value

    def child(self, parent, name, *, directory):
        access = _READ_ATTRIBUTES
        share = _SHARE_READ
        options = _OPEN_REPARSE_POINT
        if directory:
            # Attributes-only opens do not enforce the delete-sharing rule.
            access |= _TRAVERSE
            share |= _SHARE_WRITE
        else:
            access |= _READ_DATA
            options |= 0x40  # FILE_NON_DIRECTORY_FILE
        return self.open_component(parent, name, access=access, share=share,
                                   disposition=1, options=options)  # FILE_OPEN


@contextmanager
def _stream(api, handle, mode):
    """Consume the handle, transferring ownership to the CRT exactly once."""
    import msvcrt

    access = os.O_RDWR if mode == 'w+b' else os.O_RDONLY
    try:
        fd = msvcrt.open_osfhandle(handle, access | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        api.close(handle)
        raise
    try:
        stream = os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise
    with stream:
        yield stream


@contextmanager
def open_regular(path):
    """Pilot-only read from a held file handle after walking held directories."""
    if sys.platform != 'win32':
        raise UnsupportedPlatformOperation('The NTFS reader requires native Windows.')

    path = PureWindowsPath(local_path_text(path, target='win32'))
    api = _FileAPI()
    with ExitStack() as directories:
        parent = api.open_root(path.drive, directories)
        for name in path.parts[1:-1]:
            parent = api.child(parent, name, directory=True)
            directories.callback(api.close, parent)
            api.check_type(parent, directory=True)

        handle = api.child(parent, path.name, directory=False)
        try:
            api.check_type(handle, directory=False)
        except BaseException:
            api.close(handle)
            raise
        with _stream(api, handle, 'rb') as stream:
            yield stream
