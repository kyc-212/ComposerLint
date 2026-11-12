"""Optional pinned renderer, isolated from the M1 process and source files."""
from contextlib import ExitStack, contextmanager
from importlib.metadata import version, PackageNotFoundError
import json
import hashlib
from pathlib import Path
import subprocess
import sys
from composerlint.viewer_source import fail
from composerlint import runtime_paths
from composerlint import platform_support as host


def renderer_command():
    """Server-owned local build selection; never a request parameter or import hook."""
    if host.platform_name() == 'win32':
        with renderer_scope() as command:
            return command
    try:
        config = runtime_paths.renderer_config()
        if config is None:
            return sys.executable, None
        if config.is_symlink() or not config.is_file():
            raise ValueError('Renderer configuration must be a regular file')
        root = config.parent
        identity = json.loads(config.read_text(encoding='utf-8'))
        if identity['package_version'] != '6.3.0+composerlint.tie1':
            raise ValueError('Unverified renderer build')
        executable = root / identity['python']
        for relative, expected in identity['files_sha256'].items():
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
                raise ValueError('Renderer build changed')
        if not executable.is_file():
            raise ValueError('Renderer interpreter is absent')
    except (OSError, ValueError, KeyError, TypeError):
        fail('renderer_unavailable', 'Configured renderer build is missing or differs from its verified files.')
    return str(executable), identity['package_version']


@contextmanager
def renderer_scope():
    """On Windows keep the verified files and directories held for the worker."""
    if host.platform_name() != 'win32':
        yield renderer_command()
        return
    with ExitStack() as pins:
        try:
            config = runtime_paths.renderer_config()
            if config is None:
                fail('renderer_unavailable', 'Windows notation requires a verified patched renderer configuration.')
            command = pins.enter_context(windows_renderer_scope(config))
        except (OSError, ValueError, KeyError, TypeError):
            fail('renderer_unavailable', 'Configured renderer build is missing or differs from its verified files.')
        yield command


@contextmanager
def windows_renderer_scope(config):
    from composerlint import windows_renderer as pinned
    from composerlint.windows_install_storage import pinned_install_files

    with pinned_install_files(config.parent) as (read, validate):
        identity = json.loads(read((config.name,), limit=2 * 1024**2))
        files = identity['files_sha256']
        executable = identity['python']
        required = {
            'renderer-artifacts/' + pinned.OFFICIAL_WHEEL: pinned.OFFICIAL_SHA256,
            'renderer-artifacts/' + pinned.SOURCE_ARCHIVE: pinned.SOURCE_SHA256,
            'renderer-artifacts/' + pinned.PATCH: pinned.PATCH_SHA256,
        }
        if (identity['package_version'] != pinned.BUILD
                or executable != 'renderer/Scripts/python.exe'
                or not isinstance(files, dict) or len(files) > 8192
                or not {executable, 'python/python.exe', 'renderer/pyvenv.cfg',
                        'renderer-artifacts/' + pinned.PATCHED_WHEEL} <= files.keys()
                or not any(key.startswith('renderer/Lib/site-packages/verovio/') and key.endswith('.pyd')
                           for key in files)
                or any(files.get(name) != digest for name, digest in required.items())):
            raise ValueError('Windows renderer identity is incomplete.')
        seen = {config.name.casefold()}
        for relative, expected in files.items():
            parts = tuple(relative.split('/'))
            folded = relative.casefold()
            if len(parts) > 64 or folded in seen:
                raise ValueError('Windows renderer manifest contains an ambiguous file path.')
            seen.add(folded)
            if pinned.sha(read(parts, limit=32 * 1024**2)) != expected:
                raise ValueError('Windows renderer build changed.')
        validate()
        yield str(config.parent.joinpath(*executable.split('/'))), pinned.BUILD


def render(xml, start=None, end=None, width=1800, *, notation_page=None):
    try:
        if version('verovio') != '6.3.0':
            fail('renderer_unavailable', 'Viewer requires the approved optional Verovio 6.3.0.')
    except PackageNotFoundError:
        fail('renderer_unavailable', 'Optional Verovio 6.3.0 is not installed; use text selection.')
    try:
        with renderer_scope() as (executable, expected_build):
            cp = host.run_owned([executable, '-I', '-B', str(Path(__file__).with_name('verovio_worker.py'))],
                input=json.dumps(dict(xml=xml, start=start, end=end, width=width, notation_page=notation_page)),
                capture_output=True, text=True, timeout=15)
    except host.UnsupportedPlatformOperation as exc:
        fail('renderer_unavailable', str(exc))
    except host.PipeLimitError as exc:
        fail('render_failed', str(exc))
    except subprocess.TimeoutExpired:
        fail('render_timeout', 'Notation rendering exceeded 15 seconds; worker terminated.')
    if cp.returncode or len(cp.stdout.encode()) > 64 * 1024 * 1024:
        fail('render_failed', 'Renderer failed or exceeded its output budget. ' + cp.stderr.strip()[:2048])
    try:
        result = json.loads(cp.stdout)
    except ValueError:
        fail('render_failed', 'Renderer returned an invalid response.')
    if expected_build is not None and result.get('package_version') != expected_build:
        fail('renderer_unavailable', 'Renderer process did not use the configured verified build.')
    if not result.get('pages') or any(len(svg.encode()) > 8 * 1024 * 1024 for svg in result['pages']):
        fail('asset_too_large', 'A complete SVG page exceeded 8 MiB; use M1 text evidence.')
    # Import errors/warnings are significant: never hide an engraving loss.
    if cp.stderr.strip() or result['log'].strip():
        warning = (cp.stderr.strip() or result['log'].strip())[:2048]
        fail('render_failed', 'Renderer reported a notation warning: ' + warning)
    return result
