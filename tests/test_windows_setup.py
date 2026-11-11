"""Windows terminal setup tests. Default native tests use fixtures, not downloads."""

from contextlib import contextmanager, redirect_stdout
import hashlib
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile


ROOT = Path(__file__).absolute().parents[1]


def setUpModule():
    global setup, storage
    name = '_composerlint_windows_setup_tests'
    spec = ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = [str(ROOT / 'src' / 'composerlint')]
    sys.modules[name] = module_from_spec(spec)
    unittest.addModuleCleanup(_remove_modules, name)
    setup = import_module(f'{name}.windows_setup')
    storage = import_module(f'{name}.windows_install_storage')


def _remove_modules(name):
    for module in tuple(sys.modules):
        if module == name or module.startswith(f'{name}.'):
            del sys.modules[module]


def tar(*members):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as archive:
        for name, body in members:
            item = tarfile.TarInfo(name)
            if body is None:
                item.type = tarfile.SYMTYPE
                item.linkname = '../outside'
                archive.addfile(item)
            else:
                item.size = len(body)
                archive.addfile(item, io.BytesIO(body))
    return result.getvalue()


def zip_archive(name='uv-x86_64-pc-windows-msvc/uv.exe', *, link=False):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        item = zipfile.ZipInfo(name)
        if link:
            item.create_system = 3
            item.external_attr = stat.S_IFLNK << 16
        archive.writestr(item, b'uv fixture')
    return result.getvalue()


def make_plan(destination):
    data = [tar(('python/python.exe', b'python fixture')), zip_archive(), b'wheel fixture']
    names = ('python.tar.gz', 'uv.zip', 'wheel-1-py3-none-any.whl')
    assets = [setup.Asset(name, 'https://files.pythonhosted.org/' + name,
                          hashlib.sha256(body).hexdigest(), len(body),
                          'wheel' if i == 2 else '', '1' if i == 2 else '')
              for i, (name, body) in enumerate(zip(names, data, strict=True))]
    return (setup.Plan(destination, *assets[:2], (assets[2],),
                       ((('pyproject.toml',), b'project fixture'),), 'lock-hash', 'plan-id'),
            dict(zip(names, data, strict=True)))


class FakeStage:
    def __init__(self, path):
        self.path = path

    def mkdir(self, parts):
        self.path.joinpath(*parts).mkdir(parents=True, exist_ok=True)

    def create(self, parts):
        self.mkdir(parts[:-1])
        return self.path.joinpath(*parts).open('xb')

    @contextmanager
    def held_file(self, parts):
        path = self.path.joinpath(*parts)
        if not path.is_file():
            raise FileNotFoundError(path)
        yield path

    def clear_work(self):
        shutil.rmtree(self.path / 'work')

    def commit(self, receipt):
        with self.create(('complete.json',)) as stream:
            stream.write(json.dumps(receipt).encode())


def fixture_build(plan, stage, files, *, fail=None):
    actions = []

    def command(args, *, cwd, env, timeout):
        actions.append(args)
        if args[0].endswith('uv.exe'):
            assert {'--offline', '--no-config', '--no-python-downloads'} <= set(args)
        assert env['UV_PYTHON_DOWNLOADS'] == 'never'
        assert env['TEMP'] == str(stage.path / 'work' / 'tmp')
        assert Path(cwd).is_relative_to(stage.path)
        if '--version' in args:
            return b'uv 0.12.3'
        if 'venv' in args:
            with stage.create(('runtime', 'Scripts', 'python.exe')) as stream:
                stream.write(b'runtime fixture')
        if 'export' in args:
            return b'wheel==1\n'
        if '-c' in args and 'struct.calcsize' in args[args.index('-c') + 1]:
            return b'cpython 3.12.13 8\n'
        if '-c' in args and 'hatchling.build' in args[args.index('-c') + 1]:
            name = 'composerlint-0.2.0-py3-none-any.whl'
            with stage.create(('work', 'dist', name)) as stream:
                stream.write(b'local wheel fixture')
            return name.encode()
        if 'check' in args and fail == 'verify':
            raise RuntimeError('fixture dependency verification failure')
        return b''

    def fetch(asset):
        if fail == 'missing' and asset == plan.wheels[0]:
            raise setup.SetupError('Could not acquire fixture wheel')
        return setup.acquire(asset, opener=Mock(open=Mock(return_value=io.BytesIO(files[asset.filename]))))

    runtime = setup.populate(plan, stage, Mock(), fetch=fetch, command=command,
                             windows_root=lambda: stage.path / 'System root fixture')
    return runtime, actions


