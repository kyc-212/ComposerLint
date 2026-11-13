"""Focused CI orchestration controls without acquiring or installing assets."""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path, PureWindowsPath
from runpy import run_path
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from textwrap import dedent
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
host = run_path(str(ROOT / 'tools' / 'ci_windows_host_files.py'))
snapshot_entry = host['snapshot_entry']
install_once = run_path(str(ROOT / 'tools' / 'ci_windows_text_setup.py'))['install_once']
ALIAS_ROOT = PureWindowsPath(r'C:\Users\runneradmin\AppData\Local\Microsoft\WindowsApps')
ALIAS = ALIAS_ROOT / 'python.exe'
ALIAS_METADATA = {
    'attributes': stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_ARCHIVE,
    'reparse_tag': 0x8000001B, 'size': 0, 'created_filetime': 10, 'written_filetime': 20,
}


class WindowsHostSnapshotTests(unittest.TestCase):
    def test_windowsapps_execution_alias_is_recorded_without_opening_it(self):
        read = Mock(return_value=ALIAS_METADATA)
        digest = Mock(side_effect=OSError('The file cannot be accessed by the system'))
        self.assertEqual(snapshot_entry(ALIAS, ALIAS_ROOT, executable=True, metadata=read, digest=digest),
                         {'path': str(ALIAS), 'type': 'app_execution_alias', **ALIAS_METADATA})
        read.assert_called_once_with(ALIAS)
        digest.assert_not_called()
        read.return_value = {**ALIAS_METADATA, 'written_filetime': 21}
        self.assertNotEqual(
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=True, metadata=read, digest=digest),
            {'path': str(ALIAS), 'type': 'app_execution_alias', **ALIAS_METADATA},
        )

    def test_other_reparse_entries_are_errors(self):
        digest = Mock()
        for path, tag, executable in (
            (ALIAS, 0xA000000C, True),
            (PureWindowsPath(r'C:\python.exe'), 0x8000001B, True),
            (ALIAS, 0x8000001B, False),
        ):
            with self.subTest(path=path, tag=tag, executable=executable), self.assertRaisesRegex(
                OSError, 'Unexpected reparse point'
            ):
                snapshot_entry(path, ALIAS_ROOT, executable=executable, digest=digest,
                               metadata=Mock(return_value={**ALIAS_METADATA, 'reparse_tag': tag}))
        digest.assert_not_called()

    def test_regular_executables_are_hashed_and_read_errors_fail(self):
        path = PureWindowsPath(r'C:\hostedtoolcache\Python\python.exe')
        read = Mock(return_value={'attributes': stat.FILE_ATTRIBUTE_ARCHIVE})
        digest = Mock(return_value='a' * 64)
        self.assertEqual(snapshot_entry(path, ALIAS_ROOT, executable=True, metadata=read, digest=digest),
                         {'path': str(path), 'type': 'regular_file', 'sha256': 'a' * 64})
        digest.assert_called_once_with(path)
        digest.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            snapshot_entry(path, ALIAS_ROOT, executable=True, metadata=read, digest=digest)

    def test_missing_config_and_missing_executable_are_distinct(self):
        path = PureWindowsPath(r'C:\config.toml')
        read = Mock(return_value=None)
        self.assertEqual(snapshot_entry(path, ALIAS_ROOT, executable=False, metadata=read),
                         {'path': str(path), 'type': 'missing'})
        with self.assertRaisesRegex(FileNotFoundError, 'Discovered executable disappeared'):
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=True, metadata=read)

    def test_inventory_covers_cache_python_and_alias_independently_of_path(self):
        cached = PureWindowsPath(r'D:\tool cache\Python\3.12.10\x64\python.exe')
        read = Mock(side_effect={
            cached: {'attributes': stat.FILE_ATTRIBUTE_ARCHIVE},
            ALIAS: ALIAS_METADATA,
        }.get)
        digest = Mock(return_value='a' * 64)
        inventory = host['executable_inventory'](r'"C:\Program Files\Git\cmd"', cached, ALIAS_ROOT,
                                                  metadata=read, digest=digest)
        self.assertEqual({item['path']: item['type'] for item in inventory},
                         {str(cached): 'regular_file', str(ALIAS): 'app_execution_alias'})
        digest.assert_called_once_with(cached)

    def test_offline_file_is_refused_and_metadata_errors_are_not_treated_as_absence(self):
        digest = Mock()
        with self.assertRaisesRegex(OSError, 'Offline or recalled file refused'):
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=False, digest=digest,
                           metadata=Mock(return_value={'attributes': 0x1000}))
        with self.assertRaises(PermissionError):
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=False, digest=digest,
                           metadata=Mock(side_effect=PermissionError('denied')))
        digest.assert_not_called()

    def test_regular_file_change_during_hashing_fails(self):
        attributes = {'attributes': stat.FILE_ATTRIBUTE_ARCHIVE}
        with self.assertRaisesRegex(OSError, 'metadata changed'):
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=False, digest=Mock(return_value='a' * 64),
                           metadata=Mock(side_effect=[attributes, {**attributes, 'size': 2}]))


