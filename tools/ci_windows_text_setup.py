"""Run the reviewed terminal installer once on its guarded Windows CI runner."""

import hashlib
import json
import os
from pathlib import Path
import re
from runpy import run_path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
# Isolated Python omits the script directory from sys.path.
_host = run_path(str(ROOT / 'tools' / 'ci_windows_host_files.py'))


def reviewed_manifest(root):
    raw = (root / 'tools' / 'windows-setup-assets.json').read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != '7371fb1a1b6ab884816f14d0a550f76d34c46a7f6c08eb812e7260b09f8aca0c':
        raise ValueError('The reviewed Windows prerequisite manifest changed.')
    manifest = json.loads(raw)
    lock = (root / 'uv.lock').read_bytes()
    if (set(manifest['bootstrap']) != {'python', 'uv'} or len(manifest['wheels']) != 59
            or hashlib.sha256(lock).hexdigest() != manifest['lock_sha256']):
        raise ValueError('The reviewed set of 61 prerequisites or the byte-exact lock changed.')
    return manifest


def plain_entry(path, *, directory=False):
    if sys.platform == 'win32':
        info = _host['find_metadata'](path)
        if info is None:
            raise FileNotFoundError(path)
        attributes = info['attributes']
        correct_type = bool(attributes & stat.FILE_ATTRIBUTE_DIRECTORY) == directory
        forbidden = stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DEVICE
        if correct_type and not attributes & forbidden:
            return
    elif (stat.S_ISDIR if directory else stat.S_ISREG)(path.lstat().st_mode):
        return
    raise OSError(f'Unexpected entry type at {path}.')


def require_absent(path):
    if sys.platform == 'win32':
        if _host['find_metadata'](path) is None:
            return
    else:
        try:
            path.lstat()
        except FileNotFoundError:
            return
    raise OSError(f'Expected an absent path: {path}')


