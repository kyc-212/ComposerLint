"""Reviewed input identities for the native Windows renderer candidate."""

import hashlib
from pathlib import Path

BUILD = '6.3.0+composerlint.tie1'
OFFICIAL_WHEEL = 'verovio-6.3.0-cp310-abi3-win_amd64.whl'
OFFICIAL_SHA256 = 'cc8bab65570ccb38d6a440fa13d3e4820df34ee9ca483decdbe8371c4291b1a8'
PATCHED_WHEEL = f'verovio-{BUILD}-cp310-abi3-win_amd64.whl'
SOURCE_ARCHIVE = 'verovio-6.3.0.tar.gz'
SOURCE_SHA256 = '33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c'
PATCH = 'verovio-6.3.0-deferred-tie-stops.patch'
PATCH_SHA256 = '10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60'
PREIMAGE = '0def2542a241989f2368fe9b54e67db474242fc0d24b27c48af8dac5ee499ab5'
POSTIMAGE = '4235d47c8cf9939ca09858a0d7d15c56eb1b9debdd8ceb6f067bdce536605ad9'

# Run in the selected virtual environment with -I -B. Keep the probe
# self-contained so that it needs only the installed renderer and stdlib.
IDENTITY_PROBE = r"""
import ctypes
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import re
import sys

expected, executable, base = sys.argv[1:]
executable, base = Path(executable), Path(base)


def require_same_file(label, actual, wanted):
    try:
        matches = Path(actual).samefile(wanted)
    except (OSError, TypeError) as error:
        raise RuntimeError(f'{label}: cannot compare {actual!r} with {str(wanted)!r}: {error}') from error
    if not matches:
        raise RuntimeError(f'{label}: expected {str(wanted)!r}, got {str(actual)!r}')


if sys.implementation.name != 'cpython':
    raise RuntimeError(f'Python implementation: expected cpython, got {sys.implementation.name!r}')
if sys.version_info[:3] != (3, 12, 13):
    raise RuntimeError(f'CPython version: expected 3.12.13, got {sys.version!r}')
require_same_file('Virtual environment executable', sys.executable, executable)
require_same_file('Base Python executable', sys._base_executable, base)
prefix = executable.parents[1]
require_same_file('Virtual environment prefix', sys.prefix, prefix)

packages = prefix / 'Lib' / 'site-packages'
package = packages / 'verovio'
native = package / '_verovio.pyd'
try:
    dist = distribution('verovio')
except PackageNotFoundError as error:
    raise RuntimeError(f'Verovio distribution metadata missing: expected {expected!r} in {str(packages)!r}') from error
require_same_file('Verovio distribution location', dist.locate_file(''), packages)
if dist.version != expected:
    raise RuntimeError(f'Verovio distribution version: expected {expected!r}, got {dist.version!r}')

try:
    import verovio
    from verovio import _verovio
except ImportError as error:
    raise RuntimeError(f'Verovio import from {str(package)!r} failed: {error}') from error
require_same_file('Verovio package import', getattr(verovio, '__file__', None), package / '__init__.py')
imported_native = getattr(_verovio, '__file__', None)
if not imported_native or Path(imported_native).name.lower() != native.name:
    raise RuntimeError(f'Verovio native extension filename: expected {native.name!r}, got {imported_native!r}')
require_same_file('Verovio native extension location', imported_native, native)

# Verovio 6.3.0 appends [undefined] directly when git_commit.h is absent.
# Release builds with a generated header can have a Git suffix or no suffix.
toolkit = verovio.toolkit().getVersion()
if not re.fullmatch(r'6\.3\.0(?:\[undefined\]|-[0-9a-f]{7,40})?', toolkit):
    raise RuntimeError(f'Verovio toolkit version: expected release 6.3.0, got {toolkit!r}')
print(f'Verified Verovio distribution {expected}, toolkit {toolkit}, environment {prefix}')

kernel = ctypes.WinDLL('kernel32.dll', winmode=0x0800, use_last_error=True)
kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype = ctypes.c_void_p
kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint]
kernel.GetModuleFileNameW.restype = ctypes.c_uint
for path in (native, base.with_name('python3.dll'), base.with_name('python312.dll')):
    handle = kernel.GetModuleHandleW(path.name)
    if not handle:
        raise RuntimeError(f'Loaded {path.name}: no module handle for {path} (Windows error {ctypes.get_last_error()})')
    result = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(handle, result, len(result))
    if not 0 < length < len(result):
        raise RuntimeError(f'Loaded {path.name}: cannot read mapped path for {path} '
                           f'(length {length}, Windows error {ctypes.get_last_error()})')
    require_same_file(f'Loaded {path.name}', result.value, path)
    print(f'Loaded {expected}: {result.value}')
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_bytes(path, expected, limit):
    from .windows_private import read_stable
    data, stamp = read_stable(path, limit=limit)
    if stamp.links != 1 or sha(data) != expected:
        raise ValueError(f'Renderer input does not match its SHA-256 pin: {Path(path).name}')
    return data


def apply_reviewed_patch(source, patch):
    """Apply the single reviewed hunk to the exact upstream file bytes."""
    if sha(source) != PREIMAGE or sha(patch) != PATCH_SHA256:
        raise ValueError('Verovio preimage or reviewed patch differs.')
    lines = patch.splitlines(keepends=True)[3:]
    before = b''.join(line[1:] for line in lines if line.startswith((b' ', b'-')))
    after = b''.join(line[1:] for line in lines if line.startswith((b' ', b'+')))
    if source.count(before) != 1:
        raise ValueError('Reviewed Verovio patch context must match once.')
    result = source.replace(before, after, 1)
    if sha(result) != POSTIMAGE:
        raise ValueError('Verovio patched postimage differs.')
    return result
