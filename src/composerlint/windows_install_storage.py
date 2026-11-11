"""NTFS staging for the Windows setup pilot; no runtime or export callers."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as win
import json
import os
from pathlib import Path
import secrets

from .platform_support import local_path_text
from .windows_preflight import _NTFS_FLAGS, _ReadOnlyDisk
from .windows_private import (
    _DELETE, _FILE_CREATE, _FILE_DIRECTORY_FILE, _FILE_NON_DIRECTORY_FILE,
    _FILE_OPEN, _FileStandardInfo, _PrivateDirectory, _READ_CONTROL, _SecurityAPI,
    _WRITE_DATA,
)
from .windows_read import (
    _IoStatusBlock, _OPEN_REPARSE_POINT, _READ_ATTRIBUTES, _READ_DATA, _SHARE_READ,
    _TRAVERSE, _bind, _stream,
)
from .windows_workers import SetupProcessCleanupUnconfirmed


class _RenameInfo(ctypes.Structure):
    _fields_ = [('flags', win.ULONG), ('parent', win.HANDLE), ('length', win.ULONG),
                ('filename', win.WCHAR * 1)]


class _InstallDisk(_ReadOnlyDisk):
    def __init__(self):
        super().__init__()
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        ntdll = ctypes.WinDLL('ntdll.dll', winmode=0x0800)
        self.set_info = _bind(kernel, 'SetFileInformationByHandle', win.BOOL,
                              win.HANDLE, ctypes.c_int, ctypes.c_void_p, win.DWORD)
        self.set_native_info = _bind(ntdll, 'NtSetInformationFile', win.LONG, win.HANDLE,
                                     ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                                     win.ULONG, ctypes.c_int)

    def remove(self, handle):
        delete = ctypes.c_ubyte(1)
        if not self.set_info(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
            raise ctypes.WinError(ctypes.get_last_error())

    def publish(self, handle, parent, name):
        encoded = name.encode('utf-16-le')
        size = max(ctypes.sizeof(_RenameInfo), _RenameInfo.filename.offset + len(encoded))
        buffer = ctypes.create_string_buffer(size)
        info = _RenameInfo.from_buffer(buffer)
        info.parent, info.length = parent, len(encoded)  # ReplaceIfExists is false.
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfo.filename.offset, encoded, len(encoded))
        status = self.set_native_info(handle, ctypes.byref(_IoStatusBlock()), buffer, size, 10)
        if status < 0:
            raise ctypes.WinError(self.status_to_error(status))


def _component(name):
    if not isinstance(name, str) or not name or any(char in name for char in '/\\'):
        raise ValueError('Expected a single Windows setup path component.')
    local_path_text('C:\\' + name, target='win32')
    return name


class Stage:
    def __init__(self, path, disk, security, sid, chain, private):
        self.path, self.disk, self.security, self.sid = path, disk, security, sid
        self.chain, self.private = chain, private
        self.handle, self.identity = chain[-1]
        self.committed = False

    def validate(self):
        self.disk.verify_chain(self.chain)
        if self.security.user_sid() != self.sid:
            raise PermissionError('The effective setup user changed.')
        for handle in self.private:
            self.security.require_private(handle, self.sid)
        self.security.require_private(self.handle, self.sid, tree_root=True)
        # Absolute paths passed to trusted child tools must still name the
        # local volume whose root is held in this transaction.
        with ExitStack() as stack:
            alias = self.disk.open_root(self.path.drive, stack)
            if self.disk.identity(alias) != self.chain[0][1]:
                raise OSError('The setup volume mapping changed.')

    def _open(self, parent, name, *, directory, create=False, access=0, share=None):
        handle = self.disk.open_component(
            parent, _component(name), access=access | _READ_ATTRIBUTES | _READ_CONTROL |
            (_TRAVERSE if directory else 0),
            share=_SHARE_READ if share is None else share,
            disposition=_FILE_CREATE if create else _FILE_OPEN,
            options=_FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE | _OPEN_REPARSE_POINT,
            refuse_reparse=True)
        try:
            self.disk.check_type(handle, directory=directory)
            self.security.require_private(handle, self.sid, inherited_directory=directory)
            if not directory and self.disk.query(handle, 1, _FileStandardInfo).links != 1:
                raise PermissionError('A setup file must have exactly one NTFS link.')
        except BaseException:
            self.disk.close(handle)
            raise
        return handle

    def _directories(self, parts, stack, *, create=False):
        parent = self.handle
        for name in parts:
            try:
                handle = self._open(parent, name, directory=True)
            except FileNotFoundError:
                if not create:
                    raise
                handle = self._open(parent, name, directory=True, create=True)
            stack.callback(self.disk.close, handle)
            parent = handle
        return parent

    def mkdir(self, parts):
        with ExitStack() as stack:
            self._directories(parts, stack, create=True)

    @contextmanager
    def create(self, parts):
        with ExitStack() as stack:
            parent = self._directories(parts[:-1], stack, create=True)
            handle = self._open(parent, parts[-1], directory=False, create=True,
                                access=_READ_DATA | _WRITE_DATA, share=0)
            with _stream(self.disk, handle, 'w+b') as stream:
                yield stream

    @contextmanager
    def held_file(self, parts):
        self.validate()
        with ExitStack() as stack:
            parent = self._directories(parts[:-1], stack)
            handle = self._open(parent, parts[-1], directory=False, access=_READ_DATA)
            stack.callback(self.disk.close, handle)
            yield self.path.joinpath(*parts)
        self.validate()

    def _scan(self, parent, path, *, remove, depth=0, budget):
        if depth > 64:
            raise OSError('Setup tree exceeds the safe traversal depth.')
        # Windows FindFirstFile supplies names; only held, relative NT opens
        # authorize reads or deletion. DirEntry metadata is not trusted.
        with os.scandir('\\\\?\\' + str(path)) as iterator:
            entries = []
            for entry in iterator:
                budget[0] -= 1
                if budget[0] < 0:
                    raise OSError('Setup tree exceeds the safe traversal entry limit.')
                entries.append((_component(entry.name), entry.is_dir(follow_symlinks=False)))
        for name, directory in entries:
            handle = self._open(parent, name, directory=directory,
                                access=0 if directory else _READ_DATA)
            try:
                identity = self.disk.identity(handle)
                if directory:
                    self._scan(handle, path / name, remove=remove, depth=depth + 1, budget=budget)
            finally:
                self.disk.close(handle)
            if remove:
                self._delete_member(parent, name, directory, identity)

    def _delete_member(self, parent, name, directory, identity):
        handle = self._open(parent, name, directory=directory, access=_DELETE)
        try:
            if self.disk.identity(handle) != identity:
                raise OSError('A setup entry changed before cleanup.')
            self.disk.remove(handle)
        finally:
            self.disk.close(handle)

    def audit(self):
        self.validate()
        self._scan(self.handle, self.path, remove=False, budget=[100_000])
        self.validate()

    def clear_work(self):
        self.validate()
        handle = self._open(self.handle, 'work', directory=True)
        try:
            identity = self.disk.identity(handle)
            self._scan(handle, self.path / 'work', remove=True, budget=[100_000])
        finally:
            self.disk.close(handle)
        self._delete_member(self.handle, 'work', True, identity)

    def commit(self, receipt):
        self.audit()
        data = (json.dumps(receipt, sort_keys=True) + '\n').encode('utf-8')
        # Hold DELETE on the exact freshly created file and publish a flushed
        # receipt relative to its verified parent, without replacing any name.
        handle = self._open(self.handle, 'receipt.pending', directory=False, create=True,
                            access=_READ_DATA | _WRITE_DATA | _DELETE, share=0)
        with _stream(self.disk, handle, 'w+b') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            self.disk.publish(handle, self.handle, 'complete.json')
            self.committed = True


@contextmanager
def _verified_stage(stage, rollback):
    try:
        stage.validate()
        yield stage
        if not stage.committed:
            raise RuntimeError('Windows setup did not commit a verified installation.')
    except SetupProcessCleanupUnconfirmed as error:
        raise RuntimeError(f'Process exit is unconfirmed; incomplete setup preserved at {stage.path}: {error}') from error
    except BaseException as error:
        if not stage.committed:
            try:
                rollback()
            except (OSError, ValueError) as cleanup:
                raise RuntimeError(f'{error}; safe rollback could not finish at {stage.path}: {cleanup}') from error
        raise


@contextmanager
def transaction(destination):
    """Prepare strict private parents, lock setup and own only the new generation."""
    path = Path(local_path_text(destination, target='win32'))
    if path.parts[-2:] != ('ComposerLint', 'setup'):
        raise ValueError('Expected the ComposerLint setup directory in Local AppData.')
    disk, security = _InstallDisk(), _SecurityAPI()
    sid = security.user_sid()
    with ExitStack() as stack, security.private_descriptor(sid) as descriptor:
        volume = disk.open_root(path.drive, stack)
        fs, flags = disk.volume_capabilities(volume)
        if fs.upper() != 'NTFS' or flags & _NTFS_FLAGS != _NTFS_FLAGS:
            raise OSError('Windows setup requires local NTFS with persistent ACLs and reparse points.')
        chain, private = [(volume, disk.identity(volume))], []
        for name in path.parts[1:-2]:
            handle = disk.open_existing(chain[-1][0], name, stack, directory=True)
            chain.append((handle, disk.identity(handle)))
        for name in path.parts[-2:]:
            try:
                handle = disk.open_existing(chain[-1][0], name, stack, directory=True,
                                            access=_READ_CONTROL)
            except FileNotFoundError:
                handle = disk.open_component(
                    chain[-1][0], name, access=_READ_ATTRIBUTES | _TRAVERSE | _READ_CONTROL,
                    share=_SHARE_READ, disposition=_FILE_CREATE,
                    options=_FILE_DIRECTORY_FILE, descriptor=descriptor, refuse_reparse=True)
                stack.callback(disk.close, handle)
            disk.check_type(handle, directory=True)
            security.require_private(handle, sid)
            private.append(handle)
            chain.append((handle, disk.identity(handle)))
        parent = chain[-1][0]
        with _PrivateDirectory(disk, security, sid, chain).exclusive_lock(), ExitStack() as generation:
            name = 'install-' + secrets.token_hex(10)
            with security.private_descriptor(sid, inheritable=True) as stage_descriptor:
                handle = disk.open_component(
                    parent, name, access=_READ_ATTRIBUTES | _TRAVERSE | _READ_CONTROL | _WRITE_DATA,
                    share=_SHARE_READ, disposition=_FILE_CREATE,
                    options=_FILE_DIRECTORY_FILE, descriptor=stage_descriptor, refuse_reparse=True)
            generation.callback(disk.close, handle)
            identity = disk.identity(handle)
            stage = Stage(path / name, disk, security, sid, chain + [(handle, identity)], private)

            def rollback():
                stage.validate()
                stage._scan(handle, stage.path, remove=True, budget=[100_000])
                generation.close()
                # Re-open only the recorded generation. A replacement or
                # reparse point cannot be recursively removed here.
                opened = disk.open_component(
                    parent, name, access=_DELETE | _TRAVERSE | _READ_CONTROL | _READ_ATTRIBUTES,
                    share=_SHARE_READ, disposition=_FILE_OPEN,
                    options=_FILE_DIRECTORY_FILE, refuse_reparse=True)
                try:
                    disk.check_type(opened, directory=True)
                    security.require_private(opened, sid, tree_root=True)
                    if disk.identity(opened) != identity:
                        raise OSError('Setup staging directory changed before rollback.')
                    disk.remove(opened)
                finally:
                    disk.close(opened)

            with _verified_stage(stage, rollback):
                yield stage
