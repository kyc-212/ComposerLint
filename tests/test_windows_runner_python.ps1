$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../tools/ci_windows_runner_python.ps1')

function New-TestPython([string]$cache, [string]$version, [uint16]$machine, [bool]$complete) {
    $folder = Join-Path $cache "Python/$version/x64"
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    if ($complete) {
        [IO.File]::WriteAllBytes("$folder.complete", [byte[]]::new(0))
    }
    $path = Join-Path $folder 'python.exe'
    $header = [byte[]]::new(70)
    $header[0] = 0x4D
    $header[1] = 0x5A
    $header[0x3C] = 64
    $header[64] = 0x50
    $header[65] = 0x45
    [BitConverter]::GetBytes($machine).CopyTo($header, 68)
    [IO.File]::WriteAllBytes($path, $header)
    return $path
}

function Assert-SelectionFailure([string]$cache, [string]$message) {
    try {
        $null = Find-CachedRunnerPython $cache
    } catch {
        if ($_.Exception.Message -like "*$message*") { return }
        throw
    }
    throw "Expected runner Python selection to fail with '$message'."
}

$scratch = Join-Path $env:RUNNER_TEMP ('runner-python-control-' + [guid]::NewGuid().ToString('N'))
$oldPath = $env:PATH
try {
    $cache = Join-Path $scratch 'cache'
    $windowsApps = Join-Path $scratch 'Microsoft/WindowsApps'
    New-Item -ItemType Directory -Path $windowsApps -Force | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $windowsApps 'python.exe'), [byte[]]::new(0))
    $env:PATH = "$windowsApps;$oldPath"
    $null = New-TestPython $cache '3.12.9' 0x8664 $true
    $expected = New-TestPython $cache '3.12.10' 0x8664 $true
    $null = New-TestPython $cache '3.12.11' 0x8664 $false
    if ((Find-CachedRunnerPython $cache).Path -ine [IO.Path]::GetFullPath($expected)) {
        throw 'The selector did not choose the newest complete cache entry independently of PATH.'
    }
    Write-Output 'PASS: cache selection ignores a PATH shadow and incomplete entries.'
    $null = New-TestPython $cache '3.12.12' 0xAA64 $true
    Assert-SelectionFailure $cache 'not an AMD64 PE executable'
    Write-Output 'PASS: a completed entry with the wrong PE architecture is refused.'
    $unsafe = Join-Path $scratch 'redirected-cache'
    New-Item -ItemType Junction -Path $unsafe -Target $cache | Out-Null
    try {
        Assert-SelectionFailure $unsafe 'Unexpected file type'
        Write-Output 'PASS: a cache junction is refused before selecting an executable.'
    } finally {
        [IO.Directory]::Delete($unsafe)
    }
} finally {
    $env:PATH = $oldPath
    if ([IO.Directory]::Exists($scratch)) { [IO.Directory]::Delete($scratch, $true) }
}
