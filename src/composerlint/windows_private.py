"""Experimental Windows file identity, private storage, and setup locking."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as win
from dataclasses import dataclass
from pathlib import PureWindowsPath
import sys
import threading

from .platform_support import UnsupportedPlatformOperation, local_path_text
from .windows_read import (
    _FileAPI, _bind, _OPEN_REPARSE_POINT, _READ_ATTRIBUTES, _READ_DATA,
    _SHARE_READ, _TRAVERSE, _stream,
)


_READ_CONTROL = 0x00020000
_DELETE = 0x00010000
_WRITE_DATA = 0x0002
_FILE_ALL_ACCESS = 0x001F01FF
_SYSTEM_SID = 'S-1-5-18'
_FILE_OPEN, _FILE_CREATE, _FILE_OPEN_IF = 1, 2, 3
_FILE_DIRECTORY_FILE, _FILE_NON_DIRECTORY_FILE = 1, 0x40
_FILE_DELETE_ON_CLOSE = 0x1000
_ERROR_SHARING_VIOLATION = 32
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_NO_TOKEN = 1008
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_DEFAULT = 0x0080
_TOKEN_USER, _TOKEN_OWNER, _TOKEN_STATISTICS = 1, 4, 10


class _FileIdInfo(ctypes.Structure):
    _fields_ = [('volume', ctypes.c_uint64), ('file_id', ctypes.c_ubyte * 16)]


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [('creation', ctypes.c_int64), ('access', ctypes.c_int64),
                ('write', ctypes.c_int64), ('change', ctypes.c_int64),
                ('attributes', win.DWORD)]


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [('allocation', ctypes.c_int64), ('size', ctypes.c_int64),
                ('links', win.DWORD), ('delete_pending', ctypes.c_ubyte),
                ('directory', ctypes.c_ubyte)]


class _AclSizeInfo(ctypes.Structure):
    _fields_ = [('count', win.DWORD), ('bytes_used', win.DWORD), ('bytes_free', win.DWORD)]


class _AllowAce(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_ubyte), ('flags', ctypes.c_ubyte),
                ('size', win.WORD), ('mask', win.DWORD), ('sid_start', win.DWORD)]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [('sid', ctypes.c_void_p), ('attributes', win.DWORD)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', win.DWORD), ('descriptor', ctypes.c_void_p),
                ('inherit', win.BOOL)]


class _SecurityAPI:
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        advapi = ctypes.WinDLL('advapi32.dll', use_last_error=True, winmode=0x0800)
        pointer = ctypes.POINTER(ctypes.c_void_p)
        handle_pointer = ctypes.POINTER(win.HANDLE)
        self.close = _bind(kernel, 'CloseHandle', win.BOOL, win.HANDLE)
        self.free = _bind(kernel, 'LocalFree', win.HANDLE, win.HANDLE)
        self.current_process = _bind(kernel, 'GetCurrentProcess', win.HANDLE)
        self.current_thread = _bind(kernel, 'GetCurrentThread', win.HANDLE)
        self.open_process_token = _bind(advapi, 'OpenProcessToken', win.BOOL,
                                        win.HANDLE, win.DWORD, handle_pointer)
        self.open_thread_token = _bind(advapi, 'OpenThreadToken', win.BOOL,
                                       win.HANDLE, win.DWORD, win.BOOL, handle_pointer)
        self.token_info = _bind(advapi, 'GetTokenInformation', win.BOOL,
                                win.HANDLE, ctypes.c_int, ctypes.c_void_p, win.DWORD, win.LPDWORD)
        self.set_token_info = _bind(advapi, 'SetTokenInformation', win.BOOL,
                                    win.HANDLE, ctypes.c_int, ctypes.c_void_p, win.DWORD)
        self.sid_string = _bind(advapi, 'ConvertSidToStringSidW', win.BOOL,
                                ctypes.c_void_p, pointer)
        self.parse_descriptor = _bind(advapi, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
                                      win.BOOL, win.LPCWSTR, win.DWORD, pointer, win.LPDWORD)
        self.get_security = _bind(advapi, 'GetSecurityInfo', win.DWORD, win.HANDLE,
                                  ctypes.c_int, win.DWORD, pointer, pointer, pointer,
                                  pointer, pointer)
        self.descriptor_control = _bind(advapi, 'GetSecurityDescriptorControl', win.BOOL,
                                        ctypes.c_void_p, ctypes.POINTER(win.WORD), win.LPDWORD)
        self.acl_info = _bind(advapi, 'GetAclInformation', win.BOOL, ctypes.c_void_p,
                              ctypes.c_void_p, win.DWORD, ctypes.c_int)
        self.get_ace = _bind(advapi, 'GetAce', win.BOOL, ctypes.c_void_p, win.DWORD, pointer)

    def sid_text(self, sid):
        result = ctypes.c_void_p()
        if not self.sid_string(sid, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(result)
        finally:
            self.free(result)

    @contextmanager
    def _token(self, *, process=None, access=_TOKEN_QUERY):
        token = win.HANDLE()
        if process is None and not self.open_thread_token(
                self.current_thread(), access, True, ctypes.byref(token)):
            if ctypes.get_last_error() != _ERROR_NO_TOKEN:
                raise ctypes.WinError(ctypes.get_last_error())
            process = self.current_process()
        if process is not None and not self.open_process_token(process, access, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield token
        finally:
            self.close(token)

    def _token_data(self, token, info_class):
        size = win.DWORD()
        if self.token_info(token, info_class, None, 0, ctypes.byref(size)):
            raise OSError('Unexpected empty Windows token information.')
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        data = ctypes.create_string_buffer(size.value)
        if not self.token_info(token, info_class, data, len(data), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return data

    def _default_owner(self, token):
        data = self._token_data(token, _TOKEN_OWNER)
        return self.sid_text(ctypes.c_void_p.from_buffer(data).value)

    def user_sid(self):
        with self._token() as token:
            data = self._token_data(token, _TOKEN_USER)
            return self.sid_text(_SidAndAttributes.from_buffer(data).sid)

    def set_setup_child_owner(self, process):
        """Set the suspended installer's default owner to its verified user."""
        sid = self.user_sid()
        with (self._token(process=self.current_process()) as parent,
              self._token(process=process, access=_TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT) as child):
            user = self._token_data(child, _TOKEN_USER)
            owner = ctypes.c_void_p(_SidAndAttributes.from_buffer(user).sid)
            if self.sid_text(owner.value) != sid:
                raise PermissionError('Setup tool token user differs from the effective setup user.')
            if self._default_owner(child) == sid:
                return
            # TOKEN_STATISTICS begins with the token's unique eight-byte LUID.
            if self._token_data(parent, _TOKEN_STATISTICS)[:8] == self._token_data(child, _TOKEN_STATISTICS)[:8]:
                raise PermissionError('Setup tool does not have a separate primary token.')
            if not self.set_token_info(child, _TOKEN_OWNER, ctypes.byref(owner), ctypes.sizeof(owner)):
                raise ctypes.WinError(ctypes.get_last_error())
            if self._default_owner(child) != sid:
                raise PermissionError('Setup tool default owner differs from the effective setup user.')

    @contextmanager
    def descriptor(self, sddl):
        descriptor = ctypes.c_void_p()
        if not self.parse_descriptor(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield descriptor
        finally:
            self.free(descriptor)

    def private_descriptor(self, sid, *, inheritable=False):
        flags = 'OICI' if inheritable else ''
        grants = ''.join(f'(A;{flags};FA;;;{trustee})' for trustee in sorted({sid, _SYSTEM_SID}))
        return self.descriptor(f'O:{sid}D:P{grants}')

    def require_private(self, handle, sid, *, tree_root=False, allow_inherited=False):
        owner, acl, descriptor = (ctypes.c_void_p() for _ in range(3))
        result = self.get_security(handle, 1, 5, ctypes.byref(owner), None,
                                   ctypes.byref(acl), None, ctypes.byref(descriptor))
        if result:
            raise ctypes.WinError(result)
        try:
            actual_owner = self.sid_text(owner) if owner else None
            if actual_owner != sid:
                raise PermissionError('Private Windows object owner differs from the effective user SID '
                                      f'(owner {actual_owner}, expected {sid}).')
            control, revision = win.WORD(), win.DWORD()
            if not self.descriptor_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
                raise ctypes.WinError(ctypes.get_last_error())
            protected = bool(control.value & 0x1000)
            if not acl or not control.value & 0x0004 or not (protected or allow_inherited):
                raise PermissionError('A present, private Windows DACL is required.')
            # Only the setup tools' ordinary outputs may use inherited grants.
            # Both forms require the same trustees and access masks.
            if protected:
                flags = 0x03 if tree_root else 0
            else:
                flags = 0x13 if tree_root else 0x10
            info = _AclSizeInfo()
            if not self.acl_info(acl, ctypes.byref(info), ctypes.sizeof(info), 2):
                raise ctypes.WinError(ctypes.get_last_error())
            remaining = {sid, _SYSTEM_SID}
            for index in range(info.count):
                address = ctypes.c_void_p()
                if not self.get_ace(acl, index, ctypes.byref(address)):
                    raise ctypes.WinError(ctypes.get_last_error())
                ace = _AllowAce.from_address(address.value)
                if ace.kind or ace.flags != flags or ace.mask != _FILE_ALL_ACCESS:
                    raise PermissionError('Unexpected ACE in private Windows DACL.')
                trustee = self.sid_text(address.value + _AllowAce.sid_start.offset)
                if trustee not in remaining:
                    raise PermissionError('Unexpected trustee in private Windows DACL.')
                remaining.remove(trustee)
            if remaining:
                raise PermissionError('Required grants are missing from private Windows DACL.')
        finally:
            self.free(descriptor)


@dataclass(frozen=True)
class FileStamp:
    volume: int
    file_id: bytes
    change_time: int
    write_time: int
    size: int
    links: int


class _StorageAPI(_FileAPI):
    def query(self, handle, info_class, structure):
        result = structure()
        if not self.file_info(handle, info_class, ctypes.byref(result), ctypes.sizeof(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def identity(self, handle):
        info = self.query(handle, 18, _FileIdInfo)
        return info.volume, bytes(info.file_id)

    def stamp(self, handle):
        self.check_type(handle, directory=False)
        basic = self.query(handle, 0, _FileBasicInfo)
        standard = self.query(handle, 1, _FileStandardInfo)
        if standard.delete_pending or standard.links < 1 or standard.size < 0:
            raise OSError('Windows file is pending deletion or has no stable link.')
        return FileStamp(*self.identity(handle), basic.change, basic.write,
                         standard.size, standard.links)

    def verify_chain(self, chain):
        for handle, identity in chain:
            self.check_type(handle, directory=True)
            if self.identity(handle) != identity:
                raise OSError('Windows directory identity changed.')

    def ancestors(self, path, stack):
        root = self.open_root(path.drive, stack)
        chain = [(root, self.identity(root))]
        for name in path.parts[1:-1]:
            child = self.open_component(chain[-1][0], name, access=_READ_ATTRIBUTES | _TRAVERSE,
                                        share=_SHARE_READ, disposition=_FILE_OPEN,
                                        options=_OPEN_REPARSE_POINT, refuse_reparse=True)
            stack.callback(self.close, child)
            self.check_type(child, directory=True)
            chain.append((child, self.identity(child)))
        self.verify_chain(chain)
        return chain


def _native_path(path):
    if sys.platform != 'win32':
        raise UnsupportedPlatformOperation('Windows security pilot requires native Windows.')
    return PureWindowsPath(local_path_text(path, target='win32'))


def _check_read_limit(limit):
    if type(limit) is not int or limit < 0:
        raise ValueError('A nonnegative byte limit is required.')


def _read_open(api, handle, chain, *, limit, expected, require_private=None):
    try:
        if require_private is not None:
            require_private(handle)
        before = api.stamp(handle)
        if expected is not None and before != expected:
            raise OSError('Windows file changed since the expected snapshot.')
        if before.size > limit:
            raise ValueError('Windows file exceeds the read limit.')
        api.verify_chain(chain)
    except BaseException:
        api.close(handle)
        raise
    with _stream(api, handle, 'rb') as stream:
        data = stream.read(before.size + 1)
        api.verify_chain(chain)
        if require_private is not None:
            require_private(handle)
        if len(data) != before.size or api.stamp(handle) != before:
            raise OSError('Windows file changed while it was being read.')
    return data, before


def read_stable(path, *, limit, expected=None):
    """Return bounded data and its stamp only after rechecking held NTFS objects."""
    path = _native_path(path)
    _check_read_limit(limit)
    api = _StorageAPI()
    with ExitStack() as stack:
        chain = api.ancestors(path, stack)
        handle = api.open_component(chain[-1][0], path.name,
                                    access=_READ_DATA | _READ_ATTRIBUTES, share=_SHARE_READ,
                                    disposition=_FILE_OPEN,
                                    options=_FILE_NON_DIRECTORY_FILE | _OPEN_REPARSE_POINT,
                                    refuse_reparse=True)
        return _read_open(api, handle, chain, limit=limit, expected=expected)


class _PrivateDirectory:
    def __init__(self, api, security, sid, chain):
        self._api, self._security, self._sid = api, security, sid
        self._chain = chain
        self._owner_thread = threading.current_thread()

    def _validate(self):
        # A second thread can open its own scope. Sharing this scope would
        # let its owner close the directory handles during a relative open.
        if threading.current_thread() is not self._owner_thread:
            raise ValueError('Private Windows directory must be used by its owning thread.')
        if self._chain is None:
            raise ValueError('Private directory scope has ended.')
        self._api.verify_chain(self._chain)
        handle = self._chain[-1][0]
        if self._security.user_sid() != self._sid:
            raise PermissionError('Effective Windows user changed during the private scope.')
        self._security.require_private(handle, self._sid)
        return handle

    def _open_file(self, name, *, access, disposition, options=0):
        parent = self._validate()
        # Reuse the drive-path validator without permitting nested names.
        if not isinstance(name, str) or '/' in name or '\\' in name:
            raise ValueError('A single Windows filename is required.')
        local_path_text('C:\\' + name, target='win32')
        with self._security.private_descriptor(self._sid) as descriptor:
            return self._api.open_component(
                parent, name, access=access | _READ_ATTRIBUTES | _READ_CONTROL, share=0,
                disposition=disposition,
                options=options | _FILE_NON_DIRECTORY_FILE | _OPEN_REPARSE_POINT,
                descriptor=descriptor, refuse_reparse=True)

    def _validate_file(self, handle):
        self._security.require_private(handle, self._sid)
        if self._api.query(handle, 1, _FileStandardInfo).links != 1:
            raise PermissionError('A private Windows file must have exactly one link.')
        self._validate()

    def read_file(self, name, *, limit, expected=None):
        _check_read_limit(limit)
        handle = self._open_file(name, access=_READ_DATA, disposition=_FILE_OPEN)
        return _read_open(self._api, handle, self._chain, limit=limit, expected=expected,
                          require_private=self._validate_file)

    @contextmanager
    def exclusive_lock(self):
        """Hold the fixed lock file exclusively; leave its directory entry in place."""
        try:
            handle = self._open_file('setup.lock', access=_READ_DATA, disposition=_FILE_OPEN_IF)
        except OSError as error:
            if getattr(error, 'winerror', None) == _ERROR_SHARING_VIOLATION:
                raise BlockingIOError('The Windows setup lock is already in use.') from error
            raise
        try:
            self._api.stamp(handle)
            self._validate_file(handle)
            yield
            self._validate_file(handle)
        finally:
            self._api.close(handle)

    @contextmanager
    def temporary_file(self, name):
        """Create a new private scratch file; NTFS deletes that handle's link on close."""
        handle = self._open_file(name, access=_READ_DATA | _WRITE_DATA | _DELETE,
                                 disposition=_FILE_CREATE, options=_FILE_DELETE_ON_CLOSE)
        try:
            self._api.check_type(handle, directory=False)
            self._validate_file(handle)
        except BaseException:
            self._api.close(handle)
            raise
        with _stream(self._api, handle, 'w+b') as stream:
            yield stream
            if not stream.closed:
                self._api.check_type(handle, directory=False)
                self._validate_file(handle)


@contextmanager
def private_directory(path, *, create=False):
    """Hold an existing or newly created private directory with a verified DACL."""
    path = _native_path(path)
    api, security = _StorageAPI(), _SecurityAPI()
    sid = security.user_sid()
    with ExitStack() as stack, security.private_descriptor(sid) as descriptor:
        chain = api.ancestors(path, stack)
        handle = api.open_component(
            chain[-1][0], path.name, access=_READ_ATTRIBUTES | _TRAVERSE | _READ_CONTROL,
            share=_SHARE_READ, disposition=_FILE_CREATE if create else _FILE_OPEN,
            options=_FILE_DIRECTORY_FILE, descriptor=descriptor, refuse_reparse=True)
        stack.callback(api.close, handle)
        api.check_type(handle, directory=True)
        chain.append((handle, api.identity(handle)))
        directory = _PrivateDirectory(api, security, sid, chain)
        directory._validate()
        try:
            yield directory
        finally:
            directory._chain = None
