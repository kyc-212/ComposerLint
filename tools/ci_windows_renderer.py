"""One disposable native build and comparison of the two Windows renderers."""

import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_windows_renderer import ROOT, paths, private, recipe, run_probe_command, setup, storage, toolchain

ASSET_MANIFEST = ROOT / 'tools' / 'windows-renderer-assets.json'
BUILD_REQUIREMENTS = ROOT / 'requirements-renderer-build.txt'
CONTROLS = ROOT / 'tests' / 'test_renderer_packaging_equivalence.py'

LOAD = r"""
import ctypes
from importlib.metadata import version
from pathlib import Path
import sys
import verovio
from verovio import _verovio

expected, base = sys.argv[1:]
base = Path(base)
root = Path(sys.prefix) / 'Lib' / 'site-packages' / 'verovio'
native = Path(_verovio.__file__)
if (sys.version_info[:3] != (3, 12, 13) or not Path(sys._base_executable).samefile(base)
        or not native.parent.samefile(root) or native.suffix.lower() != '.pyd'
        or version('verovio') != expected or verovio.toolkit().getVersion().split('-')[0] != '6.3.0'):
    raise RuntimeError(f'Unexpected renderer identity: {sys.version}, {native}, {version("verovio")}')
kernel = ctypes.WinDLL('kernel32.dll', winmode=0x0800, use_last_error=True)
kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype = ctypes.c_void_p
kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint]
kernel.GetModuleFileNameW.restype = ctypes.c_uint
for path in (native, base.with_name('python3.dll'), base.with_name('python312.dll')):
    handle = kernel.GetModuleHandleW(path.name)
    result = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(handle, result, len(result)) if handle else 0
    if not 0 < length < len(result) or not Path(result.value).samefile(path):
        raise RuntimeError(f'Expected native module is not mapped from {path}')
    print(f'Loaded {expected}: {result.value}')
"""

PYTEST = """
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
raise SystemExit(pytest.main(sys.argv[2:]))
"""


def reviewed_assets():
    python, uv, manifest, _ = setup._asset_plan(
        (ROOT / 'tools' / 'windows-setup-assets.json').read_bytes(), (ROOT / 'uv.lock').read_bytes())
    plan = json.loads(ASSET_MANIFEST.read_bytes())
    inputs = tuple(setup.Asset(**entry) for entry in plan['build'])
    wheels = {asset.name: asset for asset in manifest}
    names = plan['controls']
    if len(names) != len(set(names)) or not set(names) <= wheels.keys():
        raise ValueError('A renderer control wheel is missing or duplicated in the reviewed Windows manifest.')
    controls = tuple(wheels[name] for name in names)
    pins = {recipe.SOURCE_ARCHIVE: recipe.SOURCE_SHA256, recipe.OFFICIAL_WHEEL: recipe.OFFICIAL_SHA256}
    build_requirements = BUILD_REQUIREMENTS.read_text(encoding='utf-8')
    for asset in inputs:
        if asset.filename in pins:
            if asset.sha256 != pins.pop(asset.filename):
                raise ValueError(f'Unexpected renderer input pin: {asset.filename}')
        elif (not asset.filename.endswith(('-win_amd64.whl', '-any.whl'))
              or f'{asset.name}=={asset.version} ' not in build_requirements
              or f'--hash=sha256:{asset.sha256}' not in build_requirements):
            raise ValueError(f'Unexpected renderer build wheel: {asset.filename}')
    if pins or len(inputs) != 10 or len({a.filename for a in inputs}) != len(inputs):
        raise ValueError('Unexpected Windows renderer build inventory.')
    return python, uv, inputs, controls


def requirements(assets):
    return ''.join(f'{asset.name}=={asset.version} --hash=sha256:{asset.sha256}\n' for asset in assets).encode()


