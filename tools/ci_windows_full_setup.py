"""One native renderer build and full terminal installation in a guarded CI run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_windows_renderer import ROOT, paths, preflight, private, storage, toolchain, verify_probe_tools
from ci_windows_renderer import build_bundle, reviewed_assets
from ci_windows_text_setup import guarded_runner, install_once, require_absent, reviewed_manifest


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
    runner_python, local_app_data = guarded_runner('23')
    reviewed_manifest(ROOT)
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
            bundle, sha256, _, _, _ = build_bundle(stage, python, uv, inputs, (colorama,))
            install_once(ROOT, runner_python, local_app_data, renderer_bundle=bundle, renderer_sha256=sha256)
    require_absent(scratch)
    print('Renderer bundle and native build staging cleanup verified.', flush=True)


if __name__ == '__main__':
    main()
