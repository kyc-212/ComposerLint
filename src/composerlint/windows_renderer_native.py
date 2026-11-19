"""Bundle, build and installation controls for the standalone Windows renderer."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from .platform_support import local_path_text
from .windows_install_storage import _component, pinned_install_files
from .windows_private import read_stable, stable_directory


KIND = 'windows-native-v1'
BUILD = '6.3.0+composerlint.tie1'
EXE = 'composerlint-renderer.exe'
TREE = ('renderer',)
BUNDLE = 'native-renderer.zip'
MAX_BUNDLE = 64 * 1024**2
MAX_MEMBER = 32 * 1024**2
KIT = 'ComposerLint-Standalone-Renderer-Windows-x64-Proof'
KIT_SHA = '0a99814933f0f75564881063fc3d1115c71988e054012d8b8d69dd8c6b8bb0f3'
SOURCE_ARCHIVE = (KIT + '-Source.zip', 26866335,
                  '9a1ec2de2bdbb71a13d6df3384caebe1275d653a70cfca3b70a441463c056daf',
                  'https://github.com/kyc-212/ComposerLint/releases/download/windows-renderer-proof-20261117/')
TOOL_ASSETS = {
    'llvm': ('llvm-mingw-20261104-ucrt-x86_64.zip', 191037046,
             '552191b5754c49a5a20723276a6fc56541ea1922c4c50b7b669022333fc1b40f',
             'https://github.com/mstorsjo/llvm-mingw/releases/download/20261104/'),
    'cmake': ('cmake-4.4.4-windows-x86_64.zip', 54400562,
              'bace36e94b31c68ab6fa295f26dfa11219e0701cf7c94b0284a7d1cb13dac536',
              'https://github.com/Kitware/CMake/releases/download/v4.4.4/'),
    'ninja': ('ninja-win.zip', 291570,
              '07fc8261b42b20e71d1720b39068c2e14ffcee6396b76fb7a795fb460b78dc65',
              'https://github.com/ninja-build/ninja/releases/download/v1.13.2/'),
}
TOOL_EXECUTABLES = {
    'llvm': ('x86_64-w64-mingw32-clang++.exe', 'x86_64-w64-mingw32-clang.exe', 'llvm-readobj.exe'),
    'cmake': ('cmake.exe',), 'ninja': ('ninja.exe',),
}
PROOF_DRIVER = ('tools', 'windows_renderer_native', 'prove.py')
CONTROLS = (PROOF_DRIVER, ('tools', 'ci_windows_mcp.py'),
            ('tests', 'fixtures', 'basic.musicxml'),
            ('tests', 'fixtures', 'm3', 'cadence_authentic_end.musicxml'),
            ('tests', 'fixtures', 'v02_m2', 'multipage.musicxml'),
            ('tests', 'fixtures', 'v02_m2', 'grandstaff.musicxml'))
RESOURCE_SUFFIXES = {'.xml', '.json', '.css', '.svg'}


@dataclass(frozen=True)
class Selection:
    origin: str
    bundle: bytes | None = None
    source: bytes | None = None
    tools: tuple = ()
    controls: tuple = ()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate renderer JSON key.')
    return result


def json_object(data):
    expect = json.loads(data, object_pairs_hook=_unique)
    if not isinstance(expect, dict):
        raise ValueError('Expected a renderer JSON object.')
    return expect


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def components(path):
    parts = tuple(_component(p) for p in path.split('/'))
    if len(parts) > 64:
        raise ValueError('Renderer path is too deep.')
    return parts


def inventory(archive, *, expanded, member_limit, max_items, directories=False):
    seen, explicit, files, total = {}, set(), {}, 0
    for item in archive.infolist():
        folder = item.is_dir()
        name = item.filename[:-1] if folder else item.filename
        parts = components(name)
        folded = tuple(p.casefold() for p in parts)
        mode = stat.S_IFMT(item.external_attr >> 16) if item.create_system == 3 else 0
        if (item.orig_filename != item.filename or item.flag_bits & 1 or item.external_attr & 0x400
                or mode not in (0, stat.S_IFDIR if folder else stat.S_IFREG)
                or folder and (not directories or item.file_size)):
            raise ValueError('Unsafe renderer ZIP member.')
        if folded in explicit:
            raise ValueError('Duplicate or case-colliding renderer ZIP member.')
        explicit.add(folded)
        for i in range(1, len(parts) + 1):
            key = folded[:i]
            value = (parts[:i], folder or i < len(parts))
            if seen.setdefault(key, value) != value:
                raise ValueError('Case or directory collision in renderer ZIP.')
        if len(seen) > max_items:
            raise ValueError('Excessive renderer ZIP members.')
        if not folder:
            files[name] = item
        total += item.file_size
        if not 0 <= item.file_size <= member_limit or total > expanded:
            raise ValueError('Renderer ZIP exceeds its expanded byte limit.')
    return files


def _amd64(data):
    offset = int.from_bytes(data[60:64], 'little')
    return (data.startswith(b'MZ') and offset >= 64 and offset + 26 <= len(data)
            and data[offset:offset + 6] == b'PE\0\0\x64\x86'
            and data[offset + 24:offset + 26] == b'\x0b\x02'
            and not int.from_bytes(data[offset + 22:offset + 24], 'little') & 0x2000)


def bundle_files(raw, *, built=False):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = inventory(archive, expanded=120 * 1024**2, member_limit=MAX_MEMBER, max_items=8192)
        if 'proof.json' not in files or files['proof.json'].file_size > 2 * 1024**2:
            raise ValueError('Missing or oversized standalone renderer inventory.')
        manifest = json_object(archive.read('proof.json'))
        if built and manifest.get('corresponding_source') != dict(name=SOURCE_ARCHIVE[0], sha256=SOURCE_ARCHIVE[2]):
            raise ValueError('Cached renderer was built from a different source companion.')
        pins = manifest['files']
        if (not isinstance(pins, dict) or set(files) - {'README.txt', 'proof.json'} != pins.keys()
                or not all(valid_sha(pin) for pin in pins.values())):
            raise ValueError('Standalone renderer inventory differs from the ZIP.')
        result = {}
        control_names = {'/'.join(parts) for parts in CONTROLS}
        for name in pins:
            data = archive.read(name)
            if name in ('patched/' + EXE, 'comparison/' + EXE):
                if not _amd64(data):
                    raise ValueError('Expected a native x64 Windows renderer executable.')
            elif name.startswith('resources/data/'):
                if Path(name).suffix.lower() not in RESOURCE_SUFFIXES:
                    raise ValueError('Unexpected file type in the renderer resources.')
            elif name.startswith('LICENSES/'):
                if not data or b'\0' in data:
                    raise ValueError('Expected a text renderer notice.')
                data.decode('utf-8')
            elif name not in control_names:
                raise ValueError('Unexpected standalone renderer ZIP member.')
            if sha(data) != pins[name]:
                raise ValueError(f'Standalone renderer file differs from its pin: {name}')
            result[name] = data
        if (not {f'{kind}/{EXE}' for kind in ('patched', 'comparison')} <= result.keys()
                or not any(name.startswith('resources/data/') for name in result)
                or not any(name.startswith('LICENSES/') for name in result)):
            raise ValueError('Expected both renderers, their shared resources and notices.')
        return result


def checked_input(path, limit, digest):
    local_path_text(path, target='win32')
    data, stamp = read_stable(path, limit=limit)
    if stamp.links != 1 or not valid_sha(digest) or sha(data) != digest:
        raise ValueError(f'Input differs from its reviewed SHA-256: {path.name}')
    return data


def cached_bundle(destination):
    """Reuse a committed stock or local-build bundle, never an owner override."""
    try:
        with stable_directory(destination):
            candidates = sorted(p.name for p in destination.iterdir()
                                if re.fullmatch(r'install-[0-9a-f]{20}', p.name))[:128]
    except FileNotFoundError:
        return None
    for name in candidates:
        try:
            with pinned_install_files(destination / name) as (read, validate):
                receipt = json_object(read(('complete.json',), limit=32 * 1024))
                origin, digest = receipt.get('renderer_origin'), receipt.get('renderer_bundle_sha256')
                if (receipt.get('target') != 'windows-x64-full-pilot' or not valid_sha(digest)
                        or origin not in ('stock', 'built') or origin == 'stock' and digest != KIT_SHA):
                    continue
                data = read((BUNDLE,), limit=MAX_BUNDLE)
                if sha(data) != digest:
                    continue
                bundle_files(data, built=origin == 'built')
                validate()
                return origin, data
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            continue
    return None


def tool_paths(overrides, confirm, *, search_path=None):
    """Discover candidates without executing anything from the ambient PATH."""
    roots = (os.environ.get('PATH', '') if search_path is None else search_path).split(';')
    selected = []
    for name, executables in TOOL_EXECUTABLES.items():
        explicit = overrides.get(name)
        if explicit:
            files = tuple(explicit / exe for exe in executables) if name == 'llvm' else (explicit,)
        else:
            files = ()
            for root in roots:
                try:
                    candidate = Path(local_path_text(root.strip('"'), target='win32'))
                except ValueError:
                    continue
                possible = tuple(candidate / exe for exe in executables)
                if all(p.is_file() for p in possible):
                    if confirm(f'Use the {name} found at {possible[0]}? Type USE to select it: ') == 'USE':
                        files = possible
                    break
        if files:
            for file in files:
                local_path_text(file, target='win32')
                if file.suffix.lower() != '.exe':
                    raise ValueError(f'Expected an explicit executable for {name}.')
            selected.append((name, files))
    return tuple(selected)


def extract(stage, raw, prefix, *, tool=False):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = inventory(archive, expanded=(3 * 1024 if tool else 160) * 1024**2,
                          member_limit=(256 if tool else 32) * 1024**2,
                          max_items=45000 if tool else 15000, directories=tool)
        for name, item in files.items():
            parts = components(name)
            with archive.open(item) as source, stage.create((*prefix, *parts)) as dest:
                while buffer := source.read(1024**2):
                    dest.write(buffer)
        return tuple(files)


@contextmanager
def renderer_scope(config, read, validate, identity):
    if (set(identity) != {'kind', 'package_version', 'files_sha256'}
            or identity['kind'] != KIND or identity['package_version'] != BUILD):
        raise ValueError('Unknown standalone renderer configuration.')
    hashes = identity['files_sha256']
    if not isinstance(hashes, dict) or EXE not in hashes or not 2 <= len(hashes) <= 8192:
        raise ValueError('Invalid standalone renderer inventory.')
    paths, folded = [], set()
    for name, digest in hashes.items():
        parts = components(name)
        if (not valid_sha(digest) or name.casefold() in folded
                or name != EXE and (parts[0] != 'data' or Path(name).suffix.lower() not in RESOURCE_SUFFIXES)):
            raise ValueError('Unsafe standalone renderer inventory.')
        folded.add(name.casefold())
        held = (*TREE, *parts)
        data = read(held, limit=MAX_MEMBER)
        if sha(data) != digest or name == EXE and not _amd64(data):
            raise ValueError('Standalone renderer files changed.')
        paths.append(held)
    validate(TREE, paths)
    yield str(config.parent.joinpath(*TREE, EXE)), BUILD


def asset(spec):
    from .windows_setup import Asset

    name, size, digest, base = spec
    return Asset(name, base + name, digest, size)


def select(root, destination, *, stock, owner, owner_digest, source, overrides, confirm):
    from .windows_setup import _read_source

    controls = tuple((parts, _read_source(root.joinpath(*parts), 2 * 1024**2)) for parts in CONTROLS)
    if owner is not None or stock is not None:
        origin, path, digest = ('owner', owner, owner_digest) if owner is not None else ('stock', stock, KIT_SHA)
        raw = checked_input(path, MAX_BUNDLE, digest)
        bundle_files(raw)
        return Selection(origin, bundle=raw, controls=controls)
    if source is None and not any(overrides.values()):
        cached = cached_bundle(destination)
        if cached is not None:
            return Selection(cached[0], bundle=cached[1], controls=controls)
    source_bytes = checked_input(source, MAX_BUNDLE, SOURCE_ARCHIVE[2]) if source else None
    return Selection('built', source=source_bytes, tools=tool_paths(overrides, confirm), controls=controls)


def planned_assets(selection):
    if selection.bundle is not None:
        return ()
    source = () if selection.source is not None else (asset(SOURCE_ARCHIVE),)
    supplied = dict(selection.tools)
    return source + tuple(asset(spec) for key, spec in TOOL_ASSETS.items() if key not in supplied)


def _portable_tools(stage, key, acquire):
    pin = asset(TOOL_ASSETS[key])
    raw = acquire(pin)
    archive_root = ('work', 'native-tools', key)
    names = extract(stage, raw, archive_root, tool=True)
    paths = set(names)
    programs = TOOL_EXECUTABLES[key]
    candidates = [name.rpartition('/')[0] for name in names
                  if name.rpartition('/')[2] == programs[0]]
    parents = [parent for parent in candidates if all(
        (parent + '/' if parent else '') + executable in paths for executable in programs)]
    if len(parents) != 1:
        raise ValueError(f'The {key} archive has an unexpected tool layout.')
    directory = stage.path.joinpath(*archive_root, *components(parents[0])) if parents[0] else stage.path.joinpath(*archive_root)
    return tuple(directory / exe for exe in programs)


def _pe_probe(executable, reader, run):
    lines = run(reader, '--file-headers', '--coff-imports', '--coff-load-config', executable).decode('utf-8')
    if any(flag not in lines for flag in ('IMAGE_FILE_MACHINE_AMD64', 'DYNAMIC_BASE', 'NX_COMPAT',
                                          'HIGH_ENTROPY_VA', 'GUARD_CF', 'CF_FUNCTION_TABLE_PRESENT')):
        raise ValueError('The local toolchain did not produce a protected Windows x64 application.')
    dlls = re.findall(r'^\s*Name:\s+(\S+\.dll)\s*$', lines, re.MULTILINE | re.IGNORECASE)
    if (not any(name.lower().startswith('api-ms-win-crt-') or name.lower() == 'ucrtbase.dll' for name in dlls)
            or any(name.lower() not in {'kernel32.dll', 'ucrtbase.dll'}
                   and not name.lower().startswith('api-ms-win-crt-') for name in dlls)):
        raise ValueError('The local toolchain produced an unexpected renderer DLL dependency.')


def _probe_tools(key, tools, run, stage):
    from .windows_setup import _write

    if key == 'cmake':
        output = run(tools[0], '--version').decode('utf-8')
        match = re.match(r'cmake version (\d+)\.(\d+)', output)
        if not match or tuple(map(int, match.groups())) < (3, 20):
            raise ValueError('A compatible CMake (3.20 or later) is required.')
    elif key == 'ninja':
        output = run(tools[0], '--version').decode('utf-8')
        if not re.match(r'[1-9]\d*\.\d+', output):
            raise ValueError('Expected an executable Ninja build tool.')
    else:
        cxx, cc, readobj = tools
        for tool in (cxx, cc):
            output = run(tool, '--version').decode('utf-8').lower()
            if 'clang version' not in output or not re.search(r'target: x86_64-[^\s]*windows-gnu', output):
                raise ValueError('Expected LLVM-MinGW targeting Windows x64.')
        # A separate probe avoids spending time building Verovio with an
        # installed LLVM that only targets MSVC or the legacy CRT.
        prefix = ('work', 'native-probe', 'attempt-' + os.urandom(6).hex())
        _write(stage, (*prefix, 'probe.cpp'), b'''#include <windows.h>
#include <cstdio>
#include <span>
#ifndef _UCRT
#error The renderer needs the Windows Universal CRT
#endif
int (*volatile show)(const char *) = std::puts;
int main(int argc, char **argv) {
    return show(std::span(argv, argc)[0]) < 0;
}
''')
        directory = stage.path.joinpath(*prefix)
        exe = directory / 'probe.exe'
        run(cxx, '-std=c++20', '-static', '-mguard=cf',
            '-Wl,--dynamicbase,--nxcompat,--high-entropy-va', directory / 'probe.cpp', '-o', exe)
        _pe_probe(exe, readobj, run)


def _build(stage, selection, acquire, command, env, confirm, status, *, offline):
    from .windows_setup import SetupError, _write

    def run(executable, *args, timeout=60):
        # Explicit user-selected paths and their publishers' portable
        # equivalents are the only tools allowed to execute here.
        snap, _ = read_stable(Path(executable), limit=256 * 1024**2)
        if not _amd64(snap):
            raise ValueError('Expected a Windows x64 build tool executable.')
        del snap
        return command([str(executable), *(str(arg) for arg in args)],
                       cwd=str(stage.path / 'work'), env=env, timeout=timeout)

    supplied = dict(selection.tools)
    chosen = {}
    for key in TOOL_ASSETS:
        tool = supplied.get(key)
        if tool:
            try:
                _probe_tools(key, tool, run, stage)
            except (OSError, ValueError, SetupError) as error:
                print(f'The selected {key} failed its capability check: {error}')
                tool = None
        if not tool:
            pin = asset(TOOL_ASSETS[key])
            verb = 'USE' if offline else 'DOWNLOAD'
            if confirm(f'Use the portable {pin.filename} ({pin.size:,} bytes, '
                       f'{pin.url}, SHA-256 {pin.sha256})? Type {verb}: ') != verb:
                raise SetupError('No compatible renderer build tool selected.')
            tool = _portable_tools(stage, key, acquire)
            _probe_tools(key, tool, run, stage)
        chosen[key] = tool

    source = selection.source if selection.source is not None else acquire(asset(SOURCE_ARCHIVE))
    if sha(source) != SOURCE_ARCHIVE[2]:
        raise ValueError('Standalone source bytes differ from the reviewed source companion.')
    _write(stage, ('renderer-source', SOURCE_ARCHIVE[0]), source)
    prefix = ('work', 'native-source')
    names = extract(stage, source, prefix)
    source_path = stage.path.joinpath(*prefix)
    cmake, = chosen['cmake']
    ninja, = chosen['ninja']
    cxx, cc, readobj = chosen['llvm']
    files = {}
    for variant, label, tree in (('tie1', 'patched', 'verovio-6.3.0-tie1'),
                                 ('comparison', 'comparison', 'verovio-6.3.0')):
        status('renderer_build', f'Building the Windows {label} renderer.')
        out = stage.path / 'work' / ('native-' + variant)
        run(cmake, '-S', source_path / 'tools/windows_renderer_native', '-B', out, '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_SYSTEM_NAME=Windows', '-DCMAKE_SYSTEM_PROCESSOR=x86_64',
            f'-DCMAKE_C_COMPILER={cc}', f'-DCMAKE_CXX_COMPILER={cxx}', f'-DCMAKE_MAKE_PROGRAM={ninja}',
            f'-DCOMPOSERLINT_VEROVIO_SOURCE={source_path / tree}', f'-DCOMPOSERLINT_RENDERER_VARIANT={variant}')
        # Large C++ files dominate peak memory on typical setup machines.
        run(cmake, '--build', out, '--parallel', '4', timeout=1800)
        executable = out / 'bin' / EXE
        _pe_probe(executable, readobj, run)
        files[f'{label}/{EXE}'], _ = read_stable(executable, limit=MAX_MEMBER)
    for name in names:
        if name.startswith('verovio-6.3.0-tie1/data/'):
            relative = name.removeprefix('verovio-6.3.0-tie1/')
            if Path(relative).suffix.lower() not in RESOURCE_SUFFIXES:
                continue
            data, _ = read_stable(source_path / name, limit=MAX_MEMBER)
            files['resources/' + relative] = data
        elif name.startswith('LICENSES/'):
            files[name], _ = read_stable(source_path / name, limit=MAX_MEMBER)
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            manifest = {'files': {name: sha(data) for name, data in files.items()},
                        'corresponding_source': dict(name=SOURCE_ARCHIVE[0], sha256=SOURCE_ARCHIVE[2])}
            archive.writestr('proof.json', json.dumps(manifest, sort_keys=True))
        data = buffer.getvalue()
    if len(data) > MAX_BUNDLE:
        raise ValueError('The locally built renderer bundle exceeds its size limit.')
    return data


def stage_controls(selection, stage):
    from .windows_setup import _write

    for parts, code in selection.controls:
        _write(stage, ('work', 'controls', *parts), code)
    return stage.path / 'work' / 'controls'


def install(selection, stage, run_python, acquire, command, env, confirm, status, *, offline):
    from .windows_setup import _write

    status('renderer', 'Preparing the standalone Windows renderer.')
    raw = selection.bundle if selection.bundle is not None else _build(
        stage, selection, acquire, command, env, confirm, status, offline=offline)
    files = bundle_files(raw, built=selection.origin == 'built')
    patched = files[f'patched/{EXE}']
    manifest = {EXE: sha(patched)}
    _write(stage, (*TREE, EXE), patched)
    compare = ('work', 'native-comparison', EXE)
    _write(stage, compare, files[f'comparison/{EXE}'])
    for name, data in files.items():
        if name.startswith('resources/data/'):
            relative = name.removeprefix('resources/')
            _write(stage, (*TREE, *components(relative)), data)
            manifest[relative] = sha(data)
        elif name.startswith('LICENSES/'):
            _write(stage, ('renderer-notices', *components(name)), data)
    if selection.origin != 'owner':
        source = asset(SOURCE_ARCHIVE)
        _write(stage, ('renderer-notices', 'SOURCE.txt'),
               f'{source.url}\nSHA-256 {source.sha256}\n'.encode('ascii'))
    _write(stage, (BUNDLE,), raw)
    _write(stage, ('renderer.json',), json.dumps(dict(kind=KIND, package_version=BUILD, files_sha256=manifest),
                                                sort_keys=True).encode('utf-8'))
    control = stage_controls(selection, stage)
    config = stage.path / 'renderer.json'
    resource = stage.path.joinpath(*TREE)
    runtime = ('runtime', 'Scripts', 'python.exe')
    # Reuse the public native proof, plus the installed MCP/viewer check,
    # before a generation can be marked complete.
    with ExitStack() as handles:
        exe = handles.enter_context(stage.held_file((*TREE, EXE)))
        other = handles.enter_context(stage.held_file(compare))
        status('renderer_controls', 'Checking both native renderers and their loaded DLL paths.')
        report = run_python(runtime, '-I', '-B', '-X', 'utf8', control.joinpath(*PROOF_DRIVER),
                            exe, resource, '--expected-sha256', manifest[EXE], '--comparison', other,
                            '--comparison-resources', resource, cwd=control, timeout=240)
        proof = json_object(report)
        if not (proof.get('comparison_exercised') and proof.get('loaded_modules')
                and proof.get('comparison_loaded_modules')):
            raise ValueError('The Windows render and DLL proof did not complete.')
    env.update(COMPOSERLINT_RENDERER_CONFIG=str(config), COMPOSERLINT_TEMP_DIR=env['TEMP'])
    status('mcp', 'Checking the installed MCP server and standalone renderer.')
    output = run_python(runtime, '-I', '-B', '-X', 'utf8', control / 'tools/ci_windows_mcp.py', '--full',
                        cwd=control, timeout=240)
    print(output.decode('utf-8', 'backslashreplace'), end='', flush=True)
    return dict(target='windows-x64-full-pilot', renderer_config=str(config), renderer_origin=selection.origin,
                renderer_sha256=manifest[EXE], renderer_bundle_sha256=sha(raw), renderer_controls=proof,
                mcp_tools=18)