class RegistryKey(str):
    def __enter__(self):
        return self

    def __exit__(self, *exception):
        pass


class WindowsRegistrySnapshotTests(unittest.TestCase):
    def test_values_remain_bound_to_their_key_and_type(self):
        values = {'scope/first': (1, 'one'), 'scope/second': (1, 'two')}
        registry = Mock(HKEY_CURRENT_USER='user')
        registry.OpenKey.side_effect = lambda parent, name: RegistryKey(
            name if parent == 'user' else f'{parent}/{name}')
        registry.QueryInfoKey.side_effect = lambda key: (2, 0, 1) if key == 'scope' else (0, 1, 1)
        registry.EnumKey.side_effect = lambda key, index: ('first', 'second')[index]
        registry.EnumValue.side_effect = lambda key, index: ('Path', values[key][1], values[key][0])
        scopes = (('HKEY_CURRENT_USER', 'scope'),)
        before = host['registry_snapshot'](scopes, registry=registry)
        values['scope/first'], values['scope/second'] = values['scope/second'], values['scope/first']
        self.assertNotEqual(before, host['registry_snapshot'](scopes, registry=registry))
        values.update({'scope/first': (2, 'one'), 'scope/second': (1, 'two')})
        self.assertNotEqual(before, host['registry_snapshot'](scopes, registry=registry))

    def test_registry_access_errors_do_not_count_as_missing_keys(self):
        registry = Mock(HKEY_CURRENT_USER='user')
        scopes = (('HKEY_CURRENT_USER', 'scope'),)
        registry.OpenKey.side_effect = FileNotFoundError
        self.assertEqual(host['registry_snapshot'](scopes, registry=registry), {scopes[0]: None})
        registry.OpenKey.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            host['registry_snapshot'](scopes, registry=registry)


