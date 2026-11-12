"""Build the exact Windows Verovio candidate from already acquired inputs."""

import argparse
from contextlib import contextmanager
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import json
import os
from pathlib import Path
from subprocess import list2cmdline
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
preflight = import_module(f'{PACKAGE}.windows_preflight')
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


PYTHON_ABI_PROBE = br"""#define Py_LIMITED_API 0x030A0000
#include <Python.h>
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <cwchar>
#ifndef _M_X64
#error Native x64 MSVC is required
#endif
static_assert(PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION == 12);
static_assert(_MSVC_LANG >= 202002L);

#ifdef ABI_EXTENSION
static PyObject* version(PyObject*, PyObject*) {
    return PyUnicode_FromString(Py_GetVersion());
}
static PyMethodDef methods[] = {
    {"version", version, METH_NOARGS, nullptr},
    {nullptr, nullptr, 0, nullptr}
};
static PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_composerlint_abi_probe", nullptr, -1, methods
};
PyMODINIT_FUNC PyInit__composerlint_abi_probe(void) {
    return PyModule_Create(&module);
}
#else
static bool check_dll(const wchar_t* name, const wchar_t* expected) {
    wchar_t actual[32768];
    auto module = GetModuleHandleW(name);
    auto length = module ? GetModuleFileNameW(module, actual, ARRAYSIZE(actual)) : 0;
    if (!length || length == ARRAYSIZE(actual)) {
        std::fprintf(stderr, "Cannot identify loaded Python DLL (Windows error %lu)\n", GetLastError());
        return false;
    }
    if (CompareStringOrdinal(actual, -1, expected, -1, TRUE) != CSTR_EQUAL) {
        std::fwprintf(stderr, L"Unexpected loaded DLL: %ls (expected %ls)\n", actual, expected);
        return false;
    }
    return true;
}
int wmain(int argc, wchar_t** argv) {
    if (argc != 3 || !check_dll(L"python3.dll", argv[1]) || !check_dll(L"python312.dll", argv[2])) {
        return 1;
    }
    const char* version = Py_GetVersion();
    std::printf("CPython stable ABI loaded: %s\n", version);
    return std::strncmp(version, "3.12.", 5) != 0;
}
#endif
"""

