"""Native pilot controls. Run with isolated CPython and the standard library."""

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes as win
import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows')
class WindowsPilotCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        kernel.GetCurrentProcess.restype = win.HANDLE
        kernel.IsWow64Process2.argtypes = (win.HANDLE, ctypes.POINTER(win.USHORT),
                                          ctypes.POINTER(win.USHORT))
        kernel.IsWow64Process2.restype = win.BOOL
        process, machine = win.USHORT(), win.USHORT()
        if not kernel.IsWow64Process2(kernel.GetCurrentProcess(), ctypes.byref(process),
                                     ctypes.byref(machine)):
            raise ctypes.WinError(ctypes.get_last_error())
        if process.value or machine.value != 0x8664:
            raise RuntimeError('The pilot requires native Windows x64 Python.')
        print(f'Windows version {sys.getwindowsversion()}; '
              f'{platform.python_implementation()} {platform.python_version()}; '
              f'runner image {os.environ.get("ImageVersion", "local")}', flush=True)

        # Exercise both source modules without importing the app or its dependencies.
        name = '_composerlint_windows_pilot'
        spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(Path(__file__).resolve().parents[1] /
                                               'src' / 'composerlint')]
        sys.modules[name] = importlib.util.module_from_spec(spec)
        cls.addClassCleanup(lambda: sys.modules.pop(name, None))
        for alias, source in (('reader', 'windows_read'), ('host', 'platform_support'),
                              ('storage', 'windows_private'), ('workers', 'windows_workers')):
            module = importlib.import_module(f'{name}.{source}')
            setattr(cls, alias, module)
            cls.addClassCleanup(sys.modules.pop, module.__name__, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='composerlint Windows pilot ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / '音楽 🎵'
        self.folder.mkdir()
        self.score = self.folder / 'Étude.musicxml'
        self.data = b'<score-partwise/>\r\n\x1a'
        self.score.write_bytes(self.data)


class WindowsReadTests(WindowsPilotCase):
    def test_reads_binary_bytes_from_unicode_and_mixed_separator_paths(self):
        for path in (self.score, str(self.score).replace('\\', '/')):
            with self.subTest(path=path), self.reader.open_regular(path) as stream:
                self.assertFalse(os.get_inheritable(stream.fileno()))
                self.assertEqual(stream.read(), self.data)
        self.assertEqual(self.score.read_bytes(), self.data)

    def test_long_path_uses_relative_opens(self):
        extended_root = Path('\\\\?\\' + str(self.root))
        start = extended_root / ('x' * 120)
        start.mkdir()
        self.addCleanup(shutil.rmtree, start)
        leaf = start / ('y' * 120) / 'long.musicxml'
        leaf.parent.mkdir(parents=True)
        leaf.write_bytes(self.data)
        raw = str(leaf)[4:]
        self.assertGreater(len(raw), 260)
        with self.reader.open_regular(raw) as stream:
            self.assertEqual(stream.read(), self.data)

    def test_rejects_junction_in_path(self):
        from _winapi import CreateJunction

        junction = self.root / 'junction'
        CreateJunction(str(self.folder), str(junction))
        self.addCleanup(junction.rmdir)
        redirected = junction / self.score.name
        self.assertEqual(redirected.read_bytes(), self.data)
        with self.assertRaisesRegex(OSError, 'reparse points'):
            with self.reader.open_regular(redirected):
                self.fail('Junction target was opened')

    def test_refuses_unsafe_syntax_missing_files_and_directories(self):
        for path in (r'C:relative.xml', r'\\server\share\score.xml', r'\\?\C:\a.xml',
                     str(self.score) + ':stream', str(self.score) + '.', self.folder / 'NUL.xml'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                with self.reader.open_regular(path):
                    self.fail('Ambiguous path was opened')
        with self.assertRaises(FileNotFoundError):
            with self.reader.open_regular(self.folder / 'missing.xml'):
                self.fail('Missing file was opened')
        with self.assertRaises(OSError):
            with self.reader.open_regular(self.folder):
                self.fail('Directory was opened as a file')

    def test_sharing_guards_release_after_exception(self):
        moved_file = self.score.with_name('renamed.musicxml')
        moved_folder = self.folder.with_name('renamed')
        with self.assertRaisesRegex(RuntimeError, 'end the read'):
            with self.reader.open_regular(self.score) as stream:
                for source, destination in ((self.score, moved_file), (self.folder, moved_folder)):
                    with self.subTest(source=source), self.assertRaises(PermissionError):
                        source.rename(destination)
                with self.assertRaises(PermissionError):
                    self.score.open('r+b').close()
                self.assertEqual(stream.read(), self.data)
                raise RuntimeError('end the read')
        self.score.rename(moved_file)
        self.folder.rename(moved_folder)
        self.assertEqual((moved_folder / moved_file.name).read_bytes(), self.data)

    def test_existing_application_gates_remain_closed(self):
        recipe = self.host.setup_recipe()
        self.assertEqual(recipe.target, 'windows-x64')
        self.assertEqual(recipe.python_relative, 'Scripts/python.exe')
        self.assertFalse(recipe.installable)
        for operation in ('Private filesystem access', 'Protected score-file reading',
                          'Exclusive setup locking', 'Owned process-tree lifecycle',
                          'Worker pipe transport', 'Safe MusicXML export'):
            with self.subTest(operation=operation):
                with self.assertRaises(self.host.UnsupportedPlatformOperation):
                    self.host.require_security(operation)
        self.assertIsNone(self.host.approval_method())


class WindowsStorageTests(WindowsPilotCase):
    def test_private_directory_scratch_and_exception_cleanup(self):
        path = self.root / 'private'
        with self.storage.private_directory(path, create=True) as directory:
            with self.assertRaisesRegex(RuntimeError, 'discard scratch'):
                with directory.temporary_file('scratch.xml') as stream:
                    stream.write(self.data)
                    stream.seek(0)
                    self.assertEqual(stream.read(), self.data)
                    self.assertFalse(os.get_inheritable(stream.fileno()))
                    raise RuntimeError('discard scratch')
            self.assertFalse((path / 'scratch.xml').exists())
            with directory.temporary_file('scratch.xml') as stream:
                stream.write(b'retry')
                stream.close()
            self.assertFalse((path / 'scratch.xml').exists())
            with ThreadPoolExecutor(max_workers=1) as executor:
                read = executor.submit(directory.read_file, 'scratch.xml', limit=100)
                with self.assertRaisesRegex(ValueError, 'owning thread'):
                    read.result(timeout=5)
        with self.assertRaisesRegex(ValueError, 'scope has ended'):
            with directory.exclusive_lock():
                self.fail('Closed directory handle was reused')
        with self.storage.private_directory(path):
            pass
        self.assertEqual(self.score.read_bytes(), self.data)

    def _widen_dacl(self, path):
        security = self.storage._SecurityAPI()
        sid = security.user_sid()
        api = ctypes.WinDLL('advapi32.dll', use_last_error=True, winmode=0x0800)
        change = api.SetFileSecurityW
        change.argtypes = win.LPCWSTR, win.DWORD, ctypes.c_void_p
        change.restype = win.BOOL
        with security.descriptor(f'D:P(A;;FA;;;{sid})(A;;FR;;;WD)') as descriptor:
            if not change(str(path), 0x80000004, descriptor):
                raise ctypes.WinError(ctypes.get_last_error())

    def test_refuses_unprotected_directory_wrong_owner_and_extra_trustees(self):
        with self.assertRaises(PermissionError):
            with self.storage.private_directory(self.folder):
                self.fail('An inherited DACL was accepted')
        path = self.root / 'private'
        with self.storage.private_directory(path, create=True) as directory:
            security = self.storage._SecurityAPI()
            with self.assertRaisesRegex(PermissionError, 'owner differs'):
                security.require_private(directory._validate(), 'S-1-1-0')
            with directory.exclusive_lock():
                pass
        self._widen_dacl(path / 'setup.lock')
        with self.storage.private_directory(path) as directory:
            with self.assertRaisesRegex(PermissionError, 'Unexpected (trustee|ACE)'):
                directory.read_file('setup.lock', limit=100)
        self._widen_dacl(path)
        with self.assertRaisesRegex(PermissionError, 'Unexpected (trustee|ACE)'):
            with self.storage.private_directory(path):
                self.fail('A directory granting Everyone read access was accepted')

    def test_lock_excludes_another_process_and_releases_after_failure(self):
        path = self.root / 'private'
        lock = path / 'setup.lock'
        probe = r'''
import _winapi, sys
try:
    handle = _winapi.CreateFile(sys.argv[1], 0x80000000, 7, 0, 3, 0, 0)
except OSError as error:
    if error.winerror != 32:
        raise
else:
    _winapi.CloseHandle(handle)
    raise SystemExit("unexpected competing open")
'''
        with self.storage.private_directory(path, create=True) as directory:
            with self.assertRaisesRegex(RuntimeError, 'end the lock'):
                with directory.exclusive_lock():
                    with self.assertRaises(BlockingIOError):
                        with directory.exclusive_lock():
                            self.fail('A second lock was acquired')
                    subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(lock)],
                                   check=True, timeout=10)
                    raise RuntimeError('end the lock')
            with directory.exclusive_lock():
                pass
            self.assertEqual(directory.read_file('setup.lock', limit=100)[0], b'')

    def test_process_death_releases_lock_and_deletes_only_new_scratch(self):
        path = self.root / 'private'
        marker = self.root / 'lock-ready'
        fixture = str(Path(__file__).resolve())
        helper = r'''
from pathlib import Path
import runpy, sys, time
case = runpy.run_path(sys.argv[1])['WindowsStorageTests']
case.setUpClass()
with case.storage.private_directory(sys.argv[2], create=True) as directory:
    with directory.exclusive_lock(), directory.temporary_file('scratch.xml') as stream:
        stream.write(b'owned scratch')
        stream.flush()
        Path(sys.argv[3]).touch()
        time.sleep(60)
'''
        process = subprocess.Popen(
            [sys.executable, '-I', '-S', '-B', '-c', helper, fixture, str(path), str(marker)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 8
            while not marker.exists():
                if process.poll() is not None:
                    self.fail(process.stderr.read().decode('utf-8', 'replace'))
                if time.monotonic() >= deadline:
                    self.fail('Fixture never acquired its lock and scratch file.')
                time.sleep(.02)
            with self.storage.private_directory(path) as directory:
                with self.assertRaises(BlockingIOError):
                    with directory.exclusive_lock():
                        self.fail('A second lock was acquired before the owner exited.')
        finally:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            finally:
                process.stderr.close()
        with self.storage.private_directory(path) as directory:
            with directory.exclusive_lock(), directory.temporary_file('scratch.xml') as stream:
                stream.write(b'new scratch')
        self.assertFalse((path / 'scratch.xml').exists())
        self.assertEqual(self.score.read_bytes(), self.data)

    def test_hard_link_and_create_collision_preserve_original_bytes(self):
        path = self.root / 'private'
        with self.storage.private_directory(path, create=True) as directory:
            with directory.exclusive_lock():
                pass
        original = self.root / 'original.xml'
        lock = path / 'setup.lock'
        lock.rename(original)
        original.write_bytes(self.data)
        os.link(original, lock)
        with self.storage.private_directory(path) as directory:
            with self.assertRaisesRegex(PermissionError, 'exactly one link'):
                with directory.exclusive_lock():
                    self.fail('A hard link was accepted as a lock')
            with self.assertRaises(FileExistsError):
                with directory.temporary_file('setup.lock'):
                    self.fail('Scratch creation opened an existing file')
        self.assertEqual(original.read_bytes(), self.data)
        self.assertEqual(lock.read_bytes(), self.data)

    def test_stable_read_refuses_same_size_replacement_and_mutation(self):
        data, stamp = self.storage.read_stable(self.score, limit=len(self.data))
        self.assertEqual(data, self.data)
        self.assertEqual(len(stamp.file_id), 16)
        self.assertEqual(self.storage.read_stable(self.score, limit=len(data), expected=stamp)[1], stamp)
        with self.assertRaisesRegex(ValueError, 'read limit'):
            self.storage.read_stable(self.score, limit=len(data) - 1)
        changed = b'X' * len(data)
        self.score.write_bytes(changed)
        # Explicitly change the write timestamp as well as the same-length contents.
        os.utime(self.score, (time.time() - 10, time.time() - 10))
        with self.assertRaisesRegex(OSError, 'changed since'):
            self.storage.read_stable(self.score, limit=len(data), expected=stamp)
        old = self.score.with_name('old.xml')
        self.score.rename(old)
        self.score.write_bytes(changed)
        _, replacement = self.storage.read_stable(self.score, limit=len(data))
        self.assertNotEqual(replacement.file_id, stamp.file_id)
        with self.assertRaisesRegex(OSError, 'changed since'):
            self.storage.read_stable(self.score, limit=len(data), expected=stamp)

    def test_rechecks_metadata_after_open_and_before_returning_data(self):
        real_stamp = self.storage._StorageAPI.stamp
        initial = []

        def stamp_then_modify(api, handle):
            observed = real_stamp(api, handle)
            if not initial:
                initial.append(observed)
                timestamp = time.time() - 30
                os.utime(self.score, (timestamp, timestamp))
            return observed

        with patch.object(self.storage._StorageAPI, 'stamp', stamp_then_modify):
            with self.assertRaisesRegex(OSError, 'changed while'):
                self.storage.read_stable(self.score, limit=1024)
        self.assertEqual(self.score.read_bytes(), self.data)

    def test_held_path_blocks_concurrent_replacement_and_junction_redirection(self):
        from _winapi import CreateJunction

        displaced = self.folder.with_name('displaced')
        attacker = self.root / 'replacement.xml'
        attacker.write_bytes(b'evil')
        real_stamp = self.storage._StorageAPI.stamp
        raced = []

        def attempt_rename(source, target, outcomes):
            try:
                source.replace(target)
            except OSError as error:
                outcomes.append(error.winerror)

        def stamp_with_race(api, handle):
            if not raced:
                for source, target in ((attacker, self.score), (self.folder, displaced)):
                    thread = threading.Thread(target=attempt_rename,
                                              args=(source, target, raced), daemon=True)
                    thread.start()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(len(raced), 2)
            return real_stamp(api, handle)

        with patch.object(self.storage._StorageAPI, 'stamp', stamp_with_race):
            self.assertEqual(self.storage.read_stable(self.score, limit=1024)[0], self.data)
        self.assertTrue(all(error in (5, 32) for error in raced), raced)

        self.folder.rename(displaced)
        CreateJunction(str(displaced), str(self.folder))
        self.addCleanup(self.folder.rmdir)
        self.assertEqual(self.score.read_bytes(), self.data)
        with self.assertRaises(OSError):
            self.storage.read_stable(self.score, limit=1024)

    def test_private_scope_blocks_writable_handle_needed_to_set_reparse_data(self):
        path = self.root / 'private'
        with self.storage.private_directory(path, create=True) as directory:
            with self.assertRaises(OSError) as caught:
                handle = directory._api.create_file(str(path), 0x40000000, 7, None, 3,
                                                    0x02200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                directory._api.close(handle)
            self.assertEqual(caught.exception.winerror, 32)


_TREE_WORKER = r'''
import pathlib, subprocess, sys, time
child = subprocess.Popen(
    [sys.executable, '-I', '-S', '-B', '-c', 'import time; time.sleep(60)'],
    stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, close_fds=False,
)
path = pathlib.Path(sys.argv[1])
stage = path.with_suffix('.tmp')
stage.write_text(str(child.pid), encoding='ascii')
stage.replace(path)
'''


class WindowsWorkerTests(WindowsPilotCase):
    def _run(self, script, payload=b'request\n', **kwargs):
        return self.workers.run_worker([sys.executable, '-I', '-S', '-B', '-c', script],
                                       payload, timeout=kwargs.pop('timeout', 10), **kwargs)

    def test_multichunk_round_trip_drains_stderr_before_worker_reads(self):
        code = ("import sys; sys.stderr.buffer.write(b'e'*50000); sys.stderr.buffer.flush(); "
                "sys.stdout.buffer.write(sys.stdin.buffer.readline()); sys.stdout.buffer.flush()")
        payload = b'x' * 80000 + b'\n'
        self.assertEqual(self._run(code, payload, max_stdout=len(payload), max_stderr=50000),
                         payload)

    def test_both_output_limits_and_request_limit(self):
        for channel in ('stdout', 'stderr'):
            with self.subTest(channel=channel):
                code = ("import sys; sys.stdin.buffer.readline(); "
                        f"sys.{channel}.buffer.write(b'x'*50000); sys.{channel}.buffer.flush()")
                with self.assertRaisesRegex(self.host.PipeLimitError, channel):
                    self._run(code, **{f'max_{channel}': 16384})
        with self.assertRaisesRegex(self.host.PipeLimitError, 'request'):
            self._run('raise AssertionError("must not launch")', b'x' * 100 + b'\n',
                      max_request=100)

    def test_rejects_malformed_response_and_preserves_failure_diagnostic(self):
        for reply in (b'', b'partial', b'first\nsecond\n'):
            with self.subTest(reply=reply), self.assertRaisesRegex(ValueError, 'one LF'):
                self._run(f'import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write({reply!r})')
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            self._run('import sys; sys.stdin.buffer.readline(); sys.exit("fixture failure")')

    def test_timeout_unblocks_pending_input_and_both_reads(self):
        with self.assertRaisesRegex(TimeoutError, 'exceeded its deadline'):
            self._run('import time; time.sleep(60)', b'x' * 1000000 + b'\n', timeout=.5)
        self.assertEqual(self._run('import sys; sys.stdout.buffer.write(sys.stdin.buffer.readline())'),
                         b'request\n')

    def _open_live_child(self, marker):
        import _winapi

        until = time.monotonic() + 8
        while not marker.is_file():
            if time.monotonic() >= until:
                self.fail('Worker did not create its descendant marker')
            time.sleep(.02)
        pid = int(marker.read_text(encoding='ascii'))
        handle = _winapi.OpenProcess(self.workers._PROCESS_QUERY_AND_WAIT, False, pid)
        self.addCleanup(_winapi.CloseHandle, handle)
        self.assertEqual(_winapi.WaitForSingleObject(handle, 0), 258)  # WAIT_TIMEOUT
        return handle

    def test_cancel_terminates_descendant_after_leader_exits(self):
        import _winapi

        marker = self.root / 'child.pid'
        cancelled = threading.Event()
        results = []
        observed = []
        ready = threading.Event()
        current = _winapi.GetCurrentProcess()
        real_launch = self.workers._WorkerAPI.launch

        def observe_launch(api, winapi, command, job, handles, stack):
            process = real_launch(api, winapi, command, job, handles, stack)
            leader = _winapi.DuplicateHandle(current, process, current, 0, False, 2)
            self.addCleanup(_winapi.CloseHandle, leader)
            # Keep a query-only handle open across cancellation. Kill-on-close
            # alone cannot terminate the child while this handle remains open.
            query_job = _winapi.DuplicateHandle(current, job, current, 0x0004, False, 0)
            self.addCleanup(_winapi.CloseHandle, query_job)
            observed.append((api, leader, query_job))
            ready.set()
            return process

        def run():
            try:
                self._run(_TREE_WORKER.replace('sys.argv[1]', repr(str(marker))),
                          timeout=12, cancelled=cancelled)
            except Exception as error:
                results.append(error)

        thread = threading.Thread(target=run, daemon=True)
        with patch.object(self.workers._WorkerAPI, 'launch', observe_launch):
            thread.start()
            try:
                self.assertTrue(ready.wait(5), 'Worker did not expose its process and job handles.')
                api, leader, query_job = observed[0]
                child = self._open_live_child(marker)
                self.assertEqual(_winapi.WaitForSingleObject(leader, 5000), 0)
                self.assertTrue(api.is_job_member(child, query_job))
                self.assertEqual(_winapi.WaitForSingleObject(child, 0), 258)
            finally:
                cancelled.set()
                thread.join(8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], self.workers.WorkerCancelled)
        self.assertEqual(_winapi.WaitForSingleObject(child, 0), 0)

    def test_parent_death_closes_job_and_kills_descendant(self):
        import _winapi

        marker = self.root / 'child.pid'
        helper = (
            "import runpy; "
            f"case=runpy.run_path({str(Path(__file__).resolve())!r})['WindowsWorkerTests']; "
            "case.setUpClass(); "
            f"case()._run({_TREE_WORKER.replace('sys.argv[1]', repr(str(marker)))!r}, timeout=60)"
        )
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', helper],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            child = self._open_live_child(marker)
        finally:
            process.kill()
            process.wait(timeout=5)
        self.assertEqual(_winapi.WaitForSingleObject(child, 5000), 0)

    def test_launch_failure_leaves_transport_available(self):
        with self.assertRaises(FileNotFoundError):
            self.workers.run_worker([str(self.root / 'missing.exe')], b'request\n', timeout=5)
        self.assertEqual(self._run('import sys; sys.stdout.buffer.write(sys.stdin.buffer.readline())'),
                         b'request\n')

    def test_only_stdio_handles_inherited(self):
        import _winapi

        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        event = self.reader._bind(kernel, 'CreateEventW', win.HANDLE, ctypes.c_void_p,
                                  win.BOOL, win.BOOL, win.LPCWSTR)(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        self.addCleanup(_winapi.CloseHandle, event)
        os.set_handle_inheritable(event, True)
        code = (
            'import ctypes, sys; from ctypes import wintypes; '
            'sys.stdin.buffer.readline(); '
            'set_event=ctypes.WinDLL("kernel32").SetEvent; '
            'set_event.argtypes=(wintypes.HANDLE,); '
            f'set_event({event}); sys.stdout.buffer.write(b"ok\\n")'
        )
        self.assertEqual(self._run(code), b'ok\n')
        self.assertEqual(_winapi.WaitForSingleObject(event, 0), 258)


if __name__ == '__main__':
    unittest.main()
