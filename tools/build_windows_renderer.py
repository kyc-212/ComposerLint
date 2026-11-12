"""Build the exact Windows Verovio candidate from already acquired inputs."""

import argparse
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
PACKAGE = '_composerlint_windows_build'
spec = ModuleSpec(PACKAGE, loader=None, is_package=True)
spec.submodule_search_locations = [str(ROOT / 'src' / 'composerlint')]
sys.modules[PACKAGE] = module_from_spec(spec)
recipe = import_module(f'{PACKAGE}.windows_renderer')
paths = import_module(f'{PACKAGE}.runtime_paths')
storage = import_module(f'{PACKAGE}.windows_install_storage')
private = import_module(f'{PACKAGE}.windows_private')
setup = import_module(f'{PACKAGE}.windows_setup')
workers = import_module(f'{PACKAGE}.windows_workers')
REQUIREMENTS = ROOT / 'requirements-renderer-build.txt'
PROBE = b'''#include <windows.h>
#include <concepts>
#include <filesystem>
#include <span>
#include <cstdio>
#ifndef _M_X64
#error Native x64 MSVC is required
#endif
static_assert(_MSVC_LANG >= 202002L);
template<std::integral T> T first(std::span<T> values) { return values.front(); }
int main() {
    int values[] = {0};
    std::printf("MSVC _MSC_FULL_VER=%d\\n", _MSC_FULL_VER);
    return first<int>(values) + (!GetModuleHandleW(nullptr) ||
           std::filesystem::current_path().empty());
}
'''


PYTHON_ABI_PROBE = b"""#define Py_LIMITED_API 0x030A0000
#include <Python.h>
#include <cstdio>
#include <cstring>
static_assert(PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION == 12);
int main() {
    const char* version = Py_GetVersion();
    std::printf("CPython stable ABI loaded: %s\n", version);
    return std::strncmp(version, "3.12.", 5) != 0;
}
"""


def toolchain():
    if sys.platform != 'win32':
        raise RuntimeError('Native Windows x64 is required for the renderer build.')
    keys = ('PATH', 'INCLUDE', 'LIB', 'LIBPATH', 'WindowsSDKVersion', 'VCToolsVersion')
    env = {key: os.environ[key] for key in keys if os.environ.get(key)}
    if not all(key in env for key in ('PATH', 'INCLUDE', 'LIB', 'WindowsSDKVersion', 'VCToolsVersion')):
        raise RuntimeError('Use the installed x64 MSVC developer environment with a Windows SDK.')
    # Search the explicit developer PATH, excluding relative entries and cwd.
    entries = [entry for entry in env['PATH'].split(os.pathsep) if Path(entry).is_absolute()]
    tools = {}
    for name in ('cl', 'link', 'rc', 'mt'):
        tool = next((Path(entry) / (name + '.exe') for entry in entries
                     if (Path(entry) / (name + '.exe')).is_file()), None)
        if tool is None:
            raise RuntimeError(f'Required compiler or Windows SDK tool is missing: {name}.exe')
        tools[name] = tool
    env['PATH'] = os.pathsep.join(entries)
    return env, tools