class WindowsSetupTests(unittest.TestCase):
    def test_exact_asset_pins_match_lock_and_preflight(self):
        manifest = (ROOT / 'tools/windows-setup-assets.json').read_bytes()
        lock = (ROOT / 'uv.lock').read_bytes()
        python, uv, wheels, lock_sha = setup._asset_plan(manifest, lock)
        self.assertEqual(len(wheels), 59)
        self.assertIn('pywin32', {w.name for w in wheels})
        self.assertEqual(lock_sha, hashlib.sha256(lock).hexdigest())
        self.assertEqual((python.sha256, uv.sha256), tuple(row[2] for row in setup.preflight._BOOTSTRAP_PINS))
        with self.assertRaisesRegex(setup.SetupError, 'uv.lock changed'):
            setup._asset_plan(manifest, lock + b'\n')
        changed = json.loads(manifest)
        changed['wheels'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(setup.SetupError, 'not in the reviewed lock'):
            setup._asset_plan(json.dumps(changed), lock)

    def test_missing_corrupt_truncated_and_oversized_downloads_refuse(self):
        plan, files = make_plan(Path('fixture'))
        asset = plan.python
        good = files[asset.filename]
        opener = Mock()
        for data, message in ((good[:-1], 'integrity'), (b'0' * len(good), 'integrity'),
                              (good + b'0', 'exceeded')):
            with self.subTest(message=message):
                opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(data)
                with self.assertRaisesRegex(setup.SetupError, message):
                    setup.acquire(asset, opener=opener)
        opener.open.side_effect = urllib.error.HTTPError(asset.url, 404, 'missing', None, None)
        with self.assertRaisesRegex(setup.SetupError, 'Could not acquire'):
            setup.acquire(asset, opener=opener)

    def test_archive_paths_and_links_refused_without_touching_outside(self):
        for kind, blob in (
            ('python', tar(('python/../outside', b'unsafe'))),
            ('python', tar(('python/NUL.txt', b'unsafe'))),
            ('python', tar(('python/link', None))),
            ('python', tar(('python/python.exe', b'1'), ('python/PYTHON.EXE', b'2'))),
            ('uv', zip_archive('uv.exe', link=True)),
            ('uv', zip_archive('C:/uv.exe')),
        ):
            with self.subTest(kind=kind, blob=blob[:8]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                stage = FakeStage(root / 'private')
                outside = root / 'outside'
                outside.write_bytes(b'keep')
                with self.assertRaises((ValueError, setup.SetupError)):
                    setup.unpack(stage, blob, kind)
                self.assertEqual(outside.read_bytes(), b'keep')
                self.assertEqual(sorted(p.name for p in root.iterdir() if p != stage.path), ['outside'])

    def test_consent_refusal_never_prepares_storage_or_downloads(self):
        plan, _ = make_plan(Path('destination'))
        report = SimpleNamespace(compatible=True, destination=plan.destination)
        for reply in ('', 'no', 'install', 'INSTALL '):
            with self.subTest(reply=reply), redirect_stdout(io.StringIO()):
                inspect, transaction, build = Mock(return_value=report), Mock(), Mock()
                self.assertEqual(setup.install(ROOT, status=Mock(), confirm=lambda _: reply,
                                              inspect=inspect, make_plan=Mock(return_value=plan),
                                              storage=transaction, build=build), 0)
                inspect.assert_called_once_with()
                transaction.assert_not_called()
                build.assert_not_called()

    def test_destination_change_and_permission_failure_do_not_download(self):
        plan, _ = make_plan(Path('destination'))
        report = SimpleNamespace(compatible=True, destination=plan.destination)
        changed = SimpleNamespace(compatible=True, destination=Path('changed'))
        with redirect_stdout(io.StringIO()):
            transaction, build = Mock(), Mock()
            with self.assertRaisesRegex(setup.SetupError, 'changed after consent'):
                setup.install(ROOT, status=Mock(), confirm=lambda _: 'INSTALL',
                              inspect=Mock(side_effect=[report, changed]), make_plan=Mock(return_value=plan),
                              storage=transaction, build=build)
            transaction.assert_not_called()
            with self.assertRaisesRegex(PermissionError, 'fixture denied'):
                setup.install(ROOT, status=Mock(), confirm=lambda _: 'INSTALL',
                              inspect=Mock(return_value=report), make_plan=Mock(return_value=plan),
                              storage=Mock(side_effect=PermissionError('fixture denied')), build=build)
            build.assert_not_called()
        stage = Mock()
        stage.mkdir.side_effect = PermissionError('private staging denied')
        fetch = Mock()
        with self.assertRaisesRegex(PermissionError, 'private staging denied'):
            setup.populate(plan, stage, Mock(), fetch=fetch)
        fetch.assert_not_called()

    def test_no_completion_receipt_when_acquisition_or_verification_fails(self):
        for failure in ('missing', 'verify', 'corrupt'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                stage = FakeStage(Path(tmp))
                plan, files = make_plan(stage.path)
                if failure == 'corrupt':
                    files[plan.uv.filename] = b'corrupt'
                with self.assertRaisesRegex(RuntimeError, 'fixture|integrity|exceeded'):
                    fixture_build(plan, stage, files, fail=failure)
                self.assertFalse((stage.path / 'complete.json').exists())

    def test_rollback_runs_on_failure_but_preserves_staging_if_process_exit_unknown(self):
        stage = Mock(path=Path('staging'), committed=False)
        rollback = Mock()
        for failure in (PermissionError('denied'), KeyboardInterrupt()):
            with self.assertRaises(type(failure)):
                with storage._verified_stage(stage, rollback):
                    raise failure
        self.assertEqual(rollback.call_count, 2)
        with self.assertRaisesRegex(RuntimeError, 'did not commit'):
            with storage._verified_stage(stage, rollback):
                pass
        self.assertEqual(rollback.call_count, 3)
        with self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved'):
            with storage._verified_stage(stage, rollback):
                raise storage.SetupProcessCleanupUnconfirmed('deadline expired')
        self.assertEqual(rollback.call_count, 3)
        with self.assertRaisesRegex(RuntimeError, 'safe rollback could not finish'):
            with storage._verified_stage(stage, Mock(side_effect=PermissionError('cleanup denied'))):
                raise RuntimeError('installation failed')

    def test_completed_fixture_has_private_runtime_and_no_work_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = FakeStage(Path(tmp))
            plan, files = make_plan(stage.path)
            runtime, actions = fixture_build(plan, stage, files)
            self.assertTrue(runtime.is_file())
            self.assertFalse((stage.path / 'work').exists())
            self.assertEqual(json.loads((stage.path / 'complete.json').read_text())['plan'], plan.identity)
            download_free = [args for args in actions if 'install' in args]
            self.assertEqual(len(download_free), 2)
            for args in download_free:
                self.assertTrue({'--offline', '--no-index', '--no-build', '--no-deps'} <= set(args))
            self.assertIn('--require-hashes', download_free[0])


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows')
class WindowsNativeSetupTests(unittest.TestCase):
    def test_real_preflight_and_source_plan_decline_leave_destination_absent(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint consent ') as tmp:
            with (patch.object(setup.preflight, '_local_app_data', return_value=tmp),
                  patch.dict(os.environ, {'PATH': ''}), redirect_stdout(io.StringIO()) as output):
                build = Mock(side_effect=AssertionError('Setup must not execute without consent.'))
                result = setup.install(ROOT, status=Mock(), confirm=lambda _: '', build=build)
            self.assertEqual(result, 0)
            self.assertIn('no downloads or changes made', output.getvalue())
            self.assertEqual(list(Path(tmp).iterdir()), [])
            build.assert_not_called()

    def test_native_fixture_install_then_lock_failure_and_rollback_preserve_previous(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint 测试 ') as tmp:
            base = Path(tmp)
            destination = base / 'ComposerLint' / 'setup'
            sentinel = base / 'original.musicxml'
            sentinel.write_bytes(b'original')
            plan, files = make_plan(destination)
            with storage.transaction(destination) as stage:
                runtime, _ = fixture_build(plan, stage, files)
                complete = stage.path / 'complete.json'
                with self.assertRaises(BlockingIOError):
                    with storage.transaction(destination):
                        self.fail('A second setup acquired the exclusive lock.')
            self.assertTrue(runtime.is_file())
            before = complete.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'fixture dependency verification failure'):
                with storage.transaction(destination) as stage:
                    failed = stage.path
                    fixture_build(plan, stage, files, fail='verify')
            self.assertFalse(failed.exists())
            self.assertEqual(complete.read_bytes(), before)
            self.assertEqual(sentinel.read_bytes(), b'original')

    def test_ordinary_windows_outputs_inherit_only_private_grants(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint ACL ') as tmp:
            destination = Path(tmp) / 'ComposerLint' / 'setup'
            with storage.transaction(destination) as stage:
                directory = stage.path / 'ordinary'
                directory.mkdir()
                (directory / 'child.txt').write_bytes(b'normal Windows output')
                stage.commit({'fixture': True})
            self.assertTrue((directory.parent / 'complete.json').is_file())

    def test_uncommitted_stage_and_no_replace_receipt(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint receipt ') as tmp:
            destination = Path(tmp) / 'ComposerLint' / 'setup'
            with self.assertRaisesRegex(RuntimeError, 'did not commit'):
                with storage.transaction(destination) as stage:
                    abandoned = stage.path
            self.assertFalse(abandoned.exists())
            with self.assertRaises(OSError):
                with storage.transaction(destination) as stage:
                    collision = stage.path
                    complete = collision / 'complete.json'
                    with stage.create(('complete.json',)) as stream:
                        stream.write(b'keep existing')
                    try:
                        stage.commit({'fixture': True})
                    except OSError:
                        self.assertEqual(complete.read_bytes(), b'keep existing')
                        raise
            self.assertFalse(collision.exists())

    def test_rollback_refuses_junction_and_preserves_original(self):
        import _winapi

        with tempfile.TemporaryDirectory(prefix='ComposerLint rollback ') as tmp:
            base = Path(tmp)
            original = base / 'original'
            original.mkdir()
            sentinel = original / 'score.musicxml'
            sentinel.write_bytes(b'original')
            with self.assertRaisesRegex(RuntimeError, 'safe rollback could not finish'):
                with storage.transaction(base / 'ComposerLint' / 'setup') as stage:
                    junction = stage.path / 'redirect'
                    _winapi.CreateJunction(str(original), str(junction))
                    raise RuntimeError('fixture failure')
            self.assertEqual(sentinel.read_bytes(), b'original')
            self.assertFalse((junction.parent / 'complete.json').exists())
            junction.rmdir()

    def test_one_shot_native_command_uses_private_env_cwd_and_stdin_eof(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint command ') as tmp:
            system_root = str(setup._system_root())
            with patch.dict(os.environ, {'COMPOSERLINT_TEST_AMBIENT': 'must not propagate'}):
                result = setup.run_setup_command(
                    [sys.executable, '-I', '-S', '-B', '-X', 'utf8', '-c',
                     'import os,sys; assert not sys.stdin.read(); '
                     'assert "COMPOSERLINT_TEST_AMBIENT" not in os.environ; '
                     'print(os.environ["MARKER"]); print(os.getcwd())'],
                    cwd=tmp, env={'SystemRoot': system_root, 'MARKER': 'private 测试'}, timeout=15)
            marker, directory = result.decode('utf-8').splitlines()
            self.assertEqual(marker, 'private 测试')
            self.assertEqual(Path(directory), Path(tmp))


if __name__ == '__main__':
    unittest.main()
