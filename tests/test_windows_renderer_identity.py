"""Exercise the isolated child probe with real file identities and stubbed Windows APIs."""

import ctypes
from importlib import metadata
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from composerlint.windows_renderer import BUILD, IDENTITY_PROBE


@pytest.fixture
def scratch_file():
    if sys.platform == 'win32':
        from composerlint.runtime_paths import temporary_parent
        from composerlint.windows_install_storage import private_workspace

        with private_workspace(temporary_parent(), 'identity-') as stage:
            def file(relative):
                parts = Path(relative).parts
                with stage.create(parts):
                    pass
                return stage.path.joinpath(*parts)

            yield file
        return

    with tempfile.TemporaryDirectory(prefix='composerlint-renderer-identity-') as directory:
        def file(relative):
            path = Path(directory) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path

        yield file


@pytest.fixture
def probe(scratch_file, monkeypatch):
    file = scratch_file

    base = file('python/python.exe')
    executable = file('runtime/Scripts/python.exe')
    prefix = executable.parents[1]
    packages = prefix / 'Lib' / 'site-packages'
    package = file('runtime/Lib/site-packages/verovio/__init__.py')
    native = file('runtime/Lib/site-packages/verovio/_verovio.pyd')
    sibling = file('renderer/Lib/site-packages/verovio/_verovio.pyd')
    sibling_exe = file('renderer/Scripts/python.exe')
    sibling_package = file('renderer/Lib/site-packages/verovio/__init__.py')
    stable_dll = file('python/python3.dll')
    versioned_dll = file('python/python312.dll')
    expected_mapping = {path.name: path for path in (native, stable_dll, versioned_dll)}
    actual_mapping = expected_mapping.copy()
    handles = Mock(side_effect=lambda name: name if name in actual_mapping else None)

    def mapped_path(handle, buffer, size):
        buffer.value = str(actual_mapping[handle])
        return len(buffer.value)

    paths = Mock(side_effect=mapped_path)
    kernel = SimpleNamespace(GetModuleHandleW=handles, GetModuleFileNameW=paths)
    toolkit = Mock(return_value='6.3.0[undefined]')
    extension = SimpleNamespace(__file__=str(native))
    module = SimpleNamespace(__file__=str(package), _verovio=extension,
                             toolkit=lambda: SimpleNamespace(getVersion=toolkit))
    distribution = SimpleNamespace(version='6.3.0', locate_file=lambda _: packages)
    lookup = Mock(return_value=distribution)

    def run(expected='6.3.0', **python):
        identity = dict(version_info=(3, 12, 13), version='3.12.13 fixture',
                        implementation=SimpleNamespace(name='cpython'), executable=str(executable),
                        _base_executable=str(base), prefix=str(prefix))
        with monkeypatch.context() as patch:
            for key, value in (identity | python).items():
                patch.setattr(sys, key, value)
            patch.setattr(sys, 'argv', ['-c', expected, str(executable), str(base)])
            patch.setitem(sys.modules, 'verovio', module)
            patch.setattr(metadata, 'distribution', lookup)
            patch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
            patch.setattr(ctypes, 'get_last_error', lambda: 126, raising=False)
            exec(IDENTITY_PROBE, {})

    return SimpleNamespace(
        run=run, distribution=distribution, lookup=lookup, module=module,
        extension=extension, toolkit=toolkit, paths=paths, actual_mapping=actual_mapping,
        expected_mapping=expected_mapping, sibling=sibling, sibling_exe=sibling_exe,
        sibling_package=sibling_package, file=file,
    )


@pytest.mark.parametrize('build', ['6.3.0', BUILD])
@pytest.mark.parametrize('toolkit_version', ['6.3.0[undefined]', '6.3.0', '6.3.0-425dd7b'])
def test_accepts_reviewed_wheel_versions_and_upstream_toolkit_formats(probe, capsys, build, toolkit_version):
    probe.distribution.version = build
    probe.toolkit.return_value = toolkit_version
    probe.run(build)
    output = capsys.readouterr().out
    assert f'Verified Verovio distribution {build}, toolkit {toolkit_version}' in output
    for path in probe.expected_mapping.values():
        assert f'Loaded {build}: {path}' in output


