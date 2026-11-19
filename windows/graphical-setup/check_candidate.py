"""Verify and extract the graphical ZIP, or exercise its staged MCP sample loader."""

import argparse
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from package import read_package, sha


def extract_candidate(archive, destination):
    raw = archive.read_bytes()
    checksum = archive.with_suffix('.zip.sha256').read_text(encoding='ascii').strip()
    if checksum != f'{sha(raw)} *{archive.name}':
        raise ValueError('Graphical ZIP differs from its generated SHA-256.')
    read_package(BytesIO(raw))
    destination.mkdir()
    with ZipFile(BytesIO(raw)) as package:
        package.extractall(destination)


class WorkingFolder:
    """Filesystem adapter for the staging check on a non-Windows build runner."""

    def __init__(self, path):
        self.path = path

    def create(self, parts):
        target = self.path.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.open('xb')


def check_staged_samples(root, work):
    # Import only from the extracted ZIP. The native reader requires Win32
    # handles, so adapt that boundary while using the real selection and writer.
    spec = ModuleSpec('_packaged_setup', loader=None, is_package=True)
    spec.submodule_search_locations = [str(root / 'src' / 'composerlint')]
    sys.modules[spec.name] = module_from_spec(spec)
    native = import_module(f'{spec.name}.windows_renderer_native')
    setup = import_module(f'{spec.name}.windows_setup')
    with (patch.object(setup, '_read_source', lambda path, limit: path.read_bytes()),
          patch.object(native, 'cached_bundle', return_value=None)):
        selection = native.select(root, work / 'unused-install', stock=None, owner=None,
                                  owner_digest=None, source=None, overrides={}, confirm=lambda _: '')
    controls = native.stage_controls(selection, WorkingFolder(work))
    subprocess.run(
        [sys.executable, '-I', '-S', '-B', '-c',
         "from runpy import run_path; run_path('tools/ci_windows_mcp.py')['load_sample_inputs']()"],
        cwd=controls, check=True,
    )
    print('The packaged MCP verifier loaded its samples from the staged working folder.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--extract', type=Path)
    args = parser.parse_args()
    if args.extract is not None:
        extract_candidate(args.archive, args.extract)
    else:
        with tempfile.TemporaryDirectory(prefix='cl-setup-') as temporary:
            temporary = Path(temporary)
            root = temporary / 'package'
            extract_candidate(args.archive, root)
            check_staged_samples(root, temporary / 'working')


if __name__ == '__main__':
    main()
