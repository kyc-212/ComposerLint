"""Windows terminal setup tests. Default native tests use fixtures, not downloads."""

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr, redirect_stdout
import ctypes
import hashlib
from importlib import util
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import subprocess
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


class WindowsCompilerProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = util.spec_from_file_location('build_windows_renderer', ROOT / 'tools' / 'build_windows_renderer.py')
        cls.probe = util.module_from_spec(spec)
        with patch.object(sys, 'dont_write_bytecode', True):
            spec.loader.exec_module(cls.probe)
        cls.addClassCleanup(_remove_modules, cls.probe.PACKAGE)

    def test_abi_source_emits_cpp_escapes_instead_of_physical_newlines(self):
        source = self.probe.PYTHON_ABI_PROBE
        for line in (
            br'    std::printf("CPython stable ABI loaded: %s\n", version);',
            br'        std::fprintf(stderr, "Cannot identify loaded Python DLL (Windows error %lu)\n", GetLastError());',
            br'        std::fwprintf(stderr, L"Unexpected loaded DLL: %ls (expected %ls)\n", actual, expected);',
        ):
            self.assertIn(line, source.splitlines())
        compile(self.probe.ABI_LOADER, '<stable ABI loader>', 'exec')

    def test_probe_failure_reports_both_streams_and_the_exit_status(self):
        executable = PureWindowsPath(r'C:\Program Files\MSVC\cl.exe')
        include = PureWindowsPath(r'C:\Python Tools\include')
        cwd = PureWindowsPath(r'C:\Private Workspace')
        env = {'SystemRoot': r'C:\Windows'}
        result = subprocess.CompletedProcess([], 2, b'fatal compiler diagnostic', b'additional context')
        with (patch.object(self.probe.workers, 'run_setup_command', return_value=result) as run,
              self.assertRaises(RuntimeError) as raised):
            self.probe.run_probe_command('Compile ABI', executable, '/I', include, cwd=cwd, env=env)
        run.assert_called_once_with([str(executable), '/I', str(include)], cwd=str(cwd), env=env,
                                    timeout=120, capture_result=True)
        for expected in ('Compile ABI failed', '0x00000002', 'stdout: fatal compiler diagnostic',
                         'stderr: additional context',
                         'command: ' + subprocess.list2cmdline([str(executable), '/I', str(include)])):
            self.assertIn(expected, str(raised.exception))

    def test_successful_link_keeps_stderr_separate_from_stdout(self):
        result = subprocess.CompletedProcess([], 0, b'link output\r\n', b'link warning\r\n')
        with (patch.object(self.probe.workers, 'run_setup_command', return_value=result),
              redirect_stderr(io.StringIO()) as diagnostics):
            output = self.probe.run_probe_command('Link ABI', 'link.exe', cwd='work', env={})
        self.assertEqual(output, 'link output')
        self.assertEqual(diagnostics.getvalue(), 'link warning\r\n')

    def test_renderer_build_passes_literal_cmake_paths_as_separate_arguments(self):
        vc = PureWindowsPath(r'C:\Program Files\Microsoft Visual Studio\18\VC\bin\Hostx64\x64')
        sdk = PureWindowsPath(r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64')
        tools = {'cl': vc / 'cl.exe', 'link': vc / 'link.exe', 'rc': sdk / 'rc.exe', 'mt': sdk / 'mt.exe'}
        workspace = PureWindowsPath(r"D:\构建 & score (#2)\éditeur's [x64] + 100% = {name}")
        python = workspace / 'python' / 'python.exe'
        interpreter = workspace / 'build-tools' / 'Scripts' / 'python.exe'
        arguments = self.probe.renderer_build_arguments(tools, python, workspace)
        with patch.object(self.probe.workers, 'run_setup_command',
                          return_value=subprocess.CompletedProcess([], 0, b'', b'')) as launch:
            self.probe.run_probe_command('Build renderer', interpreter, *arguments,
                                         cwd=workspace, env={'SystemRoot': r'C:\Windows'})
        command = launch.call_args.args[0]
        self.assertEqual(command[:7], [str(interpreter), '-I', '-B', '-m', 'build', '--no-isolation', '--wheel'])
        defines = dict(option.removeprefix('-Ccmake.define.').split('=', 1)
                       for option in command if option.startswith('-Ccmake.define.'))
        base = "D:/构建 & score (#2)/éditeur's [x64] + 100% = {name}"
        self.assertEqual(defines, {
            'CMAKE_CXX_COMPILER': 'C:/Program Files/Microsoft Visual Studio/18/VC/bin/Hostx64/x64/cl.exe',
            'CMAKE_C_COMPILER': 'C:/Program Files/Microsoft Visual Studio/18/VC/bin/Hostx64/x64/cl.exe',
            'CMAKE_LINKER': 'C:/Program Files/Microsoft Visual Studio/18/VC/bin/Hostx64/x64/link.exe',
            'CMAKE_RC_COMPILER': 'C:/Program Files (x86)/Windows Kits/10/bin/10.0.26100.0/x64/rc.exe',
            'CMAKE_MT': 'C:/Program Files (x86)/Windows Kits/10/bin/10.0.26100.0/x64/mt.exe',
            'Python_EXECUTABLE': f'{base}/python/python.exe',
            'Python_ROOT_DIR': f'{base}/python',
            'Python_INCLUDE_DIR': f'{base}/python/include',
            'Python_SABI_LIBRARY': f'{base}/python/libs/python3.lib',
            'CMAKE_FIND_USE_PACKAGE_REGISTRY': 'OFF',
            'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY': 'OFF',
        })
        self.assertTrue(all('\\' not in argument for argument in command[1:]))
        self.assertEqual(command[-4].removeprefix('-Cbuild-dir=').format(name='verovio'), f'{base}/build')
        self.assertEqual(command[-3:], ['--outdir', f'{base}/dist', f'{base}/verovio-6.3.0'])

    def test_renderer_build_rejects_paths_that_cmake_would_reinterpret(self):
        root = PureWindowsPath(r'D:\private build')
        tools = {name: root / (name + '.exe') for name in ('cl', 'link', 'rc', 'mt')}
        for component in ('compiler;other', 'compiler${name}', 'compiler$ENV{PATH}', 'compiler$CACHE{path}',
                          'compiler$<CONFIG>'):
            with self.subTest(component=component), self.assertRaisesRegex(ValueError, 'cannot use this path literally'):
                self.probe.renderer_build_arguments(tools | {'rc': root / component / 'rc.exe'},
                                                    root / 'python.exe', root)

    def test_uv_0123_build_tool_sync_uses_offline_hash_pinned_wheels(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint uv build ') as temporary:
            root = Path(temporary)
            python = root / 'python' / 'python.exe'
            for name in ('include/Python.h', 'libs/python3.lib'):
                file = python.parent / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            args = SimpleNamespace(probe_only=False, inputs=root / 'inputs', uv=root / 'uv.exe',
                                   wheelhouse=root / 'wheelhouse', build_requirements=root / 'build.txt')
            developer = {'INCLUDE': 'headers', 'LIB': 'libs', 'VCToolsVersion': 'fixture',
                         'WindowsSDKVersion': 'fixture'}
            tools = {name: root / (name + '.exe') for name in ('cl', 'rc')}
            stage = Mock(path=root / 'stage')
            runtime = SimpleNamespace(version_info=(3, 12, 13), _base_executable=str(python),
                                      version='3.12.13 fixture')
            stop = RuntimeError('Reached build dependency installation')
            with (patch.object(self.probe, 'sys', runtime),
                  patch.object(self.probe, 'toolchain', return_value=(developer, tools)),
                  patch.object(self.probe, 'probe'),
                  patch.object(self.probe, 'verify_probe_tools'),
                  patch.object(self.probe.paths, 'temporary_parent', return_value=root),
                  patch.object(self.probe.storage, 'private_workspace', return_value=nullcontext(stage)),
                  patch.object(self.probe.setup, '_system_root', return_value=root / 'Windows'),
                  patch.object(self.probe.recipe, 'checked_bytes', return_value=b'verified'),
                  patch.object(self.probe.setup, 'unpack'),
                  patch.object(self.probe, 'run_probe_command', side_effect=['', stop]) as run,
                  self.assertRaisesRegex(RuntimeError, str(stop))):
                self.probe.build(args)
            prefix = ('uv.exe', args.uv, '--offline', '--no-config', '--no-progress', '--no-python-downloads')
            tools_env = stage.path / 'build-tools'
            self.assertEqual([command.args for command in run.call_args_list], [
                (*prefix, 'venv', '--python', str(python), tools_env),
                (*prefix, 'pip', 'sync', '--python', tools_env / 'Scripts/python.exe', '--require-hashes',
                 '--no-index', '--no-build', '--link-mode', 'copy', '--find-links',
                 args.wheelhouse, args.build_requirements),
            ])

    def test_uv_0123_renderer_control_sync_and_both_installs_use_verified_wheels(self):
        spec = util.spec_from_file_location('ci_windows_renderer', ROOT / 'tools' / 'ci_windows_renderer.py')
        checkpoint = util.module_from_spec(spec)
        with (patch.dict(sys.modules, {'build_windows_renderer': self.probe}),
              patch.object(sys, 'path', sys.path[:])):
            spec.loader.exec_module(checkpoint)

        with tempfile.TemporaryDirectory(prefix='ComposerLint uv control ') as temporary:
            root = Path(temporary)
            stage = Mock(path=root)
            stage.mkdir.side_effect = lambda parts: root.joinpath(*parts).mkdir()
            stage.held_file.side_effect = lambda parts: nullcontext(root.joinpath(*parts))
            stage.held_directory.side_effect = lambda parts: nullcontext(root.joinpath(*parts))
            pins = checkpoint.recipe
            bundle = root / 'renderer-artifacts'
            provenance = dict(source_sha256=pins.SOURCE_SHA256, patch_sha256=pins.PATCH_SHA256,
                              preimage_sha256=pins.PREIMAGE, postimage_sha256=pins.POSTIMAGE,
                              wheel_sha256='fixture')
            runtime = root / 'runtime' / 'Scripts' / 'python.exe'
            renderer = root / 'renderer' / 'Scripts' / 'python.exe'
            uv_commands = []
            identity_commands = []
            stop = RuntimeError('Reached patched renderer load')

            def command(_, executable, *args, **kwargs):
                if executable.name == 'uv.exe':
                    uv_commands.append(args)
                    return 'uv 0.12.3' if '--version' in args else ''
                if '--output' in args:
                    bundle.mkdir()
                    for name in (pins.SOURCE_ARCHIVE, pins.OFFICIAL_WHEEL, pins.PATCH,
                                 pins.PATCHED_WHEEL, 'build-provenance.json'):
                        (bundle / name).touch()
                if executable in (runtime, renderer):
                    identity_commands.append((executable, *args))
                if checkpoint.IDENTITY_TESTS in args:
                    scratch = root / 'tmp' / 'identity-pytest'
                    self.assertTrue(scratch.is_dir())
                    self.assertEqual(kwargs['env']['COMPOSERLINT_TEMP_DIR'], str(scratch))
                    stage.held_directory.assert_called_once_with(('tmp', 'identity-pytest'))
                if executable == renderer:
                    raise stop
                return ''

            with (patch.object(checkpoint.setup, 'acquire', return_value=b'verified'),
                  patch.object(checkpoint.setup, '_write'),
                  patch.object(checkpoint.setup, 'unpack'),
                  patch.object(checkpoint.setup, '_read_source', return_value=json.dumps(provenance).encode()),
                  patch.object(checkpoint.setup, '_system_root', return_value=root / 'Windows'),
                  patch.object(checkpoint.recipe, 'checked_bytes'),
                  patch.object(checkpoint, 'toolchain', return_value=({}, {})),
                  patch.object(checkpoint, 'run_probe_command', side_effect=command),
                  redirect_stdout(io.StringIO()),
                  self.assertRaisesRegex(RuntimeError, str(stop))):
                checkpoint.checkpoint(stage, *checkpoint.reviewed_assets())
            prefix = ('--offline', '--no-config', '--no-progress', '--no-python-downloads')
            policy = ('--no-index', '--no-build', '--link-mode', 'copy')
            python = root / 'python' / 'python.exe'
            self.assertEqual(uv_commands, [
                (*prefix, '--version'),
                (*prefix, 'venv', '--python', python, runtime.parents[1]),
                (*prefix, 'pip', 'sync', '--python', runtime, *policy, '--require-hashes',
                 '--find-links', root / 'wheelhouse', root / 'work' / 'controls.txt'),
                (*prefix, 'pip', 'install', '--python', runtime, *policy, '--no-deps', bundle / pins.OFFICIAL_WHEEL),
                (*prefix, 'pip', 'check', '--python', runtime),
                (*prefix, 'venv', '--python', python, renderer.parents[1]),
                (*prefix, 'pip', 'install', '--python', renderer, *policy, '--no-deps', bundle / pins.PATCHED_WHEEL),
                (*prefix, 'pip', 'check', '--python', renderer),
            ])
            self.assertEqual(identity_commands, [
                (runtime, '-I', '-B', '-c', checkpoint.PYTEST, checkpoint.ROOT, '-v', '-x',
                 '-p', 'no:cacheprovider', '-p', 'no:tmpdir',
                 checkpoint.IDENTITY_TESTS),
                (runtime, '-I', '-B', '-c', pins.IDENTITY_PROBE, '6.3.0', runtime, python),
                (renderer, '-I', '-B', '-c', pins.IDENTITY_PROBE, pins.BUILD, renderer, python),
            ])

    def test_probe_environment_preserves_profile_and_limits_tool_settings(self):
        system = PureWindowsPath(r'C:\Windows')
        temp = PureWindowsPath(r'C:\Private Workspace\tmp')
        vc = PureWindowsPath(r'C:\Developer Tools\MSVC')
        sdk = PureWindowsPath(r'C:\Developer Tools\SDK')
        profile = {'USERPROFILE': r'C:\Users\fixture', 'LOCALAPPDATA': r'D:\Fixture Local',
                   'APPDATA': r'D:\Fixture Roaming', 'HOMEDRIVE': 'C:',
                   'HOMEPATH': r'\Users\fixture', 'HOMESHARE': r'\\host\home',
                   'PROGRAMDATA': r'C:\ProgramData', 'PUBLIC': r'C:\Users\Public',
                   'ALLUSERSPROFILE': r'C:\ProgramData', 'TEMP': r'C:\Original Temp',
                   'CL': '/Fooutside', '_CL_': '/Zi', 'LINK': '/OUT:outside',
                   '_LINK_': '/DEBUG', 'PYTHONPATH': r'C:\Unreviewed Python'}
        developer = {'INCLUDE': str(vc / 'include'), 'LIB': str(vc / 'lib'),
                     'PATH': r'C:\Unreviewed', 'LIBPATH': r'C:\Metadata'}
        env = self.probe.probe_environment(developer, {'cl': vc / 'cl.exe', 'rc': sdk / 'rc.exe'},
                                           system, temp, profile)
        for key in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH',
                    'HOMESHARE', 'PROGRAMDATA', 'PUBLIC', 'ALLUSERSPROFILE'):
            self.assertEqual(env[key], profile[key])
        self.assertEqual(env['TMP'], str(temp))
        self.assertEqual(env['TEMP'], str(temp))
        self.assertEqual(env['PATH'], os.pathsep.join(map(str, (vc, sdk, system / 'System32'))))
        self.assertEqual(env['SystemRoot'], str(system))
        self.assertEqual(env['INCLUDE'], developer['INCLUDE'])
        self.assertEqual(env['LIB'], developer['LIB'])
        self.assertTrue({'CL', '_CL_', 'LINK', '_LINK_', 'PYTHONPATH', 'LIBPATH'}.isdisjoint(env))
        self.assertEqual(profile['TEMP'], r'C:\Original Temp')
        missing = self.probe.probe_environment(developer, {'cl': vc / 'cl.exe', 'rc': sdk / 'rc.exe'},
                                               system, temp, {})
        self.assertTrue(set(self.probe.PROFILE_VARIABLES).isdisjoint(missing))

    def test_probe_steps_keep_outputs_private_and_compile_link_and_load_both_abi_modes(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint probe ') as temporary:
            base = Path(temporary)
            root = base / 'private workspace'
            root.mkdir()
            stage = Mock(path=root)
            stage.mkdir.side_effect = lambda parts: root.joinpath(*parts).mkdir()
            stage.create.side_effect = lambda parts: root.joinpath(*parts).open('xb')
            stage.held_file.side_effect = lambda parts: nullcontext(root.joinpath(*parts))
            stage.held_directory.side_effect = lambda parts: nullcontext(root.joinpath(*parts))
            stage.audit.side_effect = lambda: {
                path.relative_to(root).parts: path.stat().st_ino for path in root.rglob('*')
            }
            tools = {name: base / 'tools' / (name + '.exe') for name in ('cl', 'link', 'rc')}
            python = base / 'Selected Python' / 'python.exe'
            library = python.parent / 'libs' / 'python3.lib'
            module = root / (self.probe.ABI_MODULE + '.pyd')
            import_library = module.with_suffix('.lib')
            export_file = module.with_suffix('.exp')
            generated = (import_library, export_file)
            observed = []

            def command(step, executable, *args, cwd, env):
                self.assertEqual(cwd, root / 'work')
                self.assertEqual(env['TEMP'], str(root / 'tmp'))
                self.assertEqual(env['TMP'], str(root / 'tmp'))
                observed.append((executable, args))
                for prefix in ('/Fo', '/out:'):
                    for arg in args:
                        if str(arg).startswith(prefix):
                            output = Path(str(arg).removeprefix(prefix))
                            self.assertTrue(output.is_relative_to(root))
                            self.assertEqual(output.read_bytes(), b'')
                            output.write_bytes(b'compiled fixture')
                if '/dll' in args:
                    self.assertEqual([arg for arg in args if str(arg).startswith('/implib:')],
                                     [f'/implib:{import_library}'])
                    for output in generated:
                        self.assertEqual(output.parent, root)
                        self.assertFalse(output.exists())
                        with output.open('xb') as stream:
                            stream.write(b'linker-generated fixture')
                if executable == python:
                    self.assertEqual(stage.protect_generated_file.call_args_list, [
                        call((output.name,), output.stat().st_ino) for output in generated
                    ])
                if executable == root / 'abi-exe' / 'python_abi.exe':
                    return f'CPython stable ABI loaded: {sys.version}'
                return ''

            with (patch.object(self.probe, 'run_probe_command', side_effect=command),
                  patch.object(self.probe.setup, '_system_root', return_value=base / 'Windows'),
                  redirect_stdout(io.StringIO())):
                self.probe.run_probe_steps(stage, {'INCLUDE': 'headers', 'LIB': 'libs'}, tools, python,
                                           {'python3.dll': b'stable', 'python312.dll': b'versioned'})

            self.assertEqual([exe for exe, _ in observed], [
                tools['cl'], tools['link'], root / 'probe.exe',
                tools['cl'], tools['link'], root / 'abi-exe' / 'python_abi.exe',
                tools['cl'], tools['link'], python,
            ])
            for index in (3, 6):
                self.assertIn('/std:c++20', observed[index][1])
                self.assertIn(python.parent / 'include', observed[index][1])
                self.assertIn(root / 'python_abi.cpp', observed[index][1])
            self.assertIn('/DABI_EXTENSION', observed[6][1])
            for index in (4, 7):
                self.assertIn(library, observed[index][1])
                self.assertIn('/nodefaultlib:python3.lib', observed[index][1])
                self.assertIn('/machine:x64', observed[index][1])
            self.assertIn(f'/implib:{root / (self.probe.ABI_MODULE + ".lib")}', observed[7][1])
            self.assertEqual(observed[5][1], (root / 'abi-exe' / 'python3.dll',
                                              root / 'abi-exe' / 'python312.dll'))
            self.assertEqual(observed[8][1][-3:], (self.probe.ABI_LOADER, module, python))
            self.assertEqual({p.relative_to(root).parts for p in root.rglob('*') if p.is_file()},
                             set(self.probe.PROBE_FILES))

    def test_generated_file_refuses_unlisted_replaced_or_empty_outputs_before_acl_change(self):
        parts = ('approved.lib',)
        disk, security = Mock(), Mock()
        stage = storage.Stage(Path('workspace'), disk, security, 'sid', [('root', 'id')], [],
                              workspace_members={parts: False})
        with (patch.object(stage, 'validate'),
              patch.object(stage, '_open', return_value='file') as open_member):
            with self.assertRaisesRegex(PermissionError, 'Unexpected generated workspace file'):
                stage.protect_generated_file(('unexpected.lib',), 'id')
            open_member.assert_not_called()
            disk.identity.return_value = 'replacement'
            with self.assertRaisesRegex(PermissionError, 'changed after audit'):
                stage.protect_generated_file(parts, 'original')
            disk.identity.return_value = 'original'
            disk.query.return_value = SimpleNamespace(size=0)
            with self.assertRaisesRegex(RuntimeError, 'output was not written'):
                stage.protect_generated_file(parts, 'original')
        security.protect_file_dacl.assert_not_called()

    def test_strict_cleanup_refuses_unlisted_or_replaced_objects(self):
        path = Path('workspace')
        disk = Mock()
        stage = storage.Stage(path, disk, Mock(), 'sid', [('root', 'id')], [],
                              workspace_members={('expected',): False})
        for name, expected, opened in (
            ('unexpected', 'Unexpected Windows workspace member', False),
            ('expected', 'changed after audit', True),
        ):
            with self.subTest(name=name):
                entry = Mock()
                entry.name = name
                entry.is_dir.return_value = False
                disk.identity.return_value = 'replacement-id'
                with (patch.object(storage.os, 'scandir', return_value=nullcontext([entry])),
                      patch.object(stage, '_open', return_value='file') as open_member,
                      patch.object(stage, '_delete_member') as delete,
                      self.assertRaisesRegex(PermissionError, expected) as raised):
                    stage._scan('root', path, remove=True, budget=[100], verified={('expected',): 'original-id'})
                self.assertIn(str(path / name), str(raised.exception))
                self.assertEqual(open_member.called, opened)
                delete.assert_not_called()

    def test_incomplete_workspace_never_deletes_after_refused_audit(self):
        disk = Mock()
        disk.ancestors.return_value = [('parent', 'parent-identity')]
        disk.open_component.return_value = 'scratch'
        disk.identity.return_value = 'scratch-identity'
        security = Mock()
        security.private_descriptor.return_value = nullcontext()
        stage = Mock()
        stage.audit.side_effect = PermissionError('unexpected owner')
        with (patch.object(storage, '_InstallDisk', return_value=disk),
              patch.object(storage, '_SecurityAPI', return_value=security),
              patch.object(storage, 'Stage', return_value=stage) as create_stage,
              patch.object(storage, 'local_path_text'),
              self.assertRaises(PermissionError) as raised):
            with storage.private_workspace('fixture', 'probe-'):
                raise RuntimeError('compiler diagnostic')
        stage._scan.assert_not_called()
        stage._delete_member.assert_not_called()
        self.assertIn(str(create_stage.call_args.args[0]), raised.exception.__notes__[0])
        self.assertIn('workspace is incomplete', raised.exception.__notes__[0])
        self.assertEqual(str(raised.exception.__context__), 'compiler diagnostic')


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

    def test_full_setup_requires_all_ten_controls_without_skips(self):
        import xml.etree.ElementTree as ET
        clean = ET.fromstring('<testsuites><testsuite tests="10" failures="0" errors="0" skipped="0"/></testsuites>')
        self.assertEqual(setup.control_counts(clean)['tests'], 10)
        for attrs in ('tests="9"', 'tests="10" skipped="1"', 'tests="10" failures="1"',
                      'tests="10" errors="1"'):
            with self.subTest(attrs=attrs), self.assertRaisesRegex(setup.SetupError, 'All 10'):
                setup.control_counts(ET.fromstring(f'<testsuite {attrs}/>'))

    def test_private_dacl_reports_the_rejected_sid_and_grant_without_other_identity_details(self):
        private = import_module(f'{setup.__package__}.windows_private')
        security = object.__new__(private._SecurityAPI)
        user = 'S-1-5-21-1-2-3-1001'
        system = private._SYSTEM_SID
        rejected = 'S-1-5-32-544'
        aces = [private._AllowAce(kind=0, flags=3, mask=0x001f01ff) for _ in range(3)]

        def get_security(handle, object_type, requested, owner, group, acl, sacl, descriptor):
            owner._obj.value = 1
            acl._obj.value = 2
            descriptor._obj.value = 3
            return 0

        def descriptor_control(descriptor, control, revision):
            control._obj.value = 0x1004
            return True

        def get_ace(acl, index, address):
            address._obj.value = ctypes.addressof(aces[index])
            return True

        security.get_security = get_security
        security.descriptor_control = descriptor_control
        security.get_ace = get_ace
        security.free = Mock()
        for trustees in ((system, user), (system, rejected, user), (system, system, user)):
            def acl_info(acl, info, size, kind):
                info._obj.count = len(trustees)
                return True

            with self.subTest(trustees=trustees):
                security.acl_info = acl_info
                security.sid_text = Mock(side_effect=(user, *trustees))
                if len(trustees) == 2:
                    security.require_private('directory', user, tree_root=True)
                    continue
                with self.assertRaises(PermissionError) as raised:
                    security.require_private('directory', user, tree_root=True)
                message = str(raised.exception)
                self.assertIn(f'ACE index 1: SID {trustees[1]}', message)
                self.assertIn('flags=0x03, mask=0x001f01ff, protected=True', message)
                self.assertNotIn(user, message)

    def test_renderer_rejects_nonmatching_source_or_patch(self):
        renderer = import_module(f'{setup.__package__}.windows_renderer')
        patch_bytes = (ROOT / 'patches' / renderer.PATCH).read_bytes()
        self.assertEqual(renderer.sha(patch_bytes), renderer.PATCH_SHA256)
        with self.assertRaisesRegex(ValueError, 'preimage'):
            renderer.apply_reviewed_patch(b'wrong source', patch_bytes)

    def test_full_setup_verifies_both_environments_before_renderer_controls(self):
        recipe = import_module(f'{setup.__package__}.windows_renderer')
        root = ROOT / 'private fixture'
        base = root / 'python/python.exe'
        stage = SimpleNamespace(path=root)
        plan = SimpleNamespace(renderer=(), controls=())
        uv_run = Mock()
        error = RuntimeError('Patched renderer identity failed')
        run = Mock(side_effect=[b"Official renderer verified.\n", error])
        with self.assertRaisesRegex(RuntimeError, str(error)):
            setup.install_renderer(plan, stage, run, uv_run, ('pip',), base, {}, Mock())
        official = ('runtime', 'Scripts', 'python.exe')
        patched = ('renderer', 'Scripts', 'python.exe')
        self.assertEqual(run.call_args_list, [
            call(official, '-I', '-B', '-X', 'utf8', '-c', recipe.IDENTITY_PROBE, '6.3.0', root.joinpath(*official), base),
            call(patched, '-I', '-B', '-X', 'utf8', '-c', recipe.IDENTITY_PROBE, recipe.BUILD, root.joinpath(*patched), base),
        ])

    def test_full_setup_orders_controls_and_installed_mcp_before_commit(self):
        from xml.etree import ElementTree as ET
        recipe = import_module(f'{setup.__package__}.windows_renderer')
        for fail in (None, 'controls', 'mcp'):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory(prefix='ComposerLint 全体 ') as directory:
                root = Path(directory)
                plan, assets = make_plan(root)
                plan = setup.replace(plan, renderer=((recipe.PATCHED_WHEEL, b'wheel fixture'),))
                stage = FakeStage(root)
                events = []

                def command(args, *, cwd, env, timeout):
                    executable, *arguments = args
                    if 'venv' in arguments:
                        (Path(arguments[-1]) / 'Scripts').mkdir(parents=True)
                        (Path(arguments[-1]) / 'Scripts' / 'python.exe').touch()
                        (Path(arguments[-1]) / 'pyvenv.cfg').touch()
                    if recipe.PATCHED_WHEEL in Path(arguments[-1]).name:
                        native = root / 'renderer/Lib/site-packages/verovio/_verovio.pyd'
                        native.parent.mkdir(parents=True)
                        native.touch()
                    if '--version' in arguments:
                        return b'uv 0.12.3'
                    if 'export' in arguments:
                        return b'wheel==1\n'
                    if '-c' in arguments:
                        script = arguments[arguments.index('-c') + 1]
                        if 'struct.calcsize' in script:
                            return b'cpython 3.12.13 8\n'
                        if 'hatchling.build' in script:
                            name = 'composerlint-0.2.0-py3-none-any.whl'
                            (root / 'work' / 'dist' / name).touch()
                            return name.encode('ascii')
                        if script == recipe.IDENTITY_PROBE:
                            events.append(arguments[-3])
                    if 'pytest' in arguments:
                        events.append('controls')
                        report = next(arg.split('=', 1)[1] for arg in arguments if arg.startswith('--junitxml='))
                        ET.ElementTree(ET.Element('testsuite', tests='9' if fail == 'controls' else '10')).write(report)
                    if '--full' in arguments:
                        events.append('mcp')
                        self.assertEqual(Path(executable), root / 'runtime/Scripts/python.exe')
                        self.assertEqual(arguments, ['-I', '-B', '-X', 'utf8',
                                                     str(root / 'work/controls/tools/ci_windows_mcp.py'), '--full'])
                        self.assertEqual(Path(cwd), root / 'work/controls')
                        self.assertEqual(env['COMPOSERLINT_RENDERER_CONFIG'], str(root / 'renderer.json'))
                        self.assertFalse((root / 'complete.json').exists())
                        if fail == 'mcp':
                            raise RuntimeError('MCP fixture failed')
                    return b''

                build = lambda: setup.populate(plan, stage, Mock(), command=command,
                                               fetch=lambda asset: assets[asset.filename],
                                               windows_root=lambda: root / 'System root')
                with redirect_stdout(io.StringIO()):
                    if fail is None:
                        build()
                        self.assertEqual(json.loads((root / 'complete.json').read_bytes())['mcp_tools'], 18)
                        self.assertFalse((root / 'work').exists())
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'All 10' if fail == 'controls' else 'MCP fixture'):
                            build()
                        self.assertFalse((root / 'complete.json').exists())
                self.assertEqual(events, ['6.3.0', recipe.BUILD, 'controls']
                                 + ([] if fail == 'controls' else ['mcp']))

    def test_installer_command_reports_exit_and_both_output_streams(self):
        arguments = [r'D:\Private Tools\python.exe', '-I', r'D:\Control Files\probe.py']
        failure = subprocess.CompletedProcess(arguments, 0xC0000135, b'earlier gate passed', b'cannot load DLL')
        with patch.object(setup, 'run_setup_command', return_value=failure) as run:
            with self.assertRaises(setup.SetupError) as error:
                setup.checked_command(arguments, cwd=r'D:\Control Files', env={}, timeout=60)
        run.assert_called_once_with(arguments, cwd=r'D:\Control Files', env={}, timeout=60, capture_result=True)
        for message in ('0xC0000135', 'earlier gate passed', 'cannot load DLL', r'"D:\Private Tools\python.exe"'):
            self.assertIn(message, str(error.exception))

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
    def test_private_identity_scratch_and_nested_fixture_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint identity scratch ') as parent:
            parts = ('tmp', 'identity-pytest')
            with storage.private_workspace(Path(parent), 'renderer-') as stage:
                stage.mkdir(parts)
                outer = stage.path
                with stage.held_directory(parts) as scratch:
                    with storage.private_workspace(scratch, 'identity-') as fixture:
                        with fixture.create(('runtime', 'Scripts', 'python.exe')) as output:
                            output.write(b'verified fixture')
                        with fixture.held_directory(('runtime', 'Scripts')) as scripts:
                            self.assertEqual((scripts / 'python.exe').read_bytes(), b'verified fixture')
                        test_path = fixture.path
                    self.assertFalse(test_path.exists())
            self.assertFalse(outer.exists())

    def test_pytest_mode_0700_trustee_is_reported_and_cleanup_refuses_deletion(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint pytest DACL ') as parent:
            with self.assertRaisesRegex(
                    PermissionError, r'Unexpected trustee .*SID S-1-5-32-544, '
                                     r'flags=0x03, mask=0x001f01ff, protected=True') as raised:
                with storage.private_workspace(Path(parent), 'renderer-') as stage:
                    stage.mkdir(('tmp',))
                    with stage.create(('verified',)) as output:
                        output.write(b'keep verified file')
                    verified = stage.path / 'verified'
                    scratch = stage.path / 'tmp' / 'identity-pytest'
                    setup.run_setup_command(
                        [sys.executable, '-I', '-S', '-B', '-c',
                         'from pathlib import Path; import sys; Path(sys.argv[1]).mkdir(mode=0o700)',
                         str(scratch)],
                        cwd=str(stage.path), env={'SystemRoot': str(setup._system_root())}, timeout=15)
            self.assertTrue(scratch.is_dir())
            self.assertEqual(verified.read_bytes(), b'keep verified file')
            self.assertTrue(any(str(scratch) in note for note in raised.exception.__notes__))

    def test_tool_created_file_gets_protected_dacl_without_changing_identity(self):
        parts = ('generated.lib',)
        with tempfile.TemporaryDirectory(prefix='ComposerLint linker ACL ') as tmp:
            with storage.private_workspace(Path(tmp), 'probe-', members={parts: False}) as stage:
                path = stage.path.joinpath(*parts)
                self.assertNotIn(parts, stage.audit())
                setup.run_setup_command(
                    [sys.executable, '-I', '-S', '-B', '-c',
                     'from pathlib import Path; Path("generated.lib").write_bytes(b"linker output")'],
                    cwd=str(stage.path), env={'SystemRoot': str(setup._system_root())}, timeout=15)
                identity = stage.audit()[parts]
                member = stage._open(stage.handle, path.name, directory=False)
                try:
                    with self.assertRaisesRegex(PermissionError, 'private Windows DACL'):
                        stage.security.require_private(member, stage.sid)
                finally:
                    stage.disk.close(member)
                stage.protect_generated_file(parts, identity)
                member = stage._open(stage.handle, path.name, directory=False)
                try:
                    stage.security.require_private(member, stage.sid)
                    self.assertEqual(stage.disk.identity(member), identity)
                finally:
                    stage.disk.close(member)
                self.assertEqual(path.read_bytes(), b'linker output')
            self.assertFalse(path.parent.exists())

    def test_strict_probe_workspace_preserves_unknown_same_owner_object(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint probe inventory ') as tmp:
            with self.assertRaisesRegex(PermissionError, 'Unexpected Windows workspace member') as raised:
                with storage.private_workspace(Path(tmp), 'probe-', members={('planned',): False}) as stage:
                    for name in ('planned', 'unexpected'):
                        with stage.create((name,)) as output:
                            output.write(name.encode())
                    path = stage.path
            self.assertIn(str(path / 'unexpected'), str(raised.exception))
            self.assertTrue(any('workspace is incomplete' in note for note in raised.exception.__notes__))
            for name in ('planned', 'unexpected'):
                self.assertEqual((path / name).read_bytes(), name.encode())

    def test_probe_outputs_keep_protected_owner_and_capture_both_native_streams(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint ABI ') as tmp:
            with storage.private_workspace(Path(tmp), 'probe-') as stage:
                with stage.create(('output.obj',)) as output:
                    output.write(b'placeholder')
                result = setup.run_setup_command(
                    [sys.executable, '-I', '-S', '-B', '-c',
                     'import sys; from pathlib import Path; '
                     'Path("output.obj").write_bytes(b"compiler output"); '
                     'print("compiler diagnostic"); print("tool detail", file=sys.stderr); sys.exit(2)'],
                    cwd=str(stage.path), env={'SystemRoot': str(setup._system_root())}, timeout=15,
                    capture_result=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout.strip(), b'compiler diagnostic')
                self.assertEqual(result.stderr.strip(), b'tool detail')
                member = stage._open(stage.handle, 'output.obj', directory=False)
                try:
                    stage.security.require_private(member, stage.sid)
                finally:
                    stage.disk.close(member)
                self.assertEqual((stage.path / 'output.obj').read_bytes(), b'compiler output')
                path = stage.path
            self.assertFalse(path.exists())

    def test_probe_cleanup_preserves_file_created_with_a_different_default_owner(self):
        security = storage._SecurityAPI()
        with security._token() as token:
            default_owner = security._default_owner(token)
        if default_owner == security.user_sid():
            self.skipTest('the effective token already defaults to the user SID')
        with tempfile.TemporaryDirectory(prefix='ComposerLint ABI owner ') as tmp:
            with self.assertRaisesRegex(PermissionError, f'owner {default_owner},') as raised:
                with storage.private_workspace(Path(tmp), 'probe-') as stage:
                    with stage.create(('verified',)) as output:
                        output.write(b'keep verified output')
                    wrong_owner = stage.path / 'default-owner.tmp'
                    wrong_owner.write_bytes(b'keep unverified output')
                    verified = stage.path / 'verified'
            self.assertEqual(wrong_owner.read_bytes(), b'keep unverified output')
            self.assertEqual(verified.read_bytes(), b'keep verified output')
            self.assertTrue(any(str(wrong_owner) in note for note in raised.exception.__notes__))
            self.assertTrue(any('workspace is incomplete' in note for note in raised.exception.__notes__))

    def test_renderer_pins_hold_files_against_replacement_until_release(self):
        with tempfile.TemporaryDirectory(prefix='composerlint-renderer-pins-') as temporary:
            parent = Path(temporary)
            with storage.private_workspace(parent, 'install-') as stage:
                parts = ('renderer', 'native.pyd')
                with stage.create(parts) as stream:
                    stream.write(b'original')
                path = stage.path.joinpath(*parts)
                replacement = stage.path / 'replacement.pyd'
                with stage.create((replacement.name,)) as stream:
                    stream.write(b'replacement')

                with storage.pinned_install_files(stage.path) as (read, validate):
                    self.assertEqual(read(parts, limit=1024), b'original')
                    validate()
                    self.assertEqual(path.read_bytes(), b'original')
                    with self.assertRaises(OSError):
                        path.write_bytes(b'overwrite')
                    with self.assertRaises(OSError):
                        replacement.replace(path)

                replacement.replace(path)
                self.assertEqual(path.read_bytes(), b'replacement')

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

    def test_receipt_rename_works_with_a_separate_held_read_scope(self):
        with tempfile.TemporaryDirectory(prefix='ComposerLint receipt ') as tmp:
            destination = Path(tmp) / 'ComposerLint' / 'setup'
            with storage.transaction(destination) as stage:
                # Full setup reads its renderer manifest through a separate
                # held scope while the installation generation is still open.
                with storage.pinned_install_files(stage.path) as (read, validate):
                    with stage.create(('renderer.json',)) as stream:
                        stream.write(b'fixture manifest')
                    self.assertEqual(read(('renderer.json',), limit=32), b'fixture manifest')
                    validate()
                stage.commit({'fixture': True})
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
