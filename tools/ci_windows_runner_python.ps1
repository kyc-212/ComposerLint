function Assert-PlainCacheItem($item, [bool]$directory) {
    if ($item.PSIsContainer -ne $directory -or
        ($item.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device))) {
        throw "Unexpected file type in the runner tool cache: $($item.FullName)"
    }
}

function Assert-Amd64Executable([string]$path) {
    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($stream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5A4D) {
            throw "Runner Python has no valid DOS executable header: $path"
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadUInt32()
        if ($peOffset -lt 64 -or $peOffset -gt ($stream.Length - 6)) {
            throw "Runner Python has an invalid PE header offset: $path"
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw "Runner Python is not an AMD64 PE executable: $path"
        }
    } finally {
        $reader.Dispose()
    }
}

function Find-CachedRunnerPython([string]$cacheRoot) {
    if ($cacheRoot -cnotmatch '^[A-Za-z]:\\' -or
        [IO.Path]::GetFullPath($cacheRoot).TrimEnd('\') -ine $cacheRoot.TrimEnd('\')) {
        throw 'RUNNER_TOOL_CACHE must be a canonical absolute local drive path.'
    }
    # Validate the ancestor chain before enumerating the cache or reading any binary.
    $root = [IO.Path]::GetPathRoot($cacheRoot)
    $directory = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    Assert-PlainCacheItem $directory $true
    $parts = @($cacheRoot.Substring($root.Length).Split('\', [StringSplitOptions]::RemoveEmptyEntries)) + 'Python'
    foreach ($part in $parts) {
        $directory = Get-Item -LiteralPath (Join-Path $directory.FullName $part) -Force -ErrorAction Stop
        Assert-PlainCacheItem $directory $true
    }
    $versions = Get-ChildItem -LiteralPath $directory.FullName -Directory -Force -ErrorAction Stop |
        Where-Object Name -CMatch '^3\.12\.(0|[1-9][0-9]*)$' |
        Sort-Object { [version]$_.Name } -Descending
    foreach ($version in $versions) {
        Assert-PlainCacheItem $version $true
        try {
            $marker = Get-Item -LiteralPath (Join-Path $version.FullName 'x64.complete') -Force -ErrorAction Stop
        } catch [System.Management.Automation.ItemNotFoundException] {
            continue
        }
        Assert-PlainCacheItem $marker $false
        $architecture = Get-Item -LiteralPath (Join-Path $version.FullName 'x64') -Force -ErrorAction Stop
        Assert-PlainCacheItem $architecture $true
        $executable = Get-Item -LiteralPath (Join-Path $architecture.FullName 'python.exe') -Force -ErrorAction Stop
        Assert-PlainCacheItem $executable $false
        Assert-Amd64Executable $executable.FullName
        return [pscustomobject]@{ Path = $executable.FullName; Version = $version.Name }
    }
    throw 'No complete CPython 3.12 x64 entry exists in the installed runner tool cache.'
}

function Get-VerifiedRunnerPython([string]$cacheRoot) {
    $candidate = Find-CachedRunnerPython $cacheRoot
    $probe = @'
import os, struct, sys, sysconfig
def normalized(path):
    return os.path.normcase(os.path.abspath(path))
expected, version = sys.argv[1:]
actual = ".".join(map(str, sys.version_info[:3]))
if (sys.platform != "win32" or sys.implementation.name != "cpython"
    or struct.calcsize("P") != 8 or sysconfig.get_platform() != "win-amd64"
    or sys.version_info.releaselevel != "final" or actual != version
    or normalized(sys.executable) != normalized(expected)
    or normalized(sys.base_prefix) != normalized(os.path.dirname(expected))):
    raise SystemExit(f"Unexpected runner Python identity: {sys.executable}, {sys.version}, {sysconfig.get_platform()}")
print(actual)
'@
    $reported = & $candidate.Path -I -S -B -c $probe $candidate.Path $candidate.Version
    if ($LASTEXITCODE -ne 0 -or $reported -cne $candidate.Version) {
        throw "CPython identity probe failed for $($candidate.Path)."
    }
    Write-Host "Verified CI-only runner CPython $reported x64 at $($candidate.Path)."
    return $candidate.Path
}
