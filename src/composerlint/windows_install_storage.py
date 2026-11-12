"""NTFS staging for the Windows setup pilot; no runtime or export callers."""

from contextlib import ExitStack, contextmanager, nullcontext
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
    _WRITE_DATA, private_directory,
)
from .windows_read import (
    _IoStatusBlock, _OPEN_REPARSE_POINT, _READ_ATTRIBUTES, _READ_DATA, _SHARE_READ,
    _TRAVERSE, _bind, _stream,
)
from .windows_workers import SetupProcessCleanupUnconfirmed


_PENDING_RECEIPT = 'receipt.pending'
_COMPLETE_RECEIPT = 'complete.json'


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

    def publish(self, handle):
        encoded = _COMPLETE_RECEIPT.encode('utf-16-le')
        size = ctypes.sizeof(_RenameInfo) + len(encoded)
        buffer = ctypes.create_string_buffer(size)
        info = _RenameInfo.from_buffer(buffer)
        # A bare name and null RootDirectory rename within the source's
        # directory without reopening it for FILE_ADD_FILE. Keep no-replace.
        info.length = len(encoded)
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
    def __init__(self, path, disk, security, sid, chain, private, *, workspace_members=None):
        self.path, self.disk, self.security, self.sid = path, disk, security, sid
        self.chain, self.private = chain, private
        self.handle, self.identity = chain[-1]
        self.committed = False
        self.work_cleanup_incomplete = False
        self.receipt_started = False
        self.workspace_members = workspace_members

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
        descriptor_scope = (self.security.private_descriptor(self.sid, inheritable=directory)
                            if create else nullcontext())
        with descriptor_scope as descriptor:
            handle = self.disk.open_component(
                parent, _component(name), access=access | _READ_ATTRIBUTES | _READ_CONTROL |
                (_TRAVERSE if directory else 0),
                share=_SHARE_READ if share is None else share,
                disposition=_FILE_CREATE if create else _FILE_OPEN,
                options=_FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE | _OPEN_REPARSE_POINT,
                descriptor=descriptor, refuse_reparse=True)
        try:
            self.disk.check_type(handle, directory=directory)
            self.security.require_private(handle, self.sid, tree_root=directory,
                                          allow_inherited=not create)
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
    def held_directory(self, parts):
        self.validate()
        with ExitStack() as stack:
            handle = self._directories(parts, stack)
            self.security.require_private(handle, self.sid, tree_root=True)
            yield self.path.joinpath(*parts)
        self.validate()

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

    def remove(self, parts, *, directory=False):
        self.validate()
        with ExitStack() as stack:
            parent = self._directories(parts[:-1], stack)
            try:
                handle = self._open(parent, parts[-1], directory=directory, access=_DELETE)
            except FileNotFoundError:
                return
            stack.callback(self.disk.close, handle)
            self.disk.remove(handle)

    def _scan(self, parent, path, *, remove, depth=0, budget, verified=None):
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
                name, directory = _component(entry.name), entry.is_dir(follow_symlinks=False)
                parts = (path / name).relative_to(self.path).parts if verified is not None else None
                if parts is not None and self.workspace_members.get(parts) is not directory:
                    raise PermissionError(f'Unexpected Windows workspace member: {path / name}')
                entries.append((name, directory, parts))
        for name, directory, parts in entries:
            try:
                handle = self._open(parent, name, directory=directory,
                                    access=0 if directory else _READ_DATA)
            except OSError as error:
                error.add_note(f'Could not verify Windows workspace member: {path / name}')
                raise
            try:
                identity = self.disk.identity(handle)
                if parts is not None:
                    if remove and verified.get(parts) != identity:
                        raise PermissionError(f'Windows workspace member changed after audit: {path / name}')
                    if not remove:
                        verified[parts] = identity
                if directory:
                    self._scan(handle, path / name, remove=remove, depth=depth + 1,
                               budget=budget, verified=verified)
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
        verified = {} if self.workspace_members is not None else None
        self._scan(self.handle, self.path, remove=False, budget=[100_000], verified=verified)
        self.validate()
        return verified

    def clear_work(self):
        if self.work_cleanup_incomplete:
            raise RuntimeError('Setup work-directory cleanup has already failed.')
        self.work_cleanup_incomplete = True
        self.validate()
        handle = self._open(self.handle, 'work', directory=True)
        try:
            identity = self.disk.identity(handle)
            self._scan(handle, self.path / 'work', remove=True, budget=[100_000])
        finally:
            self.disk.close(handle)
        self._delete_member(self.handle, 'work', True, identity)
        self.work_cleanup_incomplete = False

    def _verify_receipt(self, handle, stream, expected):
        stamp = self.disk.stamp(handle)
        stream.seek(0)
        if stamp.links != 1 or stamp.size != len(expected) or stream.read(len(expected) + 1) != expected:
            raise OSError('Setup receipt bytes or link count changed.')
        self.security.require_private(handle, self.sid)
        if self.disk.stamp(handle) != stamp:
            raise OSError('Setup receipt changed during verification.')
        return stamp.volume, stamp.file_id

    def commit(self, receipt):
        if self.work_cleanup_incomplete:
            raise RuntimeError('Setup work-directory cleanup is incomplete.')
        if self.receipt_started:
            raise RuntimeError('Setup receipt publication has already been attempted.')
        self.receipt_started = True
        self.audit()
        data = (json.dumps(receipt, sort_keys=True) + '\n').encode('utf-8')
        # The exclusive handle pins the newly created file through the rename.
        handle = self._open(self.handle, _PENDING_RECEIPT, directory=False, create=True,
                            access=_READ_DATA | _WRITE_DATA | _DELETE, share=0)
        with _stream(self.disk, handle, 'w+b') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            identity = self._verify_receipt(handle, stream, data)
            self.validate()
            self.disk.publish(handle)
        # Reopen by the final name through the still-held staging directory.
        # The old handle must close first because it denies all sharing.
        handle = self._open(self.handle, _COMPLETE_RECEIPT, directory=False, access=_READ_DATA)
        with _stream(self.disk, handle, 'rb') as stream:
            if self._verify_receipt(handle, stream, data) != identity:
                raise OSError('Published setup receipt has a different file identity.')
            self.validate()
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
            if stage.work_cleanup_incomplete:
                raise RuntimeError(
                    f'Work-directory cleanup could not finish; incomplete setup preserved at {stage.path}: {error}'
                ) from error
            if stage.receipt_started:
                raise RuntimeError(
                    f'Receipt publication could not be verified; incomplete setup preserved at {stage.path}: {error}'
                ) from error
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


