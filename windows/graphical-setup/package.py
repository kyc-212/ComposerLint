"""Build a local graphical preview from a verified standalone terminal package."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import stat
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile


HERE = Path(__file__).resolve().parent
ARCHIVE = 'ComposerLint-Graphical-Setup-Windows-x64-preview.zip'
NAME = re.compile(r'[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*')
DIGEST = re.compile(r'[0-9a-f]{64}')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(files):
    return ''.join(f'{sha(data)} *{name}\n' for name, data in sorted(files.items())).encode('ascii')


def read_package(archive):
    with ZipFile(archive) as bundle:
        files = {}
        seen = set()
        entries = bundle.infolist()
        if len(entries) > 1500 or sum(item.file_size for item in entries) > 64 * 1024**2:
            raise ValueError('Unexpected package size.')
        for item in entries:
            name = item.filename
            if (not NAME.fullmatch(name) or len(name) > 200 or name.casefold() in seen
                    or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                    or any(part.endswith('.') or
                           re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])', part.split('.')[0])
                           for part in name.split('/'))):
                raise ValueError(f'Unsafe or duplicate package member: {name}')
            seen.add(name.casefold())
            files[name] = bundle.read(item)
    expected = files.pop('FILES.sha256', None)
    if expected is None or sorted(expected.splitlines()) != sorted(inventory(files).splitlines()):
        raise ValueError('The input package does not match its file manifest.')
    return files


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'The reviewed terminal integration point changed: {old.splitlines()[0]}')
    return source.replace(old, new, 1)


def wire_launcher(source):
    """Keep the terminal launcher and add explicit callbacks at its consent boundary."""
    source = replace(source, '    [string]$OfflineAssets\n', '''    [string]$OfflineAssets,
    [hashtable]$GraphicalSession
''')
    source = replace(source, "$script:SetupExitCode = 1\n", '''$script:SetupExitCode = 1
if ($GraphicalSession) { . (Join-Path $PSScriptRoot 'GraphicalWorker.ps1') }
''')
    start = source.index('$PythonName =')
    end = source.index('# Per-file elapsed budget', start)
    source = source[:start] + source[end:]
    source = replace(source, '    Test-Package $root\n', r'''    Test-Package $root
    $pins = Get-Content -LiteralPath (Join-Path $root 'tools/windows-setup-assets.json') -Raw | ConvertFrom-Json
    $pin = $pins.bootstrap.python
    $PythonName = [string]$pin.filename
    $PythonUrl = [string]$pin.url
    $PythonHash = [string]$pin.sha256
    $PythonSize = [int]$pin.size
    if ($PythonName -cnotmatch '^cpython-(\d+\.\d+\.\d+)\+[0-9]+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$' -or
        $PythonHash -cnotmatch '^[a-f0-9]{64}$' -or $PythonSize -lt 1 -or $PythonSize -gt 64MB) {
        throw 'Invalid Windows Python bootstrap pin.'
    }
    $PythonVersion = ($PythonName -split '[-+]')[1]
    if ($PythonUrl -cnotmatch '^https://github\.com/astral-sh/python-build-standalone/releases/download/[0-9]+/[A-Za-z0-9_%+.-]+$') {
        throw 'Invalid Windows Python bootstrap origin.'
    }
''')
    source = replace(source, '    if ($OfflineAssets) {\n        $OfflineAssets =', '''    $cachedPython = ''
    if ($GraphicalSession -and $GraphicalSession.cache) {
        $cache = Get-LocalDirectory $GraphicalSession.cache
        $candidate = Join-Path $cache $PythonName
        if (Test-Path -LiteralPath $candidate) { $cachedPython = $candidate }
    }
    if ($OfflineAssets) {
        $OfflineAssets =''')
    source = replace(source, "    if ((Read-Host 'Type INSTALL to start the private Python bootstrap, or press Enter to cancel') -cne 'INSTALL') {", '''    $approved = if ($GraphicalSession) {
        $action = if ($cachedPython) { 'Reuse and verify the local file' } else { 'Download and verify the missing file' }
        $description = @"
$action before reviewing the application install.

$PythonName
$PythonSize bytes
$PythonUrl
SHA-256: $PythonHash

Private staging: $local
"@
        Request-SetupDecision 'bootstrap_plan' $description
    } else {
        (Read-Host 'Type INSTALL to start the private Python bootstrap, or press Enter to cancel') -ceq 'INSTALL'
    }
    if (!$approved) {''')
    source = replace(source, 'pinned CPython 3.12.13 x64 archive ($PythonSize bytes)', 'pinned CPython $PythonVersion x64 archive ($PythonSize bytes)')
    source = replace(source, "    $sid = $identity.User.Value\n", '''    if ($GraphicalSession) { Send-SetupMessage 'progress' @{ message = 'Preparing and verifying private Python...' } }
    $sid = $identity.User.Value
''')
    source = replace(source, '''        if ($OfflineAssets) {
            Write-Host 'Reading and verifying the offline Python archive...'
            $offlinePython = Get-Item -LiteralPath (Join-Path $OfflineAssets $PythonName) -Force
''', '''        if ($OfflineAssets -or $cachedPython) {
            Write-Host 'Reading and verifying the local Python archive...'
            $localArchive = if ($cachedPython) { $cachedPython } else { Join-Path $OfflineAssets $PythonName }
            $offlinePython = Get-Item -LiteralPath $localArchive -Force
''')
    source = replace(source, "$version -cne 'cpython 3.12.13 8'", '$version -cne "cpython $PythonVersion 8"')
    source = replace(source, '''            & $python -I -S -B -u -X utf8 $entry @setupArgs
            $result = $LASTEXITCODE
''', '''            if ($GraphicalSession) {
                $result = Invoke-GraphicalBackend $python $archive
            } else {
                & $python -I -S -B -u -X utf8 $entry @setupArgs
                $result = $LASTEXITCODE
            }
''')
    return replace(source, '''    exit $script:SetupExitCode
} catch {
''', '''    if (!$GraphicalSession) { exit $script:SetupExitCode }
    if ($script:SetupExitCode -ne 0) { throw 'Setup stopped. Review the local log, then retry.' }
    if ($GraphicalSession.complete) {
        Send-SetupMessage 'complete' @{ result = $GraphicalSession.complete }
    } else {
        Send-SetupMessage 'cancelled' @{ message = 'Setup cancelled.' }
    }
} catch {
    if ($GraphicalSession) {
        Send-SetupMessage 'error' @{ message = $_.Exception.Message }
        return
    }
''').encode('ascii')


def wire_backend(source):
    source = replace(source, '            offline_assets=None):', '            offline_assets=None, review=None, complete=None):')
    source = replace(source, '    consent = choice(f\'Type {expected_consent} to approve this private setup plan, or press Enter to cancel: \')',
                     '''    consent = (
        expected_consent if review(plan, assets) else ''
    ) if review is not None else choice(
        f'Type {expected_consent} to approve this private setup plan, or press Enter to cancel: ')
'''.rstrip())
    return replace(source, "    print('MCP arguments: -I -B -X utf8 -m composerlint.server')\n    return 0",
                   """    print('MCP arguments: -I -B -X utf8 -m composerlint.server')
    if complete is not None:
        complete(runtime, plan)
    return 0""").encode('utf-8')


def graphical_files(files, baseline_hash):
    backend = 'src/composerlint/windows_setup.py'
    if ('src/composerlint/windows_renderer_native.py' not in files
            or 'tools/windows-full-artifacts.json' in files):
        raise ValueError('Use a standalone Windows terminal package.')
    files['Setup.ps1'] = wire_launcher(files['Setup.ps1'].decode('utf-8'))
    files[backend] = wire_backend(files[backend].decode('utf-8'))

    resources = ('SetupGUI.ps1', 'SetupGUI.xaml', 'GraphicalWorker.ps1', 'GraphicalBackend.py',
                 'Open-ComposerLint-Setup.cmd', 'GRAPHICAL-SETUP.md')
    for name in resources:
        if name in files:
            raise ValueError(f'Input already contains {name}.')
        data = (HERE / name).read_bytes()
        if name.endswith('.cmd'):
            data = data.replace(b'\n', b'\r\n')
        files[name] = data
    files['setup-icon.png'] = (HERE.parents[1] / 'linux/composerlint.png').read_bytes()
    version = tomllib.loads(files['pyproject.toml'].decode())['project']['version']
    files['graphical-preview.json'] = json.dumps(
        dict(version=version, terminal_zip_sha256=baseline_hash)).encode('ascii') + b'\n'
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('terminal_zip', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not DIGEST.fullmatch(args.sha256) or args.terminal_zip.stat().st_size > 32 * 1024**2:
        parser.error('Input must match the supplied terminal ZIP SHA-256.')
    source = args.terminal_zip.read_bytes()
    if sha(source) != args.sha256:
        parser.error('Input must match the supplied terminal ZIP SHA-256.')
    files = graphical_files(read_package(BytesIO(source)), args.sha256)
    files['FILES.sha256'] = inventory(files)
    args.output.mkdir(parents=True, exist_ok=True)
    candidate = args.output / ARCHIVE
    with ZipFile(candidate, 'x', compression=ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in sorted(files.items()):
            bundle.writestr(name, data)
    expected = files.pop('FILES.sha256')
    if inventory(read_package(candidate)) != expected:
        raise ValueError('Generated ZIP failed independent readback.')
    checksum = candidate.with_suffix('.zip.sha256')
    with checksum.open('x', encoding='ascii') as stream:
        stream.write(f'{sha(candidate.read_bytes())} *{candidate.name}\n')
    print(candidate)
    print(checksum.read_text(encoding='ascii').strip())


if __name__ == '__main__':
    main()
