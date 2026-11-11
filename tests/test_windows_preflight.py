"""Windows preflight checks, including stdlib-only native controls."""

from contextlib import ExitStack, redirect_stdout
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import io
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).absolute().parents[1]
CLI = ROOT / 'tools' / 'preflight_windows.py'
APP_DATA = PureWindowsPath(r'C:\Users\Fixture\AppData\Local')
APP = APP_DATA / 'ComposerLint'
SETUP = APP / 'setup'


def setUpModule():
    global preflight
    name = '_composerlint_preflight_tests'
    spec = ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = [str(ROOT / 'src' / 'composerlint')]
    sys.modules[name] = module_from_spec(spec)
    unittest.addModuleCleanup(_remove_modules, name)
    preflight = import_module(f'{name}.windows_preflight')


def _remove_modules(name):
    for module in tuple(sys.modules):
        if module == name or module.startswith(f'{name}.'):
            del sys.modules[module]


class _TestDisk:
    def __init__(self, *, filesystem='NTFS', flags=0x88):
        self.directories = {APP_DATA, *APP_DATA.parents}
        self.files = set()
        self.filesystem = filesystem
        self.flags = flags
        self.opened_drives = []
        self.access_probes = []
        self.denied = False

    def open_root(self, drive, stack):
        self.opened_drives.append(drive)
        if drive.upper() != 'C:':
            raise OSError('Unidentified local disk.')
        return PureWindowsPath(drive + '\\')

    def volume_capabilities(self, handle):
        return self.filesystem, self.flags

    def identity(self, handle):
        return handle

    def verify_chain(self, chain):
        assert all(handle == identity for handle, identity in chain)

    def open_existing(self, parent, name, stack, *, directory, access=0, share_write=False):
        path = parent / name
        if path not in (self.directories if directory else self.files):
            raise FileNotFoundError(path)
        if access & 0x0006:
            self.access_probes.append((path, access))
            if self.denied:
                raise PermissionError('Access refused for the current user.')
        return path


class _TestSecurity:
    def __init__(self, private=()):
        self.private = set(private)
        self.checked = []

    def user_sid(self):
        return 'fixture-user'

    def require_private(self, handle, sid):
        assert sid == 'fixture-user'
        self.checked.append(handle)
        if handle not in self.private:
            raise PermissionError('Unexpected owner or DACL.')