@contextmanager
def pinned_install_files(root):
    """Verify a private install tree and hold the requested NTFS objects open."""
    path = Path(root)
    with private_directory(path, tree_root=True) as directory, ExitStack() as pins:
        stage = Stage(path, directory._api, directory._security, directory._sid, directory._chain, [])
        folders = {(): stage.handle}

        def read(parts, *, limit):
            parent = stage.handle
            for depth, name in enumerate(parts[:-1], 1):
                prefix = parts[:depth]
                if prefix not in folders:
                    handle = stage._open(parent, name, directory=True)
                    pins.callback(stage.disk.close, handle)
                    folders[prefix] = handle
                parent = folders[prefix]
            handle = stage._open(parent, parts[-1], directory=False, access=_READ_DATA)
            stream = pins.enter_context(_stream(stage.disk, handle, 'rb'))
            stamp = stage.disk.stamp(handle)
            if stamp.size > limit:
                raise ValueError('Pinned installation file exceeds its size limit.')
            data = stream.read(stamp.size + 1)
            if len(data) != stamp.size or stage.disk.stamp(handle) != stamp:
                raise OSError('Pinned installation file changed while being read.')
            return data

        yield read, stage.validate


@contextmanager
def private_workspace(parent, prefix, *, members=None):
    """Own a new scratch tree, optionally requiring a fixed inventory for cleanup."""
    path = Path(parent) / (prefix + secrets.token_hex(16))
    local_path_text(path, target='win32')
    disk, security = _InstallDisk(), _SecurityAPI()
    sid = security.user_sid()
    with ExitStack() as parents, ExitStack() as owned:
        chain = disk.ancestors(path, parents)
        if members is not None:
            fs, flags = disk.volume_capabilities(chain[0][0])
            if fs.upper() != 'NTFS' or flags & _NTFS_FLAGS != _NTFS_FLAGS:
                raise OSError('The Windows probe workspace requires local NTFS with persistent ACLs and reparse checks.')
        root = chain[-1][0]
        with security.private_descriptor(sid, inheritable=True) as descriptor:
            handle = disk.open_component(
                root, path.name, access=_READ_ATTRIBUTES | _TRAVERSE | _READ_CONTROL,
                share=_SHARE_READ, disposition=_FILE_CREATE,
                options=_FILE_DIRECTORY_FILE, descriptor=descriptor, refuse_reparse=True)
        owned.callback(disk.close, handle)
        identity = disk.identity(handle)
        stage = Stage(path, disk, security, sid, chain + [(handle, identity)], [],
                      workspace_members=None if members is None else dict(members))
        stage.validate()

        def cleanup():
            try:
                verified = stage.audit()
                stage._scan(handle, path, remove=True, budget=[100_000], verified=verified)
                owned.close()
                stage._delete_member(root, path.name, True, identity)
            except BaseException as error:
                error.add_note(f'Safe cleanup could not finish; Windows workspace is incomplete: {path}')
                raise

        try:
            yield stage
        except SetupProcessCleanupUnconfirmed as error:
            error.add_note(f'Process exit is unconfirmed; Windows workspace preserved at: {path}')
            raise
        except BaseException:
            cleanup()
            raise
        else:
            cleanup()
