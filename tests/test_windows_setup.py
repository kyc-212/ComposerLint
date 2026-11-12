"""Windows terminal setup tests. Default native tests use fixtures, not downloads."""

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
import ctypes
import hashlib
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
import urllib.error
import zipfile


ROOT = Path(__file__).absolute().parents[1]


def setUpModule():
    global setup, storage, workers
    name = '_composerlint_windows_setup_tests'
    spec = ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = [str(ROOT / 'src' / 'composerlint')]
    sys.modules[name] = module_from_spec(spec)
    unittest.addModuleCleanup(_remove_modules, name)
    setup = import_module(f'{name}.windows_setup')
    storage = import_module(f'{name}.windows_install_storage')
    workers = import_module(f'{name}.windows_workers')


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
    def test_worker_launch_stays_positional_and_setup_has_its_own_launch(self):
        def launch_worker(winapi, command, job, handles, stack, /):
            return 'worker process'

        def launch_setup(winapi, command, job, handles, stack, /, *, cwd, env):
            return 'setup process'

        api = SimpleNamespace(
            job=Mock(return_value='owned job'), pipe=Mock(return_value=('parent', 'child')),
            launch=Mock(side_effect=launch_worker), launch_setup=Mock(side_effect=launch_setup),
            stop_job=Mock(),
        )
        winapi = SimpleNamespace(WaitForSingleObject=Mock(return_value=0),
                                 GetExitCodeProcess=Mock(return_value=0))
        security = SimpleNamespace(user_sid=lambda: 'fixture SID',
                                   private_descriptor=lambda sid: nullcontext('descriptor'))
        command = [r'C:\fixture\python.exe']
        cwd, env = r'C:\fixture', {'SystemRoot': r'C:\Windows'}
        with (patch.object(workers, 'sys', SimpleNamespace(platform='win32')),
              patch.dict(sys.modules, {'_winapi': winapi}),
              patch.object(workers, '_WorkerAPI', return_value=api),
              patch.object(workers, '_SecurityAPI', return_value=security),
              patch.object(workers, '_exchange', side_effect=[(b'reply\n', b''), (b'first\nsecond\n', b'')])):
            self.assertEqual(workers.run_worker(command, b'request\n', timeout=15), b'reply\n')
            self.assertEqual(workers.run_setup_command(command, cwd=cwd, env=env, timeout=15),
                             b'first\nsecond\n')
        api.launch.assert_called_once()
        api.launch_setup.assert_called_once()
        self.assertEqual(api.launch_setup.call_args.kwargs, {'cwd': cwd, 'env': env})

    def test_exact_asset_pins_match_lock_and_preflight(self):
        manifest = (ROOT / 'tools/windows-setup-assets.json').read_bytes()
        lock = (ROOT / 'uv.lock').read_bytes()
        crlf_count = lock.count(b'\r\n')
        self.assertEqual(hashlib.sha256(lock).hexdigest(), json.loads(manifest)['lock_sha256'],
                         f'Checkout uv.lock has {len(lock)} bytes and {crlf_count} CRLF endings.')
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

    def test_install_refuses_corrupted_or_transformed_lock_before_consent(self):
        reviewed = (ROOT / 'uv.lock').read_bytes()
        self.assertIn(b'version = 1\n', reviewed)
        changes = (reviewed.replace(b'version = 1\n', b'version = 2\n', 1),
                   reviewed.replace(b'\n', b'\r\n'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / 'tools' / 'windows-setup-assets.json'
            manifest.parent.mkdir()
            manifest.write_bytes((ROOT / 'tools' / manifest.name).read_bytes())
            destination = root / 'destination'
            confirm, transaction, build = Mock(), Mock(), Mock()
            report = SimpleNamespace(compatible=True, destination=destination)
            for changed in changes:
                with self.subTest(size=len(changed)):
                    (root / 'uv.lock').write_bytes(changed)
                    with (patch.object(setup, 'read_stable', side_effect=lambda path, limit:
                                       (path.read_bytes(), SimpleNamespace(links=1))),
                          self.assertRaisesRegex(setup.SetupError, 'uv.lock changed')):
                        setup.install(root, status=Mock(), inspect=lambda: report,
                                      confirm=confirm, storage=transaction, build=build)
            for operation in (confirm, transaction, build):
                operation.assert_not_called()
            self.assertFalse(destination.exists())

    def test_setup_adjusts_only_a_separate_child_token_with_the_same_user(self):
        private = import_module(f'{storage.__package__}.windows_private')
        security = object.__new__(private._SecurityAPI)
        security.current_process = lambda: 'parent'
        security.user_sid = lambda: 'user SID'
        security._token = lambda *, process, access=0: nullcontext(process)
        user = private._SidAndAttributes(123, 0)
        for child_sid, child_id, expected_error in (
            ('foreign SID', b'child-id', 'token user differs'),
            ('user SID', b'parentid', 'separate primary token'),
            ('user SID', b'child-id', None),
        ):
            with self.subTest(child_sid=child_sid, child_id=child_id):
                security.sid_text = Mock(return_value=child_sid)
                security._default_owner = Mock(side_effect=['administrators SID', 'user SID'])
                security._token_data = Mock(side_effect=lambda token, kind: (
                    user if kind == private._TOKEN_USER else
                    b'parentid' if token == 'parent' else child_id))
                security.set_token_info = Mock(return_value=True)
                if expected_error:
                    with self.assertRaisesRegex(PermissionError, expected_error):
                        security.set_setup_child_owner('child')
                    security.set_token_info.assert_not_called()
                else:
                    security.set_setup_child_owner('child')
                    self.assertEqual(security.set_token_info.call_args.args[:2],
                                     ('child', private._TOKEN_OWNER))
                    self.assertEqual(security._default_owner.call_args_list, [call('child'), call('child')])

    def test_wrong_owner_is_rejected_and_rollback_cannot_remove_it(self):
        security = object.__new__(storage._SecurityAPI)
        security.sid_text = Mock(return_value='S-1-5-32-544')
        security.free = Mock()
        security.descriptor_control = Mock()

        def get_security(handle, kind, info, owner, group, acl, sacl, descriptor):
            ctypes.cast(owner, ctypes.POINTER(ctypes.c_void_p))[0] = 1
            return 0

        security.get_security = get_security
        disk = Mock()
        disk.open_component.return_value = 'wrong-owner handle'
        path = PureWindowsPath(r'C:\fixture\stage')
        stage = storage.Stage(path, disk, security, 'user SID', [('root handle', 'root ID')], [])
        stage.validate = Mock()  # The held generation root already passed verification.
        entry = SimpleNamespace(name='foreign.txt', is_dir=lambda **_: False)
        fake_os = SimpleNamespace(scandir=lambda _: nullcontext(iter([entry])))

        def rollback():
            stage._scan(stage.handle, stage.path, remove=True, budget=[1])

        with patch.object(storage, 'os', fake_os):
            with self.assertRaisesRegex(RuntimeError, 'safe rollback could not finish.*owner differs'):
                with storage._verified_stage(stage, rollback):
                    stage._open(stage.handle, entry.name, directory=False)
        security.descriptor_control.assert_not_called()
        self.assertEqual(disk.close.call_args_list, [call('wrong-owner handle')] * 2)
        disk.remove.assert_not_called()

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
        stage = Mock(path=Path('staging'), committed=False, work_cleanup_incomplete=False,
                     receipt_started=False)
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


class WindowsReceiptTests(unittest.TestCase):
    def setUp(self):
        self.disk = Mock()
        self.disk.open_component.side_effect = ['pending handle', 'published handle']
        self.disk.query.return_value = SimpleNamespace(links=1)
        self.data = b'{"fixture": true}\n'
        self.stamp = SimpleNamespace(volume=1, file_id=b'file ID', links=1, size=len(self.data))
        self.disk.stamp.return_value = self.stamp
        security = Mock()
        security.private_descriptor.return_value = nullcontext('private descriptor')
        self.stage = storage.Stage(PureWindowsPath(r'C:\fixture\stage'), self.disk, security,
                                   'user SID', [('root handle', 'root ID')], [])
        self.stage.audit = Mock()
        self.stage.validate = Mock()
        self.rollback = Mock()
        self.pending = io.BytesIO()
        self.pending.fileno = lambda: 42
        self.complete = io.BytesIO(self.data)
        self.fsync = Mock()
        self.enterContext(patch.object(storage, 'os', SimpleNamespace(fsync=self.fsync)))
        streams = {'pending handle': self.pending, 'published handle': self.complete}
        self.enterContext(patch.object(storage, '_stream', side_effect=lambda disk, handle, mode:
                                       nullcontext(streams[handle])))

    def test_native_request_is_fixed_same_directory_and_no_replace(self):
        disk = object.__new__(storage._InstallDisk)
        disk.set_native_info = Mock(return_value=0)
        disk.status_to_error = Mock()
        disk.publish(42)
        handle, _, buffer, size, info_class = disk.set_native_info.call_args.args
        info = storage._RenameInfo.from_buffer(buffer)
        self.assertEqual((handle, info_class, info.parent, info.flags), (42, 10, None, 0))
        encoded = 'complete.json'.encode('utf-16-le')
        self.assertEqual(info.length, len(encoded))
        self.assertEqual(size, ctypes.sizeof(info) + len(encoded))
        self.assertEqual(ctypes.string_at(ctypes.addressof(buffer) + storage._RenameInfo.filename.offset,
                                          info.length), encoded)
        for code in (32, 5):  # Sharing violation and access denied.
            with self.subTest(code=code):
                disk.set_native_info.return_value = -1
                disk.status_to_error.return_value = code
                with (patch.object(storage.ctypes, 'WinError', OSError, create=True),
                      self.assertRaises(OSError) as raised):
                    disk.publish(42)
                self.assertEqual(raised.exception.args, (code,))
                disk.status_to_error.assert_called_with(-1)

    def test_verified_publication_reopens_only_relative_to_the_held_directory(self):
        with storage._verified_stage(self.stage, self.rollback):
            self.stage.commit({'fixture': True})
        self.assertTrue(self.stage.committed)
        self.assertEqual(self.pending.getvalue(), self.data)
        self.fsync.assert_called_once_with(42)
        self.disk.publish.assert_called_once_with('pending handle')
        self.rollback.assert_not_called()
        pending, complete = self.disk.open_component.call_args_list
        self.assertEqual(pending.args, ('root handle', 'receipt.pending'))
        self.assertEqual(complete.args, ('root handle', 'complete.json'))
        self.assertEqual((pending.kwargs['disposition'], pending.kwargs['share']), (storage._FILE_CREATE, 0))
        self.assertEqual((complete.kwargs['disposition'], complete.kwargs['share']),
                         (storage._FILE_OPEN, storage._SHARE_READ))
        for opened in (pending, complete):
            self.assertTrue(opened.kwargs['refuse_reparse'])
        self.stage.security.require_private.assert_any_call('pending handle', 'user SID')
        self.stage.security.require_private.assert_any_call('published handle', 'user SID')

    def test_missing_pending_receipt_is_preserved_without_publishing(self):
        self.disk.stamp.side_effect = FileNotFoundError('pending receipt has no stable link')
        with self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved.*no stable link'):
            with storage._verified_stage(self.stage, self.rollback):
                self.stage.commit({'fixture': True})
        self.disk.publish.assert_not_called()
        self.rollback.assert_not_called()
        self.assertFalse(self.stage.committed)

    def test_changed_pending_bytes_are_preserved_without_publishing(self):
        def corrupt_pending(fd):
            self.pending.seek(0)
            self.pending.write(b'X')

        self.fsync.side_effect = corrupt_pending
        with self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved.*bytes or link count changed'):
            with storage._verified_stage(self.stage, self.rollback):
                self.stage.commit({'fixture': True})
        self.disk.publish.assert_not_called()
        self.rollback.assert_not_called()
        self.assertFalse(self.stage.committed)

    def assert_final_verification_refused(self, message):
        with self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved.*' + message):
            with storage._verified_stage(self.stage, self.rollback):
                self.stage.commit({'fixture': True})
        self.disk.publish.assert_called_once_with('pending handle')
        self.assertFalse(self.stage.committed)
        self.rollback.assert_not_called()

    def test_missing_final_receipt_leaves_setup_incomplete(self):
        self.disk.open_component.side_effect = ['pending handle', FileNotFoundError('missing final receipt')]
        self.assert_final_verification_refused('missing final receipt')

    def test_redirected_final_receipt_leaves_setup_incomplete(self):
        self.disk.open_component.side_effect = ['pending handle', PermissionError('reparse point')]
        self.assert_final_verification_refused('reparse point')

    def test_replaced_final_receipt_with_same_bytes_leaves_setup_incomplete(self):
        foreign = SimpleNamespace(**(vars(self.stamp) | {'file_id': b'other ID'}))
        self.disk.stamp.side_effect = [self.stamp, self.stamp, foreign, foreign]
        self.assert_final_verification_refused('different file identity')

    def test_corrupted_final_receipt_leaves_setup_incomplete(self):
        self.complete.write(b'X')
        self.assert_final_verification_refused('bytes or link count changed')

    def test_final_receipt_changed_during_verification_leaves_setup_incomplete(self):
        changed = SimpleNamespace(**(vars(self.stamp) | {'change_time': 2}))
        self.disk.stamp.side_effect = [self.stamp, self.stamp, self.stamp, changed]
        self.assert_final_verification_refused('changed during verification')

    def test_installer_reports_no_success_after_rename_error(self):
        plan, _ = make_plan(self.stage.path)
        report = SimpleNamespace(compatible=True, destination=plan.destination)
        failure = OSError('sharing violation')
        self.disk.publish.side_effect = failure

        def build(plan, stage, status):
            stage.commit({'fixture': True})

        with (redirect_stdout(io.StringIO()) as output,
              self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved.*sharing violation') as raised):
            setup.install(ROOT, status=Mock(), confirm=lambda _: 'INSTALL', inspect=lambda: report,
                          make_plan=Mock(return_value=plan),
                          storage=lambda _: storage._verified_stage(self.stage, self.rollback), build=build)
        self.assertIs(raised.exception.__cause__, failure)
        self.assertNotIn('environment verified:', output.getvalue())
        self.assertFalse(self.stage.committed)
        self.rollback.assert_not_called()

    def test_partial_work_cleanup_preserves_stage_and_blocks_receipt(self):
        self.disk.open_component.side_effect = lambda parent, name, **kwargs: name
        self.disk.identity.side_effect = lambda handle: (1, handle)
        denied = PermissionError('work member is in use')
        self.disk.remove.side_effect = [None, denied]
        entries = [SimpleNamespace(name=name, is_dir=lambda **_: False)
                   for name in ('removed.tmp', 'blocked.tmp')]

        with (patch.object(storage.os, 'scandir', create=True, return_value=nullcontext(iter(entries))),
              self.assertRaisesRegex(RuntimeError, 'cleanup could not finish.*incomplete setup preserved') as raised):
            with storage._verified_stage(self.stage, self.rollback):
                self.stage.clear_work()

        self.assertIs(raised.exception.__cause__, denied)
        self.assertEqual(self.disk.remove.call_args_list, [call('removed.tmp'), call('blocked.tmp')])
        with self.assertRaisesRegex(RuntimeError, 'cleanup is incomplete'):
            self.stage.commit({'fixture': True})
        self.disk.publish.assert_not_called()
        self.rollback.assert_not_called()
        self.assertFalse(self.stage.committed)


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
                with stage.security._token() as token:
                    default_owner = stage.security._default_owner(token)
                print(f'Staging fixture TokenUser={stage.sid} TokenOwner={default_owner}')
                if default_owner != stage.sid:
                    # This fixture deliberately creates the old failure with
                    # its own unmodified token, then removes only its probe.
                    probe = stage.path / 'default-owner-probe'
                    probe.touch(exist_ok=False)
                    try:
                        with self.assertRaisesRegex(PermissionError, f'owner {default_owner},'):
                            stage._open(stage.handle, probe.name, directory=False)
                    finally:
                        probe.unlink()
                child = ('from pathlib import Path; '
                         'Path("ordinary").mkdir(); '
                         'Path("ordinary/child.txt").write_bytes(b"normal Windows output")')
                setup.run_setup_command(
                    [sys.executable, '-I', '-S', '-B', '-c',
                     'import subprocess,sys; subprocess.run([sys.executable, "-I", "-S", "-B", '
                     '"-c", sys.argv[1]], check=True)', child],
                    cwd=str(stage.path), env={'SystemRoot': str(setup._system_root())}, timeout=15)
                with stage.security._token() as token:
                    self.assertEqual(stage.security._default_owner(token), default_owner)
                stage.commit({'fixture': True})
            self.assertEqual((stage.path / 'ordinary' / 'child.txt').read_bytes(), b'normal Windows output')
            self.assertTrue((stage.path / 'complete.json').is_file())

    def test_explicit_staging_creates_protected_user_owned_directories_and_files(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint protected ') as tmp:
            with storage.transaction(Path(tmp) / 'ComposerLint' / 'setup') as stage:
                with stage.create(('nested', 'data')) as stream:
                    stream.write(b'fixture')
                stage.commit({'fixture': True})
                with ExitStack() as held:
                    directory = stage._directories(('nested',), held)
                    stage.security.require_private(directory, stage.sid, tree_root=True)
                    for parent, name in ((directory, 'data'), (stage.handle, 'complete.json')):
                        handle = stage._open(parent, name, directory=False)
                        held.callback(stage.disk.close, handle)
                        stage.security.require_private(handle, stage.sid)

    def test_same_directory_receipt_rename_avoids_verified_parent_share_conflict(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint receipt ') as tmp:
            destination = Path(tmp) / 'ComposerLint' / 'setup'
            with storage.transaction(destination) as stage:
                native = stage.disk.set_native_info
                source_ids = []

                def compare_rename_requests(handle, io_status, buffer, size, info_class):
                    legacy = ctypes.create_string_buffer(bytes(buffer), size)
                    storage._RenameInfo.from_buffer(legacy).parent = stage.handle
                    status = native(handle, io_status, legacy, size, info_class)
                    self.assertLess(status, 0)
                    self.assertEqual(stage.disk.status_to_error(status), 32)
                    source_ids.append(stage.disk.identity(handle))
                    return native(handle, io_status, buffer, size, info_class)

                with patch.object(stage.disk, 'set_native_info', side_effect=compare_rename_requests):
                    stage.commit({'fixture': True})
                final = stage._open(stage.handle, 'complete.json', directory=False)
                try:
                    self.assertEqual(source_ids, [stage.disk.identity(final)])
                finally:
                    stage.disk.close(final)
            self.assertEqual((stage.path / 'complete.json').read_bytes(), b'{"fixture": true}\n')
            self.assertFalse((stage.path / 'receipt.pending').exists())
            self.assertFalse((destination / 'complete.json').exists())

    def test_uncommitted_stage_and_no_replace_receipt(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint receipt ') as tmp:
            destination = Path(tmp) / 'ComposerLint' / 'setup'
            with self.assertRaisesRegex(RuntimeError, 'did not commit'):
                with storage.transaction(destination) as stage:
                    abandoned = stage.path
            self.assertFalse(abandoned.exists())
            with self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved') as raised:
                with storage.transaction(destination) as stage:
                    collision = stage.path
                    complete = collision / 'complete.json'
                    with stage.create(('complete.json',)) as stream:
                        stream.write(b'keep existing')
                    stage.commit({'fixture': True})
            self.assertIn(raised.exception.__cause__.winerror, (80, 183))
            self.assertFalse(stage.committed)
            self.assertEqual(complete.read_bytes(), b'keep existing')
            self.assertEqual((collision / 'receipt.pending').read_bytes(), b'{"fixture": true}\n')

    def test_receipt_cannot_follow_a_destination_junction_created_after_audit(self):
        import _winapi

        with tempfile.TemporaryDirectory(prefix='ComposerLint receipt redirect ') as tmp:
            base = Path(tmp)
            outside = base / 'outside'
            outside.mkdir()
            sentinel = outside / 'complete.json'
            sentinel.write_bytes(b'keep outside')
            with self.assertRaisesRegex(RuntimeError, 'incomplete setup preserved'):
                with storage.transaction(base / 'ComposerLint' / 'setup') as stage:
                    junction = stage.path / 'complete.json'
                    publish = stage.disk.publish

                    def redirect_then_publish(handle):
                        _winapi.CreateJunction(str(outside), str(junction))
                        return publish(handle)

                    with patch.object(stage.disk, 'publish', side_effect=redirect_then_publish):
                        stage.commit({'fixture': True})
            self.assertFalse(stage.committed)
            self.assertTrue(junction.is_junction())
            self.assertEqual(sentinel.read_bytes(), b'keep outside')
            self.assertEqual((stage.path / 'receipt.pending').read_bytes(), b'{"fixture": true}\n')
            junction.rmdir()

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