def verify_receipt(destination, manifest, output, *, renderer_sha256=None):
    plain_entry(destination.parent, directory=True)
    plain_entry(destination, directory=True)
    lock_path = destination / 'setup.lock'
    plain_entry(lock_path)
    generations = [path for path in destination.iterdir() if path != lock_path]
    if len(generations) != 1 or not re.fullmatch(r'install-[0-9a-f]{20}', generations[0].name):
        raise ValueError('Expected one private installation generation under Local AppData.')
    generation = generations[0]
    plain_entry(generation, directory=True)
    receipt_path = generation / 'complete.json'
    plain_entry(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    runtime = generation / 'runtime' / 'Scripts' / 'python.exe'
    full = renderer_sha256 is not None
    mode = 'full' if full else 'text'
    lines = output.splitlines()
    if (type(receipt['schema']) is not int or receipt['schema'] != 1
            or receipt['target'] != f'windows-x64-{mode}-pilot'
            or receipt['lock_sha256'] != manifest['lock_sha256']
            or Path(receipt['python']) != runtime
            or not re.fullmatch(r'[0-9a-f]{64}', receipt['plan'])
            or f"Reviewed setup plan: {receipt['plan']}" not in lines
            or f"Private Windows {mode} environment verified: {receipt['python']}" not in lines):
        raise ValueError('Completion receipt does not match the verified installation plan.')
    for directory in (runtime.parent.parent, runtime.parent, generation / 'python', generation / 'uv'):
        plain_entry(directory, directory=True)
    for executable in (runtime, generation / 'python' / 'python.exe', generation / 'uv' / 'uv.exe'):
        plain_entry(executable)
    if full:
        renderer = generation / 'renderer' / 'Scripts' / 'python.exe'
        config = generation / 'renderer.json'
        for directory in (renderer.parents[1], renderer.parent, generation / 'renderer-artifacts'):
            plain_entry(directory, directory=True)
        for path in (renderer, config):
            plain_entry(path)
        if (Path(receipt['renderer_config']) != config or receipt['renderer_sha256'] != renderer_sha256):
            raise ValueError('Completion receipt does not match the reviewed renderer candidate.')
        if receipt['renderer_controls'] != dict(tests=10, failures=0, errors=0, skipped=0):
            raise ValueError('Completion receipt lacks ten passing renderer controls.')
        if receipt['mcp_tools'] != 18 or 'MCP stdio discovered 18 tools.' not in lines:
            raise ValueError('Completion receipt lacks installed MCP discovery evidence.')
        if 'Verified unchanged authored sources and MCP shutdown.' not in lines:
            raise ValueError('Completion output lacks source-preservation and MCP shutdown evidence.')
    for transient in ('work', 'receipt.pending'):
        require_absent(generation / transient)
    return receipt_path


def install_once(root, runner_python, local_app_data, *, renderer_bundle=None, renderer_sha256=None,
                 run=subprocess.run, snapshot=_host['host_snapshot']):
    manifest = reviewed_manifest(root)
    destination = local_app_data / 'ComposerLint' / 'setup'
    require_absent(destination.parent)
    before = snapshot(runner_python, local_app_data)
    for entry in before['Python/uv files and aliases']:
        if entry['type'] == 'app_execution_alias':
            print(f"Recorded Windows app execution alias {entry['path']} (tag 0x{entry['reparse_tag']:08X}).")
    print('Captured baseline host snapshot before installation.', flush=True)
    command = [str(runner_python), '-I', '-S', '-B', '-u', '-X', 'utf8',
               str(root / 'tools' / 'setup_composerlint.py')]
    if renderer_bundle is None:
        command.append('--windows-text-pilot')
    else:
        command.extend(('--windows-full-pilot', '--renderer-bundle', str(renderer_bundle),
                        '--renderer-sha256', renderer_sha256))
    try:
        # Send one exact consent line to the existing terminal entry point. Its
        # prompt is reached only after preflight and plan preparation.
        result = run(command, cwd=root, input='INSTALL\n', encoding='utf-8',
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(result.stdout, end='', flush=True)
        result.check_returncode()
        receipt = verify_receipt(destination, manifest, result.stdout, renderer_sha256=renderer_sha256)
    finally:
        after = snapshot(runner_python, local_app_data)
        changes = [scope for scope in before if before[scope] != after[scope]]
        if changes:
            raise RuntimeError('Host snapshot changed in: ' + ', '.join(changes))
        print('Before and after host snapshots match.', flush=True)
    print('Verified 61 pinned files, private CPython 3.12.13 x64 and uv 0.12.3, '
          '59 locked distributions and the locally built ComposerLint.')
    print(f'Verified completion receipt and removal of download/build staging: {receipt}')


def guarded_runner(run_number):
    expected = {
        'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Windows', 'RUNNER_ARCH': 'X64',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REPOSITORY': 'kyc-212/ComposerLint',
        'GITHUB_ACTOR': 'kyc-212', 'GITHUB_REF': 'refs/heads/codex/windows-ci-pilot',
        'GITHUB_RUN_NUMBER': run_number, 'GITHUB_RUN_ATTEMPT': '1',
    }
    if sys.platform != 'win32' or any(os.environ.get(key) != value for key, value in expected.items()):
        raise RuntimeError('This entry point requires the guarded Windows installation dispatch.')
    commit = os.environ['REVIEWED_COMMIT']
    checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if (not re.fullmatch(r'[0-9a-f]{40}', commit) or commit != os.environ.get('GITHUB_SHA')
            or commit != checkout or ROOT != Path(os.environ['GITHUB_WORKSPACE']).resolve()):
        raise RuntimeError('Checkout differs from the reviewed dispatch.')
    runner_python = Path(os.environ['CI_RUNNER_PYTHON'])
    if runner_python != Path(sys.executable):
        raise RuntimeError('Orchestration is not running under the verified runner Python.')
    return runner_python, Path(os.environ['LOCALAPPDATA'])


def main():
    runner_python, local_app_data = guarded_runner('13')
    if local_app_data != Path(sys.argv[1]):
        raise RuntimeError('Windows Local AppData does not match the runner environment.')
    install_once(ROOT, runner_python, local_app_data)


if __name__ == '__main__':
    main()
