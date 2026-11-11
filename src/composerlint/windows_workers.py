"""Experimental one-request Windows workers with owned jobs and bounded pipes."""

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as win
import math
import os
import secrets
import subprocess
import sys
import time

from .platform_support import PipeLimitError, UnsupportedPlatformOperation, local_path_text
from .windows_private import _SecurityAPI, _SecurityAttributes
from .windows_read import _bind


_CHUNK = 16 * 1024
_LIMIT = 64 * 1024 * 1024
_CLEANUP_SECONDS = 5
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_BROKEN_PIPE = (109, 232, 233)
_IO_INCOMPLETE = 996
_JOB_LIST = 0x0002000D
_HANDLE_LIST = 0x00020002
_PROCESS_QUERY_AND_WAIT = 0x00101000  # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
_ERROR_MORE_DATA = 234
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF


class WorkerCancelled(RuntimeError):
    pass


class WorkerCleanupTimeout(TimeoutError):
    """The cleanup deadline expired before Windows confirmed termination."""


class WorkerTerminationUnconfirmed(RuntimeError):
    """The handles available to cleanup do not establish complete termination."""


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', win.DWORD), ('working_set_min', ctypes.c_size_t),
                ('working_set_max', ctypes.c_size_t), ('active_process_limit', win.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', win.DWORD),
                ('scheduling', win.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io_counters', ctypes.c_uint64 * 6),
                ('process_memory_limit', ctypes.c_size_t), ('job_memory_limit', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class _JobAccounting(ctypes.Structure):
    _fields_ = [('times', ctypes.c_int64 * 4), ('page_faults', win.DWORD),
                ('total', win.DWORD), ('active', win.DWORD), ('terminated', win.DWORD)]


class _JobProcessList(ctypes.Structure):
    _fields_ = [('assigned', win.DWORD), ('listed', win.DWORD),
                ('process_ids', ctypes.c_size_t * 1)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [('size', win.DWORD), ('reserved', win.LPWSTR), ('desktop', win.LPWSTR),
                ('title', win.LPWSTR), ('dimensions', win.DWORD * 7), ('flags', win.DWORD),
                ('show_window', win.WORD), ('reserved_size', win.WORD),
                ('reserved_data', ctypes.c_void_p), ('stdin', win.HANDLE),
                ('stdout', win.HANDLE), ('stderr', win.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [('startup', _StartupInfo), ('attributes', ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [('process', win.HANDLE), ('thread', win.HANDLE),
                ('pid', win.DWORD), ('tid', win.DWORD)]


def _cleanup_remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WorkerCleanupTimeout('Windows worker termination was not confirmed within the cleanup deadline.')
    return remaining


def _api_error(operation, code=None):
    error = ctypes.WinError(ctypes.get_last_error() if code is None else code)
    error.add_note(f'{operation} failed during Windows worker lifecycle.')
    return error


class _WorkerAPI:
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        p = ctypes.c_void_p
        self._close = _bind(kernel, 'CloseHandle', win.BOOL, win.HANDLE)
        self.create_job = _bind(kernel, 'CreateJobObjectW', win.HANDLE, p, win.LPCWSTR)
        self.set_job = _bind(kernel, 'SetInformationJobObject', win.BOOL,
                             win.HANDLE, ctypes.c_int, p, win.DWORD)
        self.query_job = _bind(kernel, 'QueryInformationJobObject', win.BOOL,
                               win.HANDLE, ctypes.c_int, p, win.DWORD, win.LPDWORD)
        self.terminate_job = _bind(kernel, 'TerminateJobObject', win.BOOL, win.HANDLE, win.UINT)
        self.in_job = _bind(kernel, 'IsProcessInJob', win.BOOL,
                            win.HANDLE, win.HANDLE, ctypes.POINTER(win.BOOL))
        self.open_process = _bind(kernel, 'OpenProcess', win.HANDLE,
                                  win.DWORD, win.BOOL, win.DWORD)
        self.process_id = _bind(kernel, 'GetProcessId', win.DWORD, win.HANDLE)
        self.wait = _bind(kernel, 'WaitForSingleObject', win.DWORD, win.HANDLE, win.DWORD)
        self.resume = _bind(kernel, 'ResumeThread', win.DWORD, win.HANDLE)
        self.init_attributes = _bind(kernel, 'InitializeProcThreadAttributeList', win.BOOL,
                                     p, win.DWORD, win.DWORD, ctypes.POINTER(ctypes.c_size_t))
        self.update_attribute = _bind(kernel, 'UpdateProcThreadAttribute', win.BOOL,
                                      p, win.DWORD, ctypes.c_size_t, p, ctypes.c_size_t, p, p)
        self.delete_attributes = _bind(kernel, 'DeleteProcThreadAttributeList', None, p)
        self.create_process = _bind(kernel, 'CreateProcessW', win.BOOL, win.LPCWSTR,
                                    win.LPWSTR, p, p, win.BOOL, win.DWORD, p,
                                    win.LPCWSTR, ctypes.POINTER(_StartupInfoEx),
                                    ctypes.POINTER(_ProcessInfo))
        self.create_pipe = _bind(kernel, 'CreateNamedPipeW', win.HANDLE, win.LPCWSTR,
                                 win.DWORD, win.DWORD, win.DWORD, win.DWORD, win.DWORD,
                                 win.DWORD, ctypes.POINTER(_SecurityAttributes))
        self.open_pipe = _bind(kernel, 'CreateFileW', win.HANDLE, win.LPCWSTR,
                               win.DWORD, win.DWORD, ctypes.POINTER(_SecurityAttributes),
                               win.DWORD, win.DWORD, win.HANDLE)
        self.pipe_client = _bind(kernel, 'GetNamedPipeClientProcessId', win.BOOL,
                                 win.HANDLE, win.LPDWORD)

    def close(self, handle):
        if not self._close(handle):
            raise _api_error('CloseHandle')

    def job(self, stack):
        # An unnamed, noninheritable handle owned by this scope. JOB_LIST
        # assigns membership; only HANDLE_LIST passes handles to the child.
        handle = self.create_job(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(self.close, handle)
        if os.get_handle_inheritable(handle):
            raise WorkerTerminationUnconfirmed('The Windows worker job handle is inheritable.')
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.set_job(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def accounting(self, job):
        accounting = _JobAccounting()
        if not self.query_job(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise _api_error('QueryInformationJobObject (accounting)')
        return accounting

    def process_ids(self, job, capacity, deadline):
        offset = _JobProcessList.process_ids.offset
        while True:
            _cleanup_remaining(deadline)
            size = offset + capacity * ctypes.sizeof(ctypes.c_size_t)
            if size > 0xFFFFFFFF:
                raise WorkerTerminationUnconfirmed('The Windows job process list exceeds the native buffer size.')
            buffer = ctypes.create_string_buffer(size)
            header = _JobProcessList.from_buffer(buffer)
            # JobObjectBasicProcessIdList has a variable-length PID array.
            if not self.query_job(job, 3, buffer, size, None):
                error = ctypes.get_last_error()
                if error != _ERROR_MORE_DATA or header.assigned <= capacity:
                    raise _api_error('QueryInformationJobObject (process list)', error)
            if header.assigned > capacity:
                capacity = header.assigned
                continue
            if header.listed != header.assigned:
                raise WorkerTerminationUnconfirmed('Windows returned an incomplete job process list.')
            pids = (ctypes.c_size_t * header.listed).from_buffer(buffer, offset)
            return tuple(pids)

    def is_job_member(self, process, job):
        member = win.BOOL()
        if not self.in_job(process, job, ctypes.byref(member)):
            raise _api_error('IsProcessInJob')
        return bool(member.value)

    def wait_for_exit(self, process, deadline):
        milliseconds = math.floor(_cleanup_remaining(deadline) * 1000)
        result = self.wait(process, milliseconds)
        if result == _WAIT_TIMEOUT:
            raise WorkerCleanupTimeout('A Windows worker process did not exit within the cleanup deadline.')
        if result == _WAIT_FAILED:
            raise _api_error('WaitForSingleObject (worker exit)')
        if result != _WAIT_OBJECT_0:
            raise WorkerTerminationUnconfirmed(f'Unexpected Windows process wait result: {result}.')

    def stop_job(self, job, leader):
        deadline = time.monotonic() + _CLEANUP_SECONDS
        processes = [] if leader is None else [leader]
        with ExitStack() as held:
            try:
                leader_pid = None
                if leader is not None:
                    leader_pid = self.process_id(leader)
                    if not leader_pid:
                        raise _api_error('GetProcessId (worker leader)')
                accounting = self.accounting(job)
                for pid in self.process_ids(job, max(1, accounting.active), deadline):
                    _cleanup_remaining(deadline)
                    if not 0 < pid <= 0xFFFFFFFF:
                        raise WorkerTerminationUnconfirmed('Windows returned an invalid job process ID.')
                    if pid == leader_pid:
                        continue  # The verified creation handle is already held.
                    process = self.open_process(_PROCESS_QUERY_AND_WAIT, False, pid)
                    if not process:
                        raise _api_error('OpenProcess (job member)')
                    held.callback(self.close, process)
                    # A PID may have been recycled between the list and open.
                    if not self.is_job_member(process, job):
                        raise WorkerTerminationUnconfirmed('A listed Windows process is no longer in the worker job.')
                    processes.append(process)
            finally:
                # Explicit termination works even if an extra job handle is
                # still open. The outer scope also closes its kill-on-close
                # handle if discovery, termination, or confirmation fails.
                if not self.terminate_job(job, 1):
                    raise _api_error('TerminateJobObject')
                for process in processes:
                    self.wait_for_exit(process, deadline)
            # A process born during shutdown may have escaped the snapshot.
            # The lifetime count can detect this even if it already exited.
            if self.accounting(job).total != accounting.total:
                raise WorkerTerminationUnconfirmed('Windows job membership changed during termination.')
            _cleanup_remaining(deadline)

    def pipe(self, winapi, descriptor, *, input_pipe, parent_ends, child_ends):
        address = r'\\.\pipe\ComposerLint-' + secrets.token_hex(16)
        security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        access = 2 if input_pipe else 1  # PIPE_ACCESS_OUTBOUND or PIPE_ACCESS_INBOUND
        open_flags = access | 0x40000000 | 0x00080000  # overlapped, first instance
        server = self.create_pipe(address, open_flags, 8, 1, _CHUNK, _CHUNK, 0,
                                  ctypes.byref(security))  # PIPE_REJECT_REMOTE_CLIENTS
        if server == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        parent_ends.callback(self.close, server)

        security.inherit = True
        client_access = 0x80000000 if input_pipe else 0x40000000  # GENERIC_READ or GENERIC_WRITE
        client = self.open_pipe(address, client_access, 0, ctypes.byref(security), 3,
                                0x00100000, None)  # SECURITY_SQOS_PRESENT | SECURITY_ANONYMOUS
        if client == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        child_ends.callback(self.close, client)
        connection = winapi.ConnectNamedPipe(server, overlapped=True)
        _, error = connection.GetOverlappedResult(True)
        if error:
            raise ctypes.WinError(error)
        pid = win.DWORD()
        if not self.pipe_client(server, ctypes.byref(pid)):
            raise ctypes.WinError(ctypes.get_last_error())
        if pid.value != os.getpid():
            raise PermissionError('Unexpected client connected to the Windows worker pipe.')
        return server, client

    @contextmanager
    def startup(self, job, handles):
        size = ctypes.c_size_t()
        self.init_attributes(None, 2, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = ctypes.create_string_buffer(size.value)
        if not self.init_attributes(attributes, 2, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            jobs = (win.HANDLE * 1)(job)
            inherited = (win.HANDLE * 3)(*handles)
            for key, values in ((_JOB_LIST, jobs), (_HANDLE_LIST, inherited)):
                if not self.update_attribute(attributes, 0, key, values, ctypes.sizeof(values),
                                             None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
            startup = _StartupInfoEx()
            startup.startup.size = ctypes.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = ctypes.addressof(attributes)
            yield startup
        finally:
            self.delete_attributes(attributes)

    def launch(self, winapi, command, job, handles, stack):
        info = _ProcessInfo()
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        with self.startup(job, handles) as startup:
            # Assign the job at creation. Start suspended to verify membership
            # before running worker code, even on runners using nested jobs.
            flags = 0x00080000 | 0x08000000 | 0x00000004
            if not self.create_process(command[0], line, None, None, True, flags, None, None,
                                       ctypes.byref(startup), ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(self.close, info.process)
        stack.callback(self.close, info.thread)
        try:
            if not self.is_job_member(info.process, job):
                raise OSError('Windows worker is missing its assigned job.')
        except BaseException:
            # Only the exact newly created, still suspended process can reach
            # this path. A failure to confirm job membership cannot leave it alive.
            if winapi.WaitForSingleObject(info.process, 0) != _WAIT_OBJECT_0:
                winapi.TerminateProcess(info.process, 1)
                self.wait_for_exit(info.process, time.monotonic() + _CLEANUP_SECONDS)
            raise
        if self.resume(info.thread) != 1:
            raise OSError('Windows worker thread could not be resumed safely.')
        return info.process


def _check_deadline(deadline, cancelled):
    if cancelled is not None and cancelled.is_set():
        raise WorkerCancelled('Windows worker was cancelled.')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Windows worker exceeded its deadline.')
    return remaining


def _operation(winapi, handle, *, data=None):
    try:
        if data is not None:
            operation, _ = winapi.WriteFile(handle, data, overlapped=True)
        else:
            operation, _ = winapi.ReadFile(handle, _CHUNK, overlapped=True)
        return operation
    except OSError as error:
        if data is None and error.winerror in _BROKEN_PIPE:
            return None
        raise


def _cancel_operations(pending):
    # CancelIoEx is asynchronous. Drain completion before releasing any
    # OVERLAPPED buffer. Attempt every cancellation even if one fails.
    with ExitStack() as completions, ExitStack() as cancellations:
        for operation in pending.values():
            completions.callback(_drain_operation, operation)
            cancellations.callback(operation.cancel)


def _drain_operation(operation):
    try:
        operation.GetOverlappedResult(True)
    except OSError as error:
        if error.winerror not in _BROKEN_PIPE:
            raise


def _exchange(winapi, handles, payload, deadline, cancelled, max_stdout, max_stderr, pending):
    stdin, stdout, stderr = handles
    outputs = {stdout: bytearray(), stderr: bytearray()}
    limits = {stdout: max_stdout, stderr: max_stderr}
    offset = 0
    for handle in (stdout, stderr):
        operation = _operation(winapi, handle)
        if operation is not None:
            pending[handle] = operation
    pending[stdin] = _operation(winapi, stdin, data=payload[:_CHUNK])
    while pending:
        _check_deadline(deadline, cancelled)
        progressed = False
        for handle, operation in tuple(pending.items()):
            try:
                count, error = operation.GetOverlappedResult(False)
            except OSError as exc:
                if handle == stdin or exc.winerror not in _BROKEN_PIPE:
                    raise
                count, error = 0, 0
            if error == _IO_INCOMPLETE:
                continue
            del pending[handle]
            if error:
                raise ctypes.WinError(error)
            progressed = True
            if handle == stdin:
                if not count:
                    raise OSError('Windows worker did not accept request data.')
                offset += count
                if offset < len(payload):
                    pending[handle] = _operation(winapi, handle, data=payload[offset:offset + _CHUNK])
            elif count:
                buffer = outputs[handle]
                if len(buffer) + count > limits[handle]:
                    stream = 'stdout' if handle == stdout else 'stderr'
                    raise PipeLimitError(f'Windows worker exceeded the {stream} byte limit.')
                buffer.extend(operation.getbuffer())
                next_operation = _operation(winapi, handle)
                if next_operation is not None:
                    pending[handle] = next_operation
        if pending and not progressed:
            remaining = _check_deadline(deadline, cancelled)
            winapi.WaitForMultipleObjects([op.event for op in pending.values()], False,
                                           max(1, min(25, math.ceil(remaining * 1000))))
    return bytes(outputs[stdout]), bytes(outputs[stderr])


def run_worker(command, payload, *, timeout, cancelled=None, max_request=_LIMIT,
               max_stdout=_LIMIT, max_stderr=256 * 1024):
    """Run a trusted local executable exchanging exactly one LF-framed request and reply."""
    if sys.platform != 'win32':
        raise UnsupportedPlatformOperation('Windows worker pilot requires native Windows.')
    import _winapi

    if not isinstance(command, (list, tuple)) or not command:
        raise ValueError('A nonempty Windows worker argument array is required.')
    if any(not isinstance(part, str) or '\x00' in part for part in command):
        raise ValueError('Windows worker arguments must be text without NUL.')
    executable = local_path_text(command[0], target='win32')
    if not executable.lower().endswith('.exe'):
        raise ValueError('A fully qualified Windows worker .exe is required.')
    for limit in (max_request, max_stdout, max_stderr):
        if type(limit) is not int or not 0 <= limit <= _LIMIT:
            raise ValueError('Windows worker byte limits must be between zero and 64 MiB.')
    if not isinstance(payload, bytes) or len(payload) > max_request:
        raise PipeLimitError('Windows worker exceeded the request byte limit.')
    if payload.count(b'\n') != 1 or not payload.endswith(b'\n'):
        raise ValueError('Expected one LF-terminated Windows worker request.')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('A finite positive Windows worker timeout is required.')

    deadline = time.monotonic() + timeout
    _check_deadline(deadline, cancelled)
    api, security = _WorkerAPI(), _SecurityAPI()
    with ExitStack() as parents, ExitStack() as process_handles:
        pending = {}
        process = None
        try:
            # Close the kill-on-close job before draining pipe completions,
            # including when explicit termination or accounting fails.
            with ExitStack() as jobs:
                job = api.job(jobs)
                jobs.callback(lambda: api.stop_job(job, process))
                with ExitStack() as children, security.private_descriptor(security.user_sid()) as descriptor:
                    pipes = [api.pipe(_winapi, descriptor, input_pipe=index == 0,
                                      parent_ends=parents, child_ends=children) for index in range(3)]
                    _check_deadline(deadline, cancelled)
                    process = api.launch(_winapi, command, job, [pair[1] for pair in pipes], process_handles)
                stdout, stderr = _exchange(_winapi, [pair[0] for pair in pipes], payload, deadline,
                                           cancelled, max_stdout, max_stderr, pending)
                while True:
                    remaining = _check_deadline(deadline, cancelled)
                    interval = max(1, min(25, math.ceil(remaining * 1000)))
                    if _winapi.WaitForSingleObject(process, interval) == _WAIT_OBJECT_0:
                        break
                _check_deadline(deadline, cancelled)
                if _winapi.GetExitCodeProcess(process):
                    raise RuntimeError('Windows worker failed: ' + stderr[-4000:].decode('utf-8', 'replace'))
                if stdout.count(b'\n') != 1 or not stdout.endswith(b'\n'):
                    raise ValueError('Expected exactly one LF-terminated Windows worker response.')
                return stdout
        finally:
            _cancel_operations(pending)