class WindowsPreflightTests(unittest.TestCase):
    def _report(self, disk, *, native=0x8664, process=0, app_data=str(APP_DATA), path='', security=None):
        with (patch.object(sys, 'platform', 'win32'),
              patch.object(preflight, '_architecture', return_value=(native, process)),
              patch.object(preflight, '_local_app_data', return_value=app_data),
              patch.object(preflight, '_ReadOnlyDisk', return_value=disk),
              patch.object(preflight, '_SecurityAPI', return_value=security or _TestSecurity()),
              patch.dict(os.environ, {'PATH': path})):
            return preflight.inspect()

    def test_native_x64_plan_uses_private_pins_with_missing_optional_tools(self):
        disk = _TestDisk()
        report = self._report(disk)
        self.assertTrue(report.compatible)
        self.assertEqual(report.destination, SETUP)
        for key in ('Windows x64', 'Destination path', 'Local NTFS volume', 'Destination access'):
            self.assertEqual(report.checks[key].status, 'Passed')
        for key in ('Host Python (optional)', 'Host uv (optional)'):
            self.assertEqual(report.checks[key].status, 'Failed')
        for key in ('Existing private directories', 'Installation operations'):
            self.assertEqual(report.checks[key].status, 'Not checked')
        self.assertEqual(disk.access_probes, [(APP_DATA, preflight._ADD_SUBDIRECTORY)])
        output = io.StringIO()
        with redirect_stdout(output):
            preflight.print_report(report)
        for expected in ('CPython 3.12.13', 'uv 0.12.3', 'uv.lock', 'Expected downloads',
                         'Windows Approve & Export', str(SETUP)):
            self.assertIn(expected, output.getvalue())

    def test_incompatible_or_emulated_architectures_skip_all_disk_probes(self):
        for native, process in ((0xAA64, 0), (0xAA64, 0x8664), (0x8664, 0x014C), (0x014C, 0)):
            with self.subTest(native=native, process=process):
                disk = _TestDisk()
                report = self._report(disk, native=native, process=process, path=r'C:\Tools')
                self.assertFalse(report.compatible)
                self.assertEqual(report.checks['Windows x64'].status, 'Failed')
                self.assertEqual(report.checks['Local NTFS volume'].status, 'Not checked')
                self.assertEqual(disk.opened_drives, [])

    def test_non_ntfs_or_missing_volume_capabilities_refuses_installation_plan(self):
        for filesystem, flags in (('exFAT', 0), ('ReFS', 0x88), ('NTFS', 0x80), ('NTFS', 8)):
            with self.subTest(filesystem=filesystem, flags=flags):
                disk = _TestDisk(filesystem=filesystem, flags=flags)
                report = self._report(disk)
                self.assertFalse(report.compatible)
                self.assertEqual(report.checks['Local NTFS volume'].status, 'Failed')
                self.assertIn(filesystem, report.checks['Local NTFS volume'].detail)
                self.assertEqual(report.checks['Destination access'].status, 'Not checked')
                self.assertEqual(disk.access_probes, [])

    def test_unsafe_destination_path_is_rejected_before_any_disk_open(self):
        for path in (r'\\server\share\profile', r'\\?\C:\Local', r'C:Local',
                     r'C:\Users\..\Local', r'C:\Users\NUL.txt', r'C:\Local:stream',
                     'https://example.invalid/Local', r'C:\Local ', ''):
            with self.subTest(path=path):
                disk = _TestDisk()
                report = self._report(disk, app_data=path)
                self.assertFalse(report.compatible)
                self.assertEqual(report.checks['Destination path'].status, 'Failed')
                self.assertIsNone(report.destination)
                self.assertEqual(disk.opened_drives, [])

    def test_current_user_access_denial_and_existing_nonprivate_directory(self):
        disk = _TestDisk()
        disk.denied = True
        denied = self._report(disk)
        self.assertFalse(denied.compatible)
        self.assertEqual(denied.checks['Destination access'].status, 'Failed')
        disk.denied = False
        disk.directories.update((APP, SETUP))
        open_acl = self._report(disk, security=_TestSecurity({APP}))
        self.assertFalse(open_acl.compatible)
        self.assertEqual(open_acl.checks['Existing private directories'].status, 'Failed')
        private = _TestSecurity({APP, SETUP})
        compatible = self._report(disk, security=private)
        self.assertTrue(compatible.compatible)
        self.assertEqual(private.checked, [APP, SETUP])
        self.assertEqual(disk.access_probes[-1], (SETUP, preflight._ADD_FILE | preflight._ADD_SUBDIRECTORY))

    def test_optional_host_tools_are_discovered_only_in_safe_local_path_entries(self):
        disk = _TestDisk()
        tools = PureWindowsPath(r'C:\Local Tools')
        disk.directories.add(tools)
        disk.files.update((tools / 'python.exe', tools / 'uv.exe'))
        report = self._report(
            disk, path=r'\\server\share;\\?\C:\Local Tools;relative;"C:\Local Tools\";Z:\remote')
        self.assertTrue(report.compatible)
        for key, filename in (('Host Python (optional)', 'python.exe'), ('Host uv (optional)', 'uv.exe')):
            self.assertEqual(report.checks[key].status, 'Passed')
            self.assertIn(str(tools / filename), report.checks[key].detail)
            self.assertIn('execution were not checked', report.checks[key].detail)
        self.assertEqual(disk.opened_drives, ['C:', 'C:'])

    def test_native_open_contract_excludes_create_delete_and_nonlocal_namespaces(self):
        disk = preflight._ReadOnlyDisk.__new__(preflight._ReadOnlyDisk)
        disk.close = lambda handle: None
        disk.check_type = lambda *args, **kwargs: None
        opened = []
        disk.create_file = lambda *args: opened.append(args) or 42
        disk.open_component = lambda *args, **kwargs: opened.append(kwargs) or 43
        def mapping(value):
            def query(drive, buffer, size):
                buffer.value = value
                return len(value) + 1
            return query
        with ExitStack() as stack:
            for device in (r'\Device\Mup\server\share', r'\??\C:\Substituted'):
                disk.dos_device = mapping(device)
                with self.assertRaisesRegex(OSError, 'remote, substituted'):
                    disk.open_root('Z:', stack)
            self.assertEqual(opened, [])
            disk.dos_device = mapping(r'\Device\HarddiskVolume12')
            root = disk.open_root('C:', stack)
            disk.open_existing(root, 'Local', stack, directory=True, access=4, share_write=True)
            disk.open_existing(root, 'python.exe', stack, directory=False)
        self.assertEqual(opened[0][0], '\\\\?\\GLOBALROOT\\Device\\HarddiskVolume12\\')
        self.assertEqual(opened[0][4], 3)  # OPEN_EXISTING
        for options in opened[1:]:
            self.assertEqual(options['disposition'], 1)  # FILE_OPEN
            self.assertTrue(options['refuse_reparse'])
            self.assertEqual(options['options'] & 0x1000, 0)  # no FILE_DELETE_ON_CLOSE
            self.assertNotIn('descriptor', options)

    def test_cli_audit_guard_rejects_writes_network_and_child_processes(self):
        guard = r'''
import os, sys

def audit(event, args):
    denied = {
        'os.mkdir', 'os.rmdir', 'os.remove', 'os.rename', 'os.chmod', 'os.chown',
        'os.utime', 'os.link', 'os.symlink', 'os.truncate', 'os.putenv',
        'os.unsetenv', 'os.system', 'os.exec', 'os.posix_spawn', 'subprocess.Popen',
        'urllib.Request', 'http.client.connect', 'winreg.SetValue', 'winreg.CreateKey',
    }
    if event == 'open':
        _, mode, flags = args
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        if (flags & write_flags) or (mode and any(char in mode for char in 'wax+')):
            raise AssertionError('Forbidden preflight write: ' + str(args))
    if event in denied or event.startswith('socket.'):
        raise AssertionError('Forbidden preflight operation: ' + event)

cli = sys.argv[1]
sys.argv[:] = [cli]
sys.addaudithook(audit)
with open(cli, 'rb') as source:
    code = compile(source.read(), cli, 'exec')
exec(code, {'__name__': '__main__', '__file__': cli})
'''
        with tempfile.TemporaryDirectory(prefix='composerlint-preflight-audit-') as temporary:
            root = Path(temporary)
            files = (CLI, *(ROOT / 'src' / 'composerlint' / name for name in
                            ('windows_preflight.py', 'windows_private.py', 'windows_read.py', 'platform_support.py')))
            for source in files:
                copy = root / source.relative_to(ROOT)
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, copy)
            before = sorted(root.rglob('*'))
            # A fresh source tree and no -B make accidental bytecode writes observable.
            result = subprocess.run([sys.executable, '-I', '-S', '-X', 'utf8', '-c', guard,
                                     str(root / CLI.relative_to(ROOT))],
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(sorted(root.rglob('*')), before)
        self.assertEqual(result.stderr, '')
        self.assertIn('ComposerLint Windows terminal preflight', result.stdout)
        self.assertIn('This command performs local inspection only.', result.stdout)
        self.assertIn(result.returncode, (0, 1))
        if sys.platform != 'win32':
            self.assertEqual(result.returncode, 1)
            self.assertIn('[Failed] Windows x64', result.stdout)

    def test_compatible_windows_inspection_has_no_write_download_or_launch_calls(self):
        import socket
        import urllib.request

        def forbidden(*args, **kwargs):
            raise AssertionError('Preflight attempted a write, network request, or process launch.')

        # Drive and token operations are simulated on macOS. The successful
        # decision path still runs with common side-effect entry points blocked.
        with (patch('builtins.open', side_effect=forbidden),
              patch.object(io, 'open', side_effect=forbidden),
              patch.object(os, 'open', side_effect=forbidden),
              patch.object(os, 'mkdir', side_effect=forbidden),
              patch.object(os, 'unlink', side_effect=forbidden),
              patch.object(socket, 'socket', side_effect=forbidden),
              patch.object(urllib.request, 'urlopen', side_effect=forbidden),
              patch.object(subprocess, 'Popen', side_effect=forbidden)):
            report = self._report(_TestDisk())
        self.assertTrue(report.compatible)


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows')
class WindowsNativePreflightTests(unittest.TestCase):
    def test_actual_native_profile_and_destination_without_mutating_fixture(self):
        native, process = preflight._architecture()
        self.assertEqual((native, process), (0x8664, 0))
        self.assertTrue(preflight._checked_path(preflight._local_app_data()).is_absolute())
        with tempfile.TemporaryDirectory(prefix='ComposerLint preflight ') as temporary:
            base = Path(temporary)
            sentinel = base / 'existing.txt'
            sentinel.write_bytes(b'preserve this fixture')
            before = (tuple(base.iterdir()), sentinel.stat().st_mtime_ns, sentinel.read_bytes())
            with patch.object(preflight, '_local_app_data', return_value=str(base)), patch.dict(os.environ, {'PATH': ''}):
                report = preflight.inspect()
            self.assertTrue(report.compatible, report.checks)
            self.assertEqual(report.checks['Existing private directories'].status, 'Not checked')
            after = (tuple(base.iterdir()), sentinel.stat().st_mtime_ns, sentinel.read_bytes())
            self.assertEqual(before, after)

    def test_actual_existing_private_directory_and_junction_refusal(self):
        import _winapi
        storage = import_module(f'{preflight.__package__}.windows_private')
        with tempfile.TemporaryDirectory(prefix='ComposerLint preflight ') as temporary:
            base = Path(temporary)
            app = base / 'ComposerLint'
            with storage.private_directory(app, create=True):
                pass
            with storage.private_directory(app / 'setup', create=True):
                pass
            with patch.object(preflight, '_local_app_data', return_value=str(base)), patch.dict(os.environ, {'PATH': ''}):
                report = preflight.inspect()
                self.assertTrue(report.compatible, report.checks)
                self.assertEqual(report.checks['Existing private directories'].status, 'Passed')
                (app / 'setup').rename(app / 'target')
                _winapi.CreateJunction(str(app / 'target'), str(app / 'setup'))
                try:
                    redirected = preflight.inspect()
                    self.assertFalse(redirected.compatible)
                    self.assertEqual(redirected.checks['Destination access'].status, 'Failed')
                    self.assertTrue((app / 'target').is_dir())
                finally:
                    (app / 'setup').rmdir()


if __name__ == '__main__':
    unittest.main()
