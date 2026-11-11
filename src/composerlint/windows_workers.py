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


class WorkerCancelled(RuntimeError):
    pass


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


class _WorkerAPI:
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        p = ctypes.c_void_p
        self.close = _bind(kernel, 'CloseHandle', win.BOOL, win.HANDLE)
        self.create_job = _bind(kernel, 'CreateJobObjectW', win.HANDLE, p, win.LPCWSTR)
        self.set_job = _bind(kernel, 'SetInformationJobObject', win.BOOL,
                             win.HANDLE, ctypes.c_int, p, win.DWORD)
        self.query_job = _bind(kernel, 'QueryInformationJobObject', win.BOOL,
                               win.HANDLE, ctypes.c_int, p, win.DWORD, win.LPDWORD)
        self.terminate_job = _bind(kernel, 'TerminateJobObject', win.BOOL, win.HANDLE, win.UINT)
        self.in_job = _bind(kernel, 'IsProcessInJob', win.BOOL,
                            win.HANDLE, win.HANDLE, ctypes.POINTER(win.BOOL))
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

    def job(self, stack):
        handle = self.create_job(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(self.close, handle)
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.set_job(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def active_processes(self, job):
        accounting = _JobAccounting()
        if not self.query_job(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.active

    def stop_job(self, job):
        if not self.active_processes(job):
            return
        if not self.terminate_job(job, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + _CLEANUP_SECONDS
        while self.active_processes(job):
            if time.monotonic() >= deadline:
                raise RuntimeError('Windows did not confirm that the worker job is empty.')
            time.sleep(.01)

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
        member = win.BOOL()
        try:
            if not self.in_job(info.process, job, ctypes.byref(member)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not member.value:
                raise OSError('Windows worker is missing its assigned job.')
        except BaseException:
            # Only the exact newly created, still suspended process can reach
            # this path. A failure to confirm job membership cannot leave it alive.
            if winapi.WaitForSingleObject(info.process, 0) != 0:
                winapi.TerminateProcess(info.process, 1)
                if winapi.WaitForSingleObject(info.process, _CLEANUP_SECONDS * 1000) != 0:
                    raise RuntimeError('Unverified suspended worker did not terminate.')
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
        try:
            # Close the kill-on-close job before draining pipe completions,
            # including when explicit termination or accounting fails.
            with ExitStack() as jobs:
                job = api.job(jobs)
                jobs.callback(api.stop_job, job)
                with ExitStack() as children, security.private_descriptor(security.user_sid()) as descriptor:
                    pipes = [api.pipe(_winapi, descriptor, input_pipe=index == 0,
                                      parent_ends=parents, child_ends=children) for index in range(3)]
                    _check_deadline(deadline, cancelled)
                    process = api.launch(_winapi, command, job, [pair[1] for pair in pipes], process_handles)
                stdout, stderr = _exchange(_winapi, [pair[0] for pair in pipes], payload, deadline,
                                           cancelled, max_stdout, max_stderr, pending)
                while _winapi.WaitForSingleObject(process, 0) != 0:
                    time.sleep(min(.01, _check_deadline(deadline, cancelled)))
                _check_deadline(deadline, cancelled)
                if _winapi.GetExitCodeProcess(process):
                    raise RuntimeError('Windows worker failed: ' + stderr[-4000:].decode('utf-8', 'replace'))
                if stdout.count(b'\n') != 1 or not stdout.endswith(b'\n'):
                    raise ValueError('Expected exactly one LF-terminated Windows worker response.')
                return stdout
        finally:
            _cancel_operations(pending)