class WindowsCIOrchestrationTests(unittest.TestCase):
    @staticmethod
    def snapshot():
        return {'Process PATH': 'original', 'Python/uv files and aliases': []}

    def test_exact_consent_reaches_stub_cli_and_verified_receipt(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch).resolve() / 'reviewed source'
            (root / 'tools').mkdir(parents=True)
            for pin in ('uv.lock', 'tools/windows-setup-assets.json'):
                shutil.copyfile(ROOT / pin, root / pin)
            (root / 'tools/setup_composerlint.py').write_text(dedent('''
                import json, sys
                from pathlib import Path
                assert sys.argv[1:] == ['--windows-text-pilot']
                assert sys.stdin.read() == 'INSTALL\\n'
                root = Path(__file__).resolve().parents[1]
                assert Path.cwd() == root
                destination = root.parent / 'Local AppData' / 'ComposerLint' / 'setup'
                generation = destination / ('install-' + 'a' * 20)
                runtime = generation / 'runtime/Scripts/python.exe'
                for tool in (runtime, generation / 'python/python.exe', generation / 'uv/uv.exe'):
                    tool.parent.mkdir(parents=True)
                    tool.write_bytes(b'fixture only')
                (destination / 'setup.lock').touch()
                manifest = json.loads((root / 'tools/windows-setup-assets.json').read_bytes())
                receipt = {'schema': 1, 'target': 'windows-x64-text-pilot', 'plan': 'a' * 64,
                           'lock_sha256': manifest['lock_sha256'], 'python': str(runtime)}
                (generation / 'complete.json').write_text(json.dumps(receipt), encoding='utf-8')
                print('Reviewed setup plan:', receipt['plan'])
                print('Private Windows text environment verified:', runtime)
            '''), encoding='utf-8')
            profile = root.parent / 'Local AppData'
            profile.mkdir()
            snapshot = Mock(side_effect=[self.snapshot(), self.snapshot()])
            output = StringIO()
            with redirect_stdout(output):
                install_once(root, Path(sys.executable), profile, snapshot=snapshot)
            self.assertEqual(snapshot.call_count, 2)
            self.assertIn('Before and after host snapshots match.', output.getvalue())
            self.assertIn('Verified completion receipt', output.getvalue())

    def test_full_install_passes_the_bundle_and_requires_real_acceptance_receipt(self):
        with TemporaryDirectory(prefix='ComposerLint 全体 ') as temporary:
            root = Path(temporary)
            local_app_data = root / 'Local AppData'
            bundle = root / 'Renderer bundle'
            digest = 'b' * 64
            runtime = local_app_data / 'ComposerLint/setup' / ('install-' + 'a' * 20) / 'runtime/Scripts/python.exe'
            generation = runtime.parents[2]
            manifest = json.loads((ROOT / 'tools/windows-setup-assets.json').read_bytes())
            receipt = {'schema': 1, 'target': 'windows-x64-full-pilot', 'plan': 'a' * 64,
                       'lock_sha256': manifest['lock_sha256'], 'python': str(runtime),
                       'renderer_config': str(generation / 'renderer.json'), 'renderer_sha256': digest,
                       'renderer_controls': dict(tests=10, failures=0, errors=0, skipped=0), 'mcp_tools': 18}

            def run(command, **kwargs):
                self.assertEqual(command[-5:], ['--windows-full-pilot', '--renderer-bundle', str(bundle),
                                                '--renderer-sha256', digest])
                self.assertEqual(kwargs['input'], 'INSTALL\n')
                for path in (runtime, generation / 'python/python.exe', generation / 'uv/uv.exe',
                             generation / 'renderer/Scripts/python.exe', generation / 'renderer.json',
                             generation.parent / 'setup.lock'):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
                (generation / 'renderer-artifacts').mkdir()
                (generation / 'complete.json').write_text(json.dumps(receipt), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '\n'.join((
                    f"Reviewed setup plan: {receipt['plan']}",
                    f'Private Windows full environment verified: {runtime}',
                    'MCP stdio discovered 18 tools.',
                    'Verified unchanged authored sources and MCP shutdown.',
                )))

            for bad in (True, False):
                with self.subTest(incomplete=bad), redirect_stdout(StringIO()):
                    receipt['renderer_controls']['skipped'] = int(bad)
                    arguments = dict(renderer_bundle=bundle, renderer_sha256=digest, run=run,
                                     snapshot=lambda *_: self.snapshot())
                    if bad:
                        with self.assertRaisesRegex(ValueError, 'ten passing renderer controls'):
                            install_once(ROOT, Path(sys.executable), local_app_data, **arguments)
                        shutil.rmtree(local_app_data)
                    else:
                        install_once(ROOT, Path(sys.executable), local_app_data, **arguments)

    def test_full_entry_point_refuses_unapproved_dispatch_before_setup(self):
        env = dict(os.environ, GITHUB_RUN_NUMBER='22')
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-X', 'utf8', str(ROOT / 'tools/ci_windows_full_setup.py')],
            env=env, text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires the guarded Windows installation dispatch', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_baseline_failure_never_launches_installer(self):
        with TemporaryDirectory() as profile:
            run = Mock()
            snapshot = Mock(side_effect=PermissionError('snapshot access denied'))
            with self.assertRaisesRegex(PermissionError, 'snapshot access denied'):
                install_once(ROOT, Path(sys.executable), Path(profile), run=run, snapshot=snapshot)
            run.assert_not_called()

    def test_failed_installation_is_not_retried_and_still_compares_host(self):
        with TemporaryDirectory() as profile:
            snapshot = Mock(side_effect=[self.snapshot(), self.snapshot()])
            run = Mock(return_value=subprocess.CompletedProcess([], 1, 'Pinned acquisition failed.\n'))
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(subprocess.CalledProcessError):
                install_once(ROOT, Path(sys.executable), Path(profile), run=run, snapshot=snapshot)
            run.assert_called_once()
            self.assertEqual(snapshot.call_count, 2)
            self.assertIn('Pinned acquisition failed.', output.getvalue())
            self.assertIn('Before and after host snapshots match.', output.getvalue())
            self.assertNotIn('Verified 61 pinned files', output.getvalue())

    def test_exit_zero_without_a_receipt_is_still_failure(self):
        with TemporaryDirectory() as profile:
            snapshot = Mock(side_effect=[self.snapshot(), self.snapshot()])
            run = Mock(return_value=subprocess.CompletedProcess([], 0, 'Cancelled.\n'))
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(FileNotFoundError):
                install_once(ROOT, Path(sys.executable), Path(profile), run=run, snapshot=snapshot)
            run.assert_called_once()
            self.assertEqual(snapshot.call_count, 2)
            self.assertNotIn('Verified 61 pinned files', output.getvalue())

    def test_host_change_is_a_failure_even_when_installer_also_fails(self):
        with TemporaryDirectory() as profile:
            snapshot = Mock(side_effect=[self.snapshot(), {**self.snapshot(), 'Process PATH': 'changed'}])
            run = Mock(return_value=subprocess.CompletedProcess([], 1, 'Installer stopped.\n'))
            with redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, 'Process PATH') as error:
                install_once(ROOT, Path(sys.executable), Path(profile), run=run, snapshot=snapshot)
            self.assertIsInstance(error.exception.__context__, subprocess.CalledProcessError)