@pytest.mark.parametrize(('attribute', 'value', 'label', 'expected', 'actual'), [
    ('implementation', SimpleNamespace(name='pypy'), 'Python implementation', 'cpython', 'pypy'),
    ('version_info', (3, 12, 12), 'CPython version', '3.12.13', '3.12.12'),
    ('executable', 'sibling_exe', 'Virtual environment executable', 'runtime', 'renderer'),
    ('prefix', 'sibling_prefix', 'Virtual environment prefix', 'runtime', 'renderer'),
    ('_base_executable', 'sibling_exe', 'Base Python executable', 'python', 'renderer'),
])
def test_rejects_wrong_interpreter_or_environment(probe, attribute, value, label, expected, actual):
    if value == 'sibling_exe':
        value = str(probe.sibling_exe)
    elif value == 'sibling_prefix':
        value = str(probe.sibling_exe.parents[1])
    overrides = {attribute: value}
    if attribute == 'version_info':
        overrides['version'] = '3.12.12 fixture'
    with pytest.raises(RuntimeError, match=label) as caught:
        probe.run(**overrides)
    assert expected in str(caught.value)
    assert actual in str(caught.value)


@pytest.mark.parametrize(('expected', 'installed'), [('6.3.0', BUILD), (BUILD, '6.3.0')])
def test_rejects_official_and_patched_metadata_swaps(probe, expected, installed):
    probe.distribution.version = installed
    with pytest.raises(RuntimeError, match='Verovio distribution version') as caught:
        probe.run(expected)
    assert f'expected {expected!r}, got {installed!r}' in str(caught.value)


def test_reports_missing_metadata(probe):
    probe.lookup.side_effect = metadata.PackageNotFoundError('verovio')
    with pytest.raises(RuntimeError, match=r'Verovio distribution metadata missing: expected .*6\.3\.0.*site-packages'):
        probe.run()


def test_rejects_metadata_from_sibling_environment(probe):
    probe.distribution.locate_file = lambda _: probe.sibling.parents[1]
    with pytest.raises(RuntimeError, match=r'Verovio distribution location: .*runtime.*renderer'):
        probe.run()


def test_reports_failed_import(probe):
    del probe.module._verovio
    with pytest.raises(RuntimeError, match=r'Verovio import from .*site-packages.*failed'):
        probe.run()


def test_rejects_python_package_import_from_sibling_environment(probe):
    probe.module.__file__ = str(probe.sibling_package)
    with pytest.raises(RuntimeError, match=r'Verovio package import: .*runtime.*renderer'):
        probe.run()


@pytest.mark.parametrize('filename', ['_verovio.dll', 'unrelated.pyd'])
def test_rejects_wrong_native_extension_filename(probe, filename):
    probe.extension.__file__ = str(Path(probe.extension.__file__).with_name(filename))
    with pytest.raises(RuntimeError, match='Verovio native extension filename') as caught:
        probe.run()
    assert '_verovio.pyd' in str(caught.value)
    assert filename in str(caught.value)


def test_rejects_native_extension_import_from_sibling_environment(probe):
    probe.extension.__file__ = str(probe.sibling)
    with pytest.raises(RuntimeError, match=r'Verovio native extension location: .*runtime.*renderer'):
        probe.run()


def test_reports_unavailable_identity_path(probe):
    probe.extension.__file__ = str(probe.sibling.parent / 'missing' / probe.sibling.name)
    with pytest.raises(RuntimeError, match=r'Verovio native extension location: cannot compare .*_verovio\.pyd'):
        probe.run()


@pytest.mark.parametrize('name', ['_verovio.pyd', 'python3.dll', 'python312.dll'])
def test_rejects_wrong_mapped_file_for_each_native_module(probe, name):
    wrong = probe.file('foreign/' + name)
    probe.actual_mapping[name] = wrong
    with pytest.raises(RuntimeError, match=f'Loaded {name}') as caught:
        probe.run()
    assert str(probe.expected_mapping[name]) in str(caught.value).replace('\\\\', '\\')
    assert str(wrong) in str(caught.value).replace('\\\\', '\\')


def test_reports_missing_module_handle(probe):
    del probe.actual_mapping['python3.dll']
    with pytest.raises(RuntimeError, match=r'Loaded python3\.dll: no module handle .*Windows error 126'):
        probe.run()


@pytest.mark.parametrize('length', [0, 32768])
def test_reports_unavailable_or_truncated_mapped_path(probe, length):
    probe.paths.side_effect = None
    probe.paths.return_value = length
    with pytest.raises(RuntimeError, match=rf'Loaded _verovio\.pyd: cannot read mapped path .*length {length}'):
        probe.run()


@pytest.mark.parametrize('toolkit_version', [
    '6.3.1', '6.3.00[undefined]', '6.3.0[unknown]', '6.3.0-dev-425dd7b', BUILD,
])
def test_rejects_unexpected_native_toolkit_versions(probe, toolkit_version):
    probe.toolkit.return_value = toolkit_version
    with pytest.raises(RuntimeError, match='Verovio toolkit version') as caught:
        probe.run()
    assert 'expected release 6.3.0' in str(caught.value)
    assert repr(toolkit_version) in str(caught.value)