ABI_MODULE = '_composerlint_abi_probe'
ABI_IMPORT_LIBRARY = (ABI_MODULE + '.lib',)
ABI_EXPORT_FILE = (ABI_MODULE + '.exp',)
PROBE_DIRECTORIES = (('work',), ('tmp',), ('abi-exe',))
PROBE_FILES = (
    ('probe.cpp',), ('probe.obj',), ('probe.exe',), ('python_abi.cpp',),
    ('abi-exe.obj',), ('abi-exe', 'python_abi.exe'), ('abi-exe', 'python3.dll'),
    ('abi-exe', 'python312.dll'), ('abi-extension.obj',), (ABI_MODULE + '.pyd',),
    ABI_IMPORT_LIBRARY, ABI_EXPORT_FILE,
)
PROFILE_VARIABLES = (
    'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'HOMESHARE',
    'ALLUSERSPROFILE', 'PROGRAMDATA', 'PUBLIC',
)
ABI_LOADER = """\
import ctypes
import importlib.util
import os
from pathlib import Path
import sys

module_path, executable = map(Path, sys.argv[1:])
if not Path(sys.executable).samefile(executable):
    raise RuntimeError(f'Unexpected CPython executable: {sys.executable}')

kernel = ctypes.WinDLL('kernel32.dll', winmode=0x0800, use_last_error=True)
get_handle = kernel.GetModuleHandleW
get_handle.argtypes = [ctypes.c_wchar_p]
get_handle.restype = ctypes.c_void_p
get_filename = kernel.GetModuleFileNameW
get_filename.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint]
get_filename.restype = ctypes.c_uint

with os.add_dll_directory(str(executable.parent)):
    spec = importlib.util.spec_from_file_location('_composerlint_abi_probe', module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f'No extension loader for {module_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = module.version()
    if version != sys.version:
        raise RuntimeError(f'ABI extension reports {version!r}; expected {sys.version!r}')
    for name in ('python3.dll', 'python312.dll'):
        handle = get_handle(name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        path = ctypes.create_unicode_buffer(32768)
        length = get_filename(handle, path, len(path))
        if not length or length == len(path):
            raise ctypes.WinError(ctypes.get_last_error())
        if not Path(path.value).samefile(executable.with_name(name)):
            raise RuntimeError(f'Unexpected loaded Python DLL: {path.value}')
        print(f'Loaded {name}: {path.value}')
    print(f'CPython stable ABI extension loaded: {version}')
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


def verify_probe_tools(tools, env):
    # Require the selected developer shell's installation and SDK, with no
    # fallback to another cl, link, rc or mt elsewhere on PATH.
    vc = Path(os.environ.get('VCToolsInstallDir', ''))
    sdk = Path(os.environ.get('WindowsSdkDir', ''))
    if not vc.is_absolute() or not sdk.is_absolute():
        raise RuntimeError('The developer shell did not identify its MSVC and Windows SDK roots.')
    vc_bin = vc / 'bin' / 'Hostx64' / 'x64'
    sdk_bin = sdk / 'bin' / env['WindowsSDKVersion'].rstrip('\\') / 'x64'
    for name, tool in tools.items():
        directory = sdk_bin if name in ('rc', 'mt') else vc_bin
        if tool != directory / (name + '.exe'):
            raise RuntimeError(f'{tool} is outside the selected developer toolchain: {directory}')
    return {name: recipe.sha(private.read_stable(tool, limit=64 * 1024**2)[0])
            for name, tool in tools.items()}


def run_probe_command(step, executable, *arguments, cwd, env):
    command = [str(executable), *(str(arg) for arg in arguments)]
    try:
        result = workers.run_setup_command(command, cwd=str(cwd), env=env,
                                           timeout=120, capture_result=True)
    except workers.SetupProcessCleanupUnconfirmed:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f'{step} could not complete: {error}\ncommand: {list2cmdline(command)}') from error
    if result.returncode:
        stdout, stderr = (data.decode('utf-8', 'backslashreplace').strip() or '(empty)'
                          for data in (result.stdout, result.stderr))
        raise RuntimeError(
            f'{step} failed ({executable}), exit status 0x{result.returncode & 0xffffffff:08X}\n'
            f'command: {list2cmdline(command)}\nstdout: {stdout}\nstderr: {stderr}')
    if result.stderr:
        sys.stderr.write(result.stderr.decode('utf-8', 'backslashreplace'))
        sys.stderr.flush()
    return result.stdout.decode('utf-8', 'backslashreplace').strip()


def probe_environment(developer, tools, system_root, temp, profile):
    # Preserve defined Windows profile paths, while excluding ambient compiler
    # switches (CL, _CL_, LINK, _LINK_), Python settings and unreviewed PATH entries.
    env = {name: profile[name] for name in PROFILE_VARIABLES if name in profile}
    env.update(INCLUDE=developer['INCLUDE'], LIB=developer['LIB'],
               SystemRoot=str(system_root), TEMP=str(temp), TMP=str(temp),
               PATH=os.pathsep.join(map(str, (tools['cl'].parent, tools['rc'].parent,
                                              system_root / 'System32'))))
    return env


@contextmanager
def probe_artifact(stage, parts):
    with stage.held_file(parts) as path:
        if not path.stat().st_size:
            raise RuntimeError(f'Probe output was not written: {path}')
        yield path


def run_probe_steps(stage, developer, tools, python, python_files):
    include = python.parent / 'include'
    library = python.parent / 'libs' / 'python3.lib'
    for parts in PROBE_DIRECTORIES:
        stage.mkdir(parts)

    # Keep both directories pinned until the job and its descendants have
    # exited. Temp files may exist during a command but must be gone at audit.
    with (stage.held_directory(('work',)) as cwd,
          stage.held_directory(('tmp',)) as temp,
          stage.held_directory(('abi-exe',))):
        env = probe_environment(developer, tools, setup._system_root(), temp, os.environ)

        def audit(when):
            try:
                return stage.audit()
            except OSError as error:
                error.add_note(f'Private workspace audit failed {when}')
                raise

        def run(step, executable, *arguments, generated=()):
            print(f'{step}...', flush=True)
            before = audit(f'before {step}')
            for parts in generated:
                if parts in before:
                    raise PermissionError(f'Probe output already exists before {step}: {stage.path.joinpath(*parts)}')
            result = run_probe_command(step, executable, *arguments, cwd=cwd, env=env)
            after = audit(f'after {step}')
            for parts in generated:
                if parts not in after:
                    raise RuntimeError(f'{step} did not produce {stage.path.joinpath(*parts)}')
                stage.protect_generated_file(parts, after[parts])
            return result

        def compile_source(source, output, *flags):
            setup._write(stage, (output,), b'')
            with probe_artifact(stage, (source,)) as source_path:
                run(f'Compile {output}', tools['cl'], '/nologo', '/std:c++20', '/EHsc',
                    '/utf-8', '/c', *flags, f'/Fo{stage.path / output}', source_path)

        def link_output(parts, obj, *flags, generated=()):
            setup._write(stage, parts, b'')
            with probe_artifact(stage, (obj,)) as object_path:
                run(f'Link {parts[-1]}', tools['link'], '/nologo', '/machine:x64',
                    '/incremental:no', '/manifest:no', '/debug:none',
                    f'/out:{stage.path.joinpath(*parts)}', object_path, *flags, generated=generated)

        setup._write(stage, ('probe.cpp',), PROBE)
        compile_source('probe.cpp', 'probe.obj')
        link_output(('probe.exe',), 'probe.obj', '/subsystem:console', 'kernel32.lib')
        with probe_artifact(stage, ('probe.exe',)) as executable:
            result = run('Execute native x64 C++20 probe', executable)
        print(f'Native x64 C++20 compile/link/run passed. {result}', flush=True)

        # Suppress PC/pyconfig.h's default search directive. The absolute
        # stable import library is the only source of Python symbols.
        abi_link = ('/nodefaultlib:python3.lib', library)
        setup._write(stage, ('python_abi.cpp',), PYTHON_ABI_PROBE)
        compile_source('python_abi.cpp', 'abi-exe.obj', '/I', include)
        link_output(('abi-exe', 'python_abi.exe'), 'abi-exe.obj',
                    '/subsystem:console', *abi_link, 'kernel32.lib')
        # Load the verified DLL copies alongside the executable, then check
        # which images Windows mapped and the exact runtime version.
        for name, data in python_files.items():
            setup._write(stage, ('abi-exe', name), data)
        with (probe_artifact(stage, ('abi-exe', 'python_abi.exe')) as executable,
              probe_artifact(stage, ('abi-exe', 'python3.dll')) as stable,
              probe_artifact(stage, ('abi-exe', 'python312.dll')) as versioned):
            result = run('Load CPython stable ABI in native x64 executable', executable, stable, versioned)
        if result != f'CPython stable ABI loaded: {sys.version}':
            raise RuntimeError(f'Native executable reported an unexpected CPython identity: {result!r}')
        print(result, flush=True)

        compile_source('python_abi.cpp', 'abi-extension.obj', '/I', include, '/DABI_EXTENSION')
        extension = (ABI_MODULE + '.pyd',)
        # MSVC derives the .exp sibling from /IMPLIB. Both paths must be
        # absent at launch so LINK can generate its own import/export files.
        import_library = stage.path.joinpath(*ABI_IMPORT_LIBRARY)
        link_output(extension, 'abi-extension.obj', '/dll',
                    f'/implib:{import_library}', *abi_link, generated=(ABI_IMPORT_LIBRARY, ABI_EXPORT_FILE))
        with probe_artifact(stage, extension) as module:
            result = run('Import native x64 CPython stable ABI extension', python,
                         '-I', '-S', '-B', '-X', 'utf8', '-c', ABI_LOADER, module, python)
        print(result, flush=True)


def probe(env, tools):
    if preflight._architecture() != (preflight._AMD64, 0):
        raise RuntimeError('The compiler probe requires a native Windows x64 process and host.')
    python = Path(sys.executable)
    if (sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12)
            or python != Path(sys._base_executable) or not python.is_absolute()):
        raise RuntimeError('Launch the probe directly with the intended native CPython 3.12 executable.')
    include = python.parent / 'include'
    library = python.parent / 'libs' / 'python3.lib'
    dlls = (python.with_name('python3.dll'), python.with_name('python312.dll'))
    print(json.dumps({
        'vctools': env['VCToolsVersion'], 'windows_sdk': env['WindowsSDKVersion'].rstrip('\\'),
        'tool_paths': {name: str(path) for name, path in tools.items()},
        'tool_sha256': verify_probe_tools(tools, env), 'python': sys.version,
    }, sort_keys=True), flush=True)
    python_files = {}
    for path in (python, include / 'Python.h', library, *dlls):
        data, _ = private.read_stable(path, limit=32 * 1024**2)
        print(f'{path} SHA-256 {recipe.sha(data)}', flush=True)
        if path in dlls:
            python_files[path.name] = data

    members = {**dict.fromkeys(PROBE_DIRECTORIES, True), **dict.fromkeys(PROBE_FILES, False)}
    with storage.private_workspace(paths.temporary_parent(), 'composerlint-probe-', members=members) as stage:
        with stage.security._token() as token:
            owner = stage.security._default_owner(token)
        print(f'Probe workspace {stage.path}; TokenUser={stage.sid}; TokenOwner={owner}', flush=True)
        run_probe_steps(stage, env, tools, python, python_files)
    print('Compiler and CPython ABI probes passed; private scratch cleanup verified.', flush=True)


def build(args):
    env, tools = toolchain()
    if args.probe_only:
        probe(env, tools)
        return
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