def checkpoint(stage, python_pin, uv_pin, inputs, controls):
    for name in ('tmp', 'work', 'inputs', 'wheelhouse', 'cache'):
        stage.mkdir((name,))
    expected_top = {'tmp', 'work', 'inputs', 'wheelhouse', 'cache', 'python', 'uv', 'lock',
                    'runtime', 'renderer', 'renderer-artifacts', 'renderer.json'}
    root = stage.path
    cwd, temp, wheelhouse, bundle = (root / name for name in ('work', 'tmp', 'wheelhouse', 'renderer-artifacts'))
    source, official = recipe.SOURCE_ARCHIVE, recipe.OFFICIAL_WHEEL
    fetched = {}

    def fetch(asset, directory):
        previous = fetched.get(asset.filename)
        if previous is not None:
            if previous != (asset.size, asset.sha256, asset.url, directory):
                raise ValueError(f'Conflicting artifact identity: {asset.filename}')
            return
        setup._write(stage, (directory, asset.filename), setup.acquire(asset))
        fetched[asset.filename] = (asset.size, asset.sha256, asset.url, directory)
        print(f'Verified {asset.filename} bytes={asset.size} SHA-256={asset.sha256}', flush=True)

    for asset in (python_pin, uv_pin):
        fetch(asset, 'inputs')
    for asset in (*inputs, *controls):
        fetch(asset, 'inputs' if asset.filename in (source, official) else 'wheelhouse')
    for kind, asset in (('python', python_pin), ('uv', uv_pin)):
        with stage.held_file(('inputs', asset.filename)) as verified:
            setup.unpack(stage, recipe.checked_bytes(verified, asset.sha256, asset.size), kind)

    developer, _ = toolchain()
    system = setup._system_root()
    env = {key: os.environ[key] for key in ('VCToolsInstallDir', 'WindowsSdkDir', 'USERPROFILE',
            'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'ProgramData', 'PUBLIC') if key in os.environ}
    env.update(developer)
    env.update(SystemRoot=str(system), TEMP=str(temp), TMP=str(temp), UV_CACHE_DIR=str(root / 'cache'),
               MPLCONFIGDIR=str(cwd), COMPOSERLINT_TEMP_DIR=str(root.parent),
               UV_PYTHON_DOWNLOADS='never', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    python = root / 'python' / 'python.exe'
    uv = root / 'uv' / 'uv.exe'

    def run(executable, *args, timeout=120):
        return run_probe_command(executable.name, executable, *args, cwd=cwd, env=env, timeout=timeout)

    def uv_run(*args):
        return run(uv, '--offline', '--no-config', '--no-progress', '--no-python-downloads', *args)

    if uv_run('--version').split()[:2] != ['uv', '0.12.3']:
        raise ValueError('Unexpected private uv version.')
    build_reqs = [a for a in inputs if a.filename not in (source, official)]
    build_reqs.extend(a for a in controls if a.name == 'colorama')
    setup._write(stage, ('work', 'build.txt'), requirements(build_reqs))
    setup._write(stage, ('work', 'controls.txt'), requirements(controls))
    print(run(python, '-I', '-B', '-X', 'utf8', ROOT / 'tools' / 'build_windows_renderer.py',
              '--inputs', root / 'inputs', '--wheelhouse', wheelhouse, '--uv', uv, '--output', bundle,
              '--build-requirements', cwd / 'build.txt', timeout=1800), flush=True)
    expected_artifacts = {source, official, recipe.PATCH, recipe.PATCHED_WHEEL, 'build-provenance.json'}
    if {p.name for p in bundle.iterdir()} != expected_artifacts:
        raise ValueError('Unexpected renderer build artifacts.')
    provenance = json.loads(setup._read_source(bundle / 'build-provenance.json', 16384))
    if any(provenance.get(key) != value for key, value in (
            ('source_sha256', recipe.SOURCE_SHA256), ('patch_sha256', recipe.PATCH_SHA256),
            ('preimage_sha256', recipe.PREIMAGE), ('postimage_sha256', recipe.POSTIMAGE))):
        raise ValueError('Renderer source or patch provenance differs from the reviewed pins.')
    for name, expected in ((source, recipe.SOURCE_SHA256), (official, recipe.OFFICIAL_SHA256),
                           (recipe.PATCH, recipe.PATCH_SHA256), (recipe.PATCHED_WHEEL, provenance['wheel_sha256'])):
        recipe.checked_bytes(bundle / name, expected, 32 * 1024**2)

    pip_options = ('--no-index', '--no-build', '--link-mode', 'copy')
    runtime = root / 'runtime' / 'Scripts' / 'python.exe'
    renderer = root / 'renderer' / 'Scripts' / 'python.exe'
    for executable, wheel, version in ((runtime, official, '6.3.0'),
                                       (renderer, recipe.PATCHED_WHEEL, recipe.BUILD)):
        uv_run('venv', '--python', python, executable.parents[1])
        if executable == runtime:
            uv_run('pip', 'sync', '--python', executable, *pip_options, '--require-hashes',
                   '--find-links', wheelhouse, cwd / 'controls.txt')
        uv_run('pip', 'install', '--python', executable, *pip_options, '--no-deps', bundle / wheel)
        uv_run('pip', 'check', '--python', executable)
        print(run(executable, '-I', '-B', '-c', LOAD, version, python), flush=True)

    installed = [p for p in (root / 'renderer' / 'Lib' / 'site-packages').rglob('*') if p.is_file()]
    retained = [renderer, renderer.parents[1] / 'pyvenv.cfg', python, *python.parent.glob('*.dll'),
                *(bundle / name for name in expected_artifacts), *installed]
    hashes = {}
    for path in sorted(retained):
        parts = path.relative_to(root)
        with stage.held_file(parts.parts) as checked, checked.open('rb') as stream:
            hashes[parts.as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    config = root / 'renderer.json'
    setup._write(stage, (config.name,), json.dumps(dict(package_version=recipe.BUILD,
        python=renderer.relative_to(root).as_posix(), files_sha256=hashes), sort_keys=True).encode())
    env.update(COMPOSERLINT_RENDERER_CONFIG=str(config), COMPOSERLINT_RENDERER_EQUIVALENCE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', COMPOSERLINT_TEMP_DIR=str(temp))
    report = cwd / 'renderer-controls.xml'
    print(run(runtime, '-I', '-B', '-c', PYTEST, ROOT, '-v', '-x', '-p', 'no:cacheprovider',
              '-k', 'not test_windows_render_holds_pins_through_worker_exit',
              '--basetemp=' + str(temp / 'pytest'), '--junitxml=' + str(report), CONTROLS, timeout=240), flush=True)
    print('Renderer controls:', setup.control_counts(ET.parse(report).getroot()), flush=True)
    if {p.name for p in root.iterdir()} != expected_top:
        raise ValueError('Unexpected top-level Windows renderer workspace member.')


def main():
    assets = reviewed_assets()
    with storage.private_workspace(paths.temporary_parent(), 'cl-vr-') as stage:
        stage.mkdir(('lock',))
        with (private.private_directory(stage.path / 'lock', tree_root=True) as lock,
              lock.exclusive_lock()):
            checkpoint(stage, *assets)
    print('Renderer checkpoint private workspace cleanup verified.', flush=True)


if __name__ == '__main__':
    main()
