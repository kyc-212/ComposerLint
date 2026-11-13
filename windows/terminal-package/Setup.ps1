#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Text', 'Full')][string]$Mode,
    [string]$RendererBundle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:SetupExitCode = 1

$PythonName = 'cpython-3.12.13+20260807-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$PythonUrl = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260807/cpython-3.12.13%2B20260807-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$PythonHash = '18bcc65b17921806b72cdc88bcf000bf67a2c99a8fc381fe1629f2b9ba56858d'
$PythonSize = 21962247
# Per-file elapsed budget for slow, active transfers; network timeouts are separate.
$DownloadDeadlineSeconds = 900
$PatchedHash = '263bda9dacfd11d64439b08ae6fee2553b34b90630a9208d7542f2e62b1fd5cf'
$RendererFiles = [ordered]@{
    'verovio-6.3.0-cp310-abi3-win_amd64.whl' = 'cc8bab65570ccb38d6a440fa13d3e4820df34ee9ca483decdbe8371c4291b1a8'
    'verovio-6.3.0+composerlint.tie1-cp310-abi3-win_amd64.whl' = $PatchedHash
    'verovio-6.3.0.tar.gz' = '33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c'
    'verovio-6.3.0-deferred-tie-stops.patch' = '10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60'
    'build-provenance.json' = '651cc6bc0f10e4852228c850277e2523635ec4952b9973c31aaeb47b490adb43'
}

function Assert-Plain($Item, [bool]$Directory) {
    if ($Item.PSIsContainer -ne $Directory -or
        ($Item.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device))) {
        throw "Unexpected or redirected file or folder: $($Item.FullName)"
    }
}

