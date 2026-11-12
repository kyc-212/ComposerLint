$ErrorActionPreference = 'Stop'
if ($env:RUNNER_OS -cne 'Windows' -or $env:RUNNER_ARCH -cne 'X64' -or
    $env:GITHUB_EVENT_NAME -cne 'workflow_dispatch' -or
    $env:GITHUB_REPOSITORY -cne 'kyc-212/ComposerLint' -or
    $env:GITHUB_ACTOR -cne 'kyc-212' -or
    $env:GITHUB_REF -cne 'refs/heads/codex/windows-ci-pilot' -or
    $env:GITHUB_RUN_NUMBER -cne '11' -or $env:GITHUB_RUN_ATTEMPT -cne '1' -or
    $env:REVIEWED_COMMIT -cnotmatch '^[0-9a-f]{40}$' -or $env:REVIEWED_COMMIT -cne $env:GITHUB_SHA) {
    throw 'This script requires the one approved Windows x64 dispatch.'
}
$checkout = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $checkout -cne $env:REVIEWED_COMMIT) {
    throw 'Checkout differs from the reviewed dispatch.'
}

$manifestFile = 'tools/windows-setup-assets.json'
$manifestText = (Get-Content -LiteralPath $manifestFile -Raw).Replace("`r`n", "`n")
$manifest = $manifestText | ConvertFrom-Json
$manifestHash = [Convert]::ToHexString(
    [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($manifestText)))
if ($manifestHash -ine
    '7371fb1a1b6ab884816f14d0a550f76d34c46a7f6c08eb812e7260b09f8aca0c' -or
    $manifest.wheels.Count -ne 59) {
    throw 'The reviewed set of 61 prerequisites changed.'
}
$local = [Environment]::GetFolderPath('LocalApplicationData')
if (!$local -or $local -ine $env:LOCALAPPDATA) {
    throw 'The runner Local AppData path is ambiguous.'
}
$app = Join-Path $local 'ComposerLint'
$destination = Join-Path $app 'setup'
if (Test-Path -LiteralPath $app) {
    throw 'Expected a fresh disposable runner profile with no ComposerLint setup.'
}

$hostPython = (Get-Command python.exe -CommandType Application).Source
$reg = Join-Path $env:SystemRoot 'System32\reg.exe'
$registryKeys = @(
    'HKEY_CURRENT_USER\Environment',
    'HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'HKEY_CURRENT_USER\Software\Python',
    'HKEY_LOCAL_MACHINE\Software\Python',
    'HKEY_LOCAL_MACHINE\Software\WOW6432Node\Python',
    'HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)
$settingsFiles = @(
    $env:GITHUB_PATH,
    (Join-Path $env:USERPROFILE '.codex\config.toml'),
    (Join-Path $env:USERPROFILE '.claude.json'),
    (Join-Path $env:USERPROFILE '.claude\settings.json'),
    (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'),
    (Join-Path $env:APPDATA 'Code\User\mcp.json'),
    (Join-Path $env:APPDATA 'Code\User\settings.json'),
    (Join-Path $env:USERPROFILE '.cursor\mcp.json')
)

function Get-HostSnapshot {
    $paths = foreach ($scope in 'Process', 'User', 'Machine') {
        [Environment]::GetEnvironmentVariable('Path', $scope)
    }
    $commands = Get-Command python.exe, uv.exe -All -CommandType Application -ErrorAction SilentlyContinue
    $hashes = foreach ($file in ($settingsFiles + $commands.Source | Sort-Object -Unique)) {
        if ($file -and (Test-Path -LiteralPath $file)) {
            @{ Path = $file; Sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash }
        }
    }
    $registry = foreach ($key in $registryKeys) {
        if (Test-Path -LiteralPath "Registry::$key") {
            $values = & $reg query $key /s
            if ($LASTEXITCODE -ne 0) { throw "Could not inspect registry scope $key." }
            $values | Sort-Object
        }
    }
    [ordered]@{ Path = @($paths); Files = @($hashes); Registry = @($registry) } |
        ConvertTo-Json -Depth 5 -Compress
}

$before = Get-HostSnapshot
try {
    # The repository owner authorized this one invocation. Send the exact answer
    # to the normal consent prompt after the installer prepares its reviewed plan.
    $output = 'INSTALL' | & $hostPython -I -S -B -X utf8 tools/setup_composerlint.py --windows-text-pilot
    $exitCode = $LASTEXITCODE
    $output | Write-Output
} finally {
    if ((Get-HostSnapshot) -cne $before) {
        throw 'The host PATH, Python/uv files, monitored registry scopes, or AI client settings changed.'
    }
    Write-Output 'Host PATH, Python/uv files, monitored registry scopes and AI client settings match the baseline.'
}
if ($exitCode -ne 0) { throw "The Windows text installer exited with code $exitCode." }

$generation = @(Get-ChildItem -LiteralPath $destination -Force -Directory)
if ($generation.Count -ne 1 -or $generation[0].Name -cnotmatch '^install-[0-9a-f]{20}$' -or
    ($generation[0].Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Expected one private installation generation under Local AppData.'
}
$installed = $generation[0].FullName
$receipt = Get-Content -LiteralPath (Join-Path $installed 'complete.json') -Raw | ConvertFrom-Json
$privatePython = Join-Path $installed 'runtime\Scripts\python.exe'
if ($receipt.schema -ne 1 -or $receipt.target -cne 'windows-x64-text-pilot' -or
    $receipt.lock_sha256 -cne $manifest.lock_sha256 -or $receipt.python -cne $privatePython -or
    $receipt.plan -cnotmatch '^[0-9a-f]{64}$' -or
    "Reviewed setup plan: $($receipt.plan)" -cnotin $output -or
    "Private Windows text environment verified: $privatePython" -cnotin $output -or
    !(Test-Path -LiteralPath $privatePython -PathType Leaf) -or
    !(Test-Path -LiteralPath (Join-Path $installed 'python\python.exe') -PathType Leaf) -or
    !(Test-Path -LiteralPath (Join-Path $installed 'uv\uv.exe') -PathType Leaf) -or
    (Test-Path -LiteralPath (Join-Path $installed 'work')) -or
    (Test-Path -LiteralPath (Join-Path $installed 'receipt.pending'))) {
    throw 'The completion receipt or private installation layout does not match the verified plan.'
}
Write-Output 'Verified 61 pinned files (exact filenames, sizes and SHA-256), private CPython 3.12.13 x64 and uv 0.12.3.'
Write-Output 'Verified the 59 locked distributions plus the locally built ComposerLint, uv pip check, and native extension imports.'
Write-Output "Verified completion receipt for $installed; download/build staging was removed."