def build(args):
    env, tools = toolchain()
    print('Windows build tools:', *(f'{key}={value}' for key, value in tools.items()), sep='\n')
    with storage.private_workspace(paths.temporary_parent(), 'composerlint-verovio-') as stage:
        stage.mkdir(('tmp',))
        temp = stage.path / 'tmp'
        env.update(SystemRoot=str(setup._system_root()), TEMP=str(temp), TMP=str(temp),
                   LOCALAPPDATA=str(temp), USERPROFILE=str(temp), UV_CACHE_DIR=str(temp / 'uv'),
                   PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', UV_PYTHON_DOWNLOADS='never')

        def run(executable, *arguments, timeout=120):
            return workers.run_setup_command([str(executable), *(str(arg) for arg in arguments)],
                                     cwd=str(stage.path), env=env, timeout=timeout)

        provenance = {'vctools': env['VCToolsVersion'],
                      'windows_sdk': env['WindowsSDKVersion'].rstrip('\\'),
                      'tool_sha256': {name: recipe.sha(private.read_stable(path, limit=64 * 1024**2)[0])
                                      for name, path in tools.items()}}
        print(json.dumps(provenance, sort_keys=True), flush=True)
        setup._write(stage, ('probe.cpp',), PROBE)
        run(tools['cl'], '/nologo', '/std:c++20', '/EHsc', '/c', 'probe.cpp')
        run(tools['link'], '/nologo', '/machine:x64', '/subsystem:console', '/out:probe.exe',
            'probe.obj', 'kernel32.lib')
        compiler = run(stage.path / 'probe.exe').decode('ascii').strip()
        provenance['compiler'] = compiler
        print(f'Native x64 C++20 compile/link/run passed. {compiler}', flush=True)

        python_root = Path(sys._base_executable).parent
        if args.probe_only:
            include = python_root / 'include'
            library = python_root / 'libs' / 'python3.lib'
            for path in (include / 'Python.h', library):
                print(f'{path} SHA-256 {recipe.sha(private.read_stable(path, limit=16 * 1024**2)[0])}', flush=True)
            setup._write(stage, ('python_abi.cpp',), PYTHON_ABI_PROBE)
            run(tools['cl'], '/nologo', '/std:c++20', '/EHsc', '/c', f'/I{include}', 'python_abi.cpp')
            run(tools['link'], '/nologo', '/machine:x64', '/subsystem:console', '/out:python_abi.exe',
                'python_abi.obj', library)
            env['PATH'] = str(python_root) + os.pathsep + env['PATH']
            print(run(stage.path / 'python_abi.exe').decode('ascii').strip(), flush=True)
            return

        if sys.version_info[:3] != (3, 12, 13) or not all(
                (python_root / item).is_file() for item in ('include/Python.h', 'libs/python3.lib')):
            raise RuntimeError('The private CPython 3.12.13 headers and stable ABI import library are required.')
        source = recipe.checked_bytes(args.inputs / recipe.SOURCE_ARCHIVE, recipe.SOURCE_SHA256, 32 * 1024**2)
        official = recipe.checked_bytes(args.inputs / recipe.OFFICIAL_WHEEL, recipe.OFFICIAL_SHA256, 32 * 1024**2)
        patch = recipe.checked_bytes(ROOT / 'patches' / recipe.PATCH, recipe.PATCH_SHA256, 65536)
        setup.unpack(stage, source, 'renderer', patch=patch)
        stage.mkdir(('dist',))

        def uv(*arguments):
            return run(args.uv, '--offline', '--no-config', '--no-progress', '--no-python-downloads', *arguments)

        tool_env = stage.path / 'build-tools'
        uv('venv', '--python', sys._base_executable, tool_env)
        tool_python = tool_env / 'Scripts/python.exe'
        uv('pip', 'sync', '--python', tool_python, '--require-hashes', '--no-index',
           '--no-build', '--only-binary', ':all:', '--link-mode', 'copy',
           '--find-links', args.wheelhouse, REQUIREMENTS)
        env.update(VEROVIO_BUILD_VERSION=recipe.BUILD, CMAKE_GENERATOR='Ninja',
                   PATH=str(tool_python.parent) + os.pathsep + env['PATH'],
                   CMAKE_BUILD_PARALLEL_LEVEL='2')
        run(tool_python, '-I', '-B', '-m', 'build', '--no-isolation', '--wheel',
            '-Ccmake.define.CMAKE_CXX_COMPILER=' + str(tools['cl']),
            '-Ccmake.define.CMAKE_C_COMPILER=' + str(tools['cl']),
            '-Cbuild-dir=' + str(stage.path / 'build'), '--outdir', stage.path / 'dist',
            stage.path / 'verovio-6.3.0', timeout=1500)
        dist = stage.path / 'dist'
        if {path.name for path in dist.iterdir()} != {recipe.PATCHED_WHEEL}:
            raise RuntimeError('Expected the exact patched cp310-abi3 Windows x64 wheel.')
        candidate, stamp = private.read_stable(dist / recipe.PATCHED_WHEEL, limit=32 * 1024**2)
        if stamp.links != 1:
            raise RuntimeError('Patched renderer wheel must have exactly one NTFS link.')
        provenance.update(source_sha256=recipe.SOURCE_SHA256, patch_sha256=recipe.PATCH_SHA256,
                          postimage_sha256=recipe.POSTIMAGE, wheel_sha256=recipe.sha(candidate))
        with private.private_directory(args.output, create=True) as output:
            for name, data in ((recipe.OFFICIAL_WHEEL, official), (recipe.SOURCE_ARCHIVE, source),
                               (recipe.PATCH, patch), (recipe.PATCHED_WHEEL, candidate)):
                output.write_new(name, data)
            output.write_new('build-provenance.json', (json.dumps(provenance, indent=2) + '\n').encode())
        print('Local candidate SHA-256:', provenance['wheel_sha256'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-only', action='store_true')
    for option in ('inputs', 'wheelhouse', 'uv', 'output'):
        parser.add_argument('--' + option, type=Path)
    args = parser.parse_args()
    if not args.probe_only and not all(getattr(args, name) and getattr(args, name).is_absolute()
                                       for name in ('inputs', 'wheelhouse', 'uv', 'output')):
        parser.error('Build requires absolute --inputs, --wheelhouse, --uv and a fresh --output directory.')
    build(args)