function Get-LocalDirectory([string]$Path) {
    if ($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.Substring(2) -match '[/:]' -or
        [IO.Path]::GetFullPath($Path).TrimEnd('\') -cne $Path.TrimEnd('\')) {
        throw "Use a canonical absolute local Windows path: $Path"
    }
    $root = [IO.Path]::GetPathRoot($Path)
    $drive = [IO.DriveInfo]::new($root)
    if ($drive.DriveType -ne [IO.DriveType]::Fixed -or $drive.DriveFormat -ne 'NTFS') {
        throw "A fixed local NTFS drive is required: $root"
    }
    $item = Get-Item -LiteralPath $root -Force
    Assert-Plain $item $true
    foreach ($part in $Path.Substring($root.Length).Split('\', [StringSplitOptions]::RemoveEmptyEntries)) {
        if ($part -match '[ .]$') { throw "Ambiguous path component: $part" }
        $item = Get-Item -LiteralPath (Join-Path $item.FullName $part) -Force
        Assert-Plain $item $true
    }
    return $item.FullName
}

function Get-PlainFiles([string]$Root, [string]$Relative = '', [int]$Depth = 0) {
    if ($Depth -gt 32) { throw "Unexpected directory depth under $Root" }
    $directory = if ($Relative) { Join-Path $Root $Relative } else { $Root }
    foreach ($item in (Get-ChildItem -LiteralPath $directory -Force)) {
        Assert-Plain $item $item.PSIsContainer
        $name = if ($Relative) { $Relative + '/' + $item.Name } else { $item.Name }
        if ($item.PSIsContainer) {
            Get-PlainFiles $Root $name ($Depth + 1)
        } else {
            [pscustomobject]@{ Name = $name; Path = $item.FullName }
        }
    }
}

function Test-Package([string]$Root) {
    $list = Get-Item -LiteralPath (Join-Path $Root 'FILES.sha256') -Force
    Assert-Plain $list $false
    if ($list.Length -gt 256KB) { throw 'Unexpected package manifest size.' }
    $expected = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
    foreach ($line in [IO.File]::ReadLines($list.FullName)) {
        if ($line -cnotmatch '^([a-f0-9]{64}) \*([A-Za-z0-9_./+-]+)$') {
            throw 'Invalid package manifest row.'
        }
        $expected.Add($Matches[2], $Matches[1])
    }
    foreach ($file in (Get-PlainFiles $Root)) {
        if ($file.Name -ceq 'FILES.sha256') { continue }
        if (!$expected.ContainsKey($file.Name)) { throw "Unexpected package file: $($file.Name)" }
        $stream = [IO.File]::Open($file.Path, 'Open', 'Read', 'Read')
        $script:HeldFiles.Add($stream)
        if ((Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected[$file.Name]) {
            throw "Unexpected or changed package file: $($file.Name)"
        }
        [void]$expected.Remove($file.Name)
    }
    if ($expected.Count) { throw 'Package extraction is incomplete.' }
}

function Copy-PinnedPythonBody($InputStream, $OutputStream, $Clock) {
    $buffer = New-Object byte[] 65536
    $total = 0
    while ($true) {
        if ($Clock.Elapsed.TotalSeconds -ge $DownloadDeadlineSeconds) {
            throw 'The Python download exceeded its reviewed time limit.'
        }
        $count = $InputStream.Read($buffer, 0, $buffer.Length)
        # A blocking read can cross the deadline, including the EOF read.
        if ($Clock.Elapsed.TotalSeconds -ge $DownloadDeadlineSeconds) {
            throw 'The Python download exceeded its reviewed time limit.'
        }
        if ($count -eq 0) { break }
        $total += $count
        if ($total -gt $PythonSize) { throw 'The Python download exceeded its reviewed size.' }
        $OutputStream.Write($buffer, 0, $count)
    }
    if ($total -ne $PythonSize) { throw 'The Python download did not match its reviewed size.' }
}

function Get-PinnedPython([string]$Path) {
    $uri = [Uri]$PythonUrl
    $response = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        for ($hop = 0; $hop -le 3; $hop++) {
            $request = [Net.HttpWebRequest][Net.WebRequest]::Create($uri)
            $request.AllowAutoRedirect = $false
            $request.UseDefaultCredentials = $false
            $request.Timeout = 20000
            $request.ReadWriteTimeout = 20000
            $response = [Net.HttpWebResponse]$request.GetResponse()
            $code = [int]$response.StatusCode
            if ($code -eq 200) { break }
            if ($code -notin @(301,302,303,307,308) -or $hop -eq 3) { throw 'Unexpected download response.' }
            $uri = [Uri]::new($uri, $response.Headers['Location'])
            $response.Dispose()
            $response = $null
            if ($uri.Scheme -cne 'https' -or $uri.Authority -cne 'release-assets.githubusercontent.com' -or
                $uri.UserInfo -or $uri.Fragment) { throw 'Unexpected download redirect.' }
        }
        if ($response.ContentLength -ne -1 -and $response.ContentLength -ne $PythonSize) {
            throw 'Unexpected Python archive size.'
        }
        $inputStream = $response.GetResponseStream()
        try {
            $outputStream = [IO.File]::Open($Path, 'CreateNew', 'Write', 'None')
            try {
                Copy-PinnedPythonBody $inputStream $outputStream $clock
            } finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
    } catch {
        # Do not expose signed redirect URLs or proxy information.
        throw 'Pinned Python transfer failed. Check connectivity or local network policy and retry.'
    } finally {
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Assert-PrivateRoot([string]$Path, [string]$Sid) {
    [void](Get-LocalDirectory $Path)
    $acl = Get-Acl -LiteralPath $Path
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $Sid -or
        !$acl.AreAccessRulesProtected -or $rules.Count -ne 2 -or
        @($rules | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique).Count -ne 2) {
        throw 'Bootstrap staging is not private.'
    }
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -cnotin @($Sid, 'S-1-5-18') -or $rule.IsInherited -or
            $rule.FileSystemRights -ne 'FullControl' -or $rule.AccessControlType -ne 'Allow' -or
            [int]$rule.InheritanceFlags -ne 3 -or $rule.PropagationFlags -ne 'None') {
            throw 'Bootstrap staging permissions are unexpected.'
        }
    }
}

function Start-Setup {
    if ($PSVersionTable.PSEdition -ne 'Desktop' -or ![Environment]::Is64BitProcess -or
        $env:PROCESSOR_ARCHITECTURE -cne 'AMD64' -or $env:PROCESSOR_ARCHITEW6432) {
        throw 'Open native x64 Windows PowerShell 5.1 as your normal user.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Close the elevated window and open Windows PowerShell as your normal user.'
    }
    $root = Get-LocalDirectory $PSScriptRoot
    $tar = Join-Path ([Environment]::SystemDirectory) 'tar.exe'
    Assert-Plain (Get-Item -LiteralPath $tar -Force) $false
    $local = Get-LocalDirectory ([Environment]::GetFolderPath('LocalApplicationData', 'DoNotVerify'))
    Write-Host 'ComposerLint | Windows x64 private test | Run #24 source'
    if (!$Mode) {
        $selection = Read-Host '[1] Text analysis only  [2] Full (requires separate Run #24 renderer bundle)  [Enter] Cancel'
        switch ($selection) {
            '1' { $Mode = 'Text' }
            '2' { $Mode = 'Full' }
            default { Write-Host 'Cancelled. No downloads or setup writes.'; $script:SetupExitCode = 0; return }
        }
    }
    if ($Mode -eq 'Full') {
        if (!$RendererBundle) {
            throw 'Full mode requires -RendererBundle with the absolute path to the separately verified Run #24 bundle. This ZIP does not include it.'
        }
        $bundle = Get-LocalDirectory $RendererBundle
        $items = @(Get-ChildItem -LiteralPath $bundle -Force)
        if ($items.Count -ne $RendererFiles.Count) { throw 'Unexpected renderer bundle inventory.' }
        foreach ($item in $items) {
            Assert-Plain $item $false
            if ($item.Name -cnotin $RendererFiles.Keys) { throw "Unexpected renderer file: $($item.Name)" }
            $stream = [IO.File]::Open($item.FullName, 'Open', 'Read', 'Read')
            $script:HeldFiles.Add($stream)
            if ($stream.Length -gt 32MB -or
                (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant() -cne $RendererFiles[$item.Name]) {
                throw "Renderer bundle differs from Run #24: $($item.Name)"
            }
        }
        $setupArgs = @('--windows-full-pilot', '--renderer-bundle', $bundle, '--renderer-sha256', $PatchedHash)
    } else {
        if ($RendererBundle) { throw '-RendererBundle is only for Full mode.' }
        $setupArgs = @('--windows-text-pilot')
    }

    Write-Host "Checking extracted package files ($Mode mode)..."
    Test-Package $root
    Write-Host "First step: download the pinned CPython 3.12.13 x64 archive ($PythonSize bytes)."
    Write-Host "SHA-256: $PythonHash"
    Write-Host "A temporary private folder will be created under $local. No global tools or settings will be changed."
    Write-Host 'The reviewed installer will show its own plan and ask for INSTALL again.'
    if ((Read-Host 'Type INSTALL to start the private Python bootstrap, or press Enter to cancel') -cne 'INSTALL') {
        Write-Host 'Cancelled. No downloads or setup writes.'
        $script:SetupExitCode = 0
        return
    }

    $sid = $identity.User.Value
    $scratch = Join-Path $local ('ComposerLint-bootstrap-' + [Guid]::NewGuid().ToString('N'))
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetSecurityDescriptorSddlForm("O:${sid}D:P(A;OICI;FA;;;${sid})(A;OICI;FA;;;SY)")
    if (Test-Path -LiteralPath $scratch) { throw 'Unexpected existing bootstrap directory.' }
    $safeToRemove = $false
    try {
        [void][IO.Directory]::CreateDirectory($scratch, $acl)
        Assert-PrivateRoot $scratch $sid
        $archive = Join-Path $scratch $PythonName
        Write-Host 'Downloading and verifying the pinned Python archive...'
        Get-PinnedPython $archive
        # Hold the exact archive against replacement or writes through extraction.
        $held = [IO.File]::Open($archive, 'Open', 'Read', 'Read')
        try {
            $digest = (Get-FileHash -InputStream $held -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($held.Length -ne $PythonSize -or $digest -cne $PythonHash) { throw 'Python archive integrity check failed.' }
            Write-Host 'Extracting verified Python into private staging...'
            Push-Location -LiteralPath $scratch
            try {
                & $tar -xzf $archive -C $scratch --no-same-owner --no-same-permissions --no-acls --no-xattrs --no-fflags
                if ($LASTEXITCODE -ne 0) { throw "Windows tar failed ($LASTEXITCODE)." }
            } finally { Pop-Location }
        } finally { $held.Dispose() }
        $bootstrapFiles = @(Get-PlainFiles $scratch | ForEach-Object Name | Sort-Object -CaseSensitive)
        if (@($bootstrapFiles | Where-Object { $_ -cne $PythonName -and $_ -cnotlike 'python/*' }).Count) {
            throw 'Unexpected file outside the verified Python archive layout.'
        }
        $python = Join-Path $scratch 'python\python.exe'
        Assert-Plain (Get-Item -LiteralPath $python -Force) $false
        $entry = Join-Path $root 'tools\setup_composerlint.py'
        # Never supply consent on stdin; the reviewed backend owns its prompt and transaction.
        Push-Location -LiteralPath $scratch
        try {
            $probe = "import struct,sys; print(sys.implementation.name, sys.version.split()[0], struct.calcsize('P'))"
            $version = & $python -I -S -B -c $probe
            if ($LASTEXITCODE -ne 0 -or $version -cne 'cpython 3.12.13 8') {
                throw 'Unexpected bootstrap Python identity.'
            }
            & $python -I -S -B -u -X utf8 $entry @setupArgs
            $result = $LASTEXITCODE
            $safeToRemove = ($result -eq 0)
        } finally { Pop-Location }
    } finally {
        if ($safeToRemove) {
            try {
                Assert-PrivateRoot $scratch $sid
                $remaining = @(Get-PlainFiles $scratch | ForEach-Object Name | Sort-Object -CaseSensitive)
                if (Compare-Object -ReferenceObject $bootstrapFiles -DifferenceObject $remaining -CaseSensitive) {
                    throw 'Unexpected bootstrap file inventory after setup.'
                }
                [IO.Directory]::Delete($scratch, $true)
            } catch {
                Write-Warning "Safe bootstrap cleanup could not finish. Preserve and report: $scratch"
                $result = 1
            }
        } elseif (Test-Path -LiteralPath $scratch) {
            Write-Warning "Bootstrap staging preserved after a failure or interruption: $scratch"
        }
    }
    $script:SetupExitCode = $result
}

$script:HeldFiles = [Collections.Generic.List[IO.FileStream]]::new()
try {
    Start-Setup
    exit $script:SetupExitCode
} catch {
    Write-Error -ErrorAction Continue "Setup stopped: $($_.Exception.Message)"
    exit 1
} finally {
    foreach ($stream in $script:HeldFiles) { $stream.Dispose() }
}
