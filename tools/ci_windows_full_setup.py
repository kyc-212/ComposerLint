"""One native renderer build and full terminal installation in a guarded CI run."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_windows_renderer import ROOT, paths, preflight, private, recipe, setup, storage, toolchain, verify_probe_tools
from ci_windows_renderer import build_bundle, reviewed_assets
from ci_windows_text_setup import guarded_runner, install_once, require_absent, reviewed_manifest
from package_windows_full import setup_entries
from windows_renderer_retention import ARTIFACT, ARCHIVE_LIMIT, MAGIC, recipient_der, retained_entries


# Exact binaries measured on Run 22. Resolve paths from the current developer
# shell; refuse a runner image that silently substituted the compiler or SDK.
REVIEWED_TOOLS = {
    'cl': 'b0a1818ebae6da7f18230a84e76ce839212584ac4c2ab55b97a0d5268085da57',
    'link': 'a8c42e25c91ca2453e1110540ac08f0fc12d7539454a11b9af8baeecb469837d',
    'mt': 'fa74dd31612c78de40169599640fb955ef9ccb9f2dfabf80f100022ff1b8bc22',
    'rc': '9e2ee68da28b3318f50a9f1008d64567a9b402bda384a007a9b71a3f16bd011b',
}


def main():
    # Must match the workflow's reviewed one-run guard before any acquisition.
    runner_python, local_app_data = guarded_runner('30')
    reviewed_manifest(ROOT)
    recipient_der(os.environ['RETENTION_PUBLIC_KEY'], os.environ['RETENTION_KEY_SHA256'])
    retained = paths.temporary_parent() / 'cl-renderer-retention'
    require_absent(retained)
    report = preflight.inspect()
    if not report.compatible or report.destination != local_app_data / 'ComposerLint' / 'setup':
        preflight.print_report(report)
        raise RuntimeError('Native preflight refused the clean Local AppData destination.')
    require_absent(local_app_data / 'ComposerLint')
    developer, tools = toolchain()
    actual_tools = verify_probe_tools(tools, developer)
    for name, expected in REVIEWED_TOOLS.items():
        if actual_tools[name] != expected:
            raise RuntimeError(f'{name}.exe differs from Run 22: expected {expected}, got {actual_tools[name]}.')
    print('Verified the four MSVC and Windows SDK binary hashes from Run 22.', flush=True)
    python, uv, inputs, controls = reviewed_assets()
    colorama, = (asset for asset in controls if asset.name == 'colorama')
    with storage.private_workspace(paths.temporary_parent(), 'cl-full-') as stage:
        scratch = stage.path
        stage.mkdir(('lock',))
        with (private.private_directory(scratch / 'lock', tree_root=True) as lock,
              lock.exclusive_lock()):
            bundle, sha256, _, run, _ = build_bundle(stage, python, uv, inputs, (colorama,))
            provenance = json.loads(setup._read_source(bundle / recipe.PROVENANCE, recipe.BUNDLE_LIMITS[recipe.PROVENANCE]))
            if (provenance['tool_sha256'] != actual_tools or provenance['vctools'] != developer['VCToolsVersion']
                    or provenance['windows_sdk'] != developer['WindowsSDKVersion'].rstrip('\\')):
                raise ValueError('Retained build provenance disagrees with the verified runner toolchain.')
            manifest = {
                'schema': 1,
                'ci': {'commit': os.environ['REVIEWED_COMMIT'], 'run_id': int(os.environ['GITHUB_RUN_ID']),
                       'run_number': int(os.environ['GITHUB_RUN_NUMBER']),
                       'attempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
                       'image': os.environ['ImageOS'], 'image_version': os.environ['ImageVersion']},
                'files': {},
            }
            for name, limit in recipe.BUNDLE_LIMITS.items():
                data = setup._read_source(bundle / name, limit)
                manifest['files'][name] = {'size': len(data), 'sha256': recipe.sha(data)}
            if manifest['files'][recipe.PATCHED_WHEEL]['sha256'] != sha256:
                raise ValueError('Patched wheel changed after the native build.')
            raw = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('ascii')
            manifest_sha256 = recipe.sha(raw)
            for name, data in setup_entries(raw).items():
                setup._write(stage, ('setup-source', *name.split('/')), data)
            source_root = scratch / 'setup-source'
            receipt = install_once(source_root, runner_python, local_app_data, renderer_bundle=bundle,
                                   renderer_sha256=sha256, renderer_manifest_sha256=manifest_sha256)
            # Read the bytes retained by the successful installation, independently of the build output.
            artifacts = receipt.parent / 'renderer-artifacts'
            retained_entries(artifacts, raw, setup._read_source)
            stage.mkdir(('seal',))
            runtime = receipt.parent / 'runtime' / 'Scripts' / 'python.exe'
            evidence_env = {key: os.environ[key] for key in (
                'GITHUB_ACTIONS', 'RUNNER_OS', 'RUNNER_ARCH', 'RETENTION_PUBLIC_KEY', 'RETENTION_KEY_SHA256')}
            run(runtime, '-I', '-B', '-X', 'utf8', ROOT / 'tools' / 'windows_renderer_retention.py',
                'seal', artifacts, source_root / recipe.FULL_MANIFEST, scratch / 'seal', manifest_sha256,
                extra_env=evidence_env)
            sealed = setup._read_source(scratch / 'seal' / ARTIFACT, ARCHIVE_LIMIT + 1024)
            if not sealed.startswith(MAGIC):
                raise ValueError('CI retention output is not the encrypted envelope.')
    require_absent(scratch)
    print('Renderer bundle and native build staging cleanup verified.', flush=True)
    with private.private_directory(retained, create=True, tree_root=True) as output:
        output.write_new(ARTIFACT, sealed)
    print('Verified retention manifest SHA-256:', manifest_sha256, flush=True)
    print('Encrypted renderer retention SHA-256:', recipe.sha(sealed), flush=True)


if __name__ == '__main__':
    main()
