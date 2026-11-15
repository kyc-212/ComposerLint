#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$path = Join-Path $PSScriptRoot '../windows/terminal-package/Setup.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors -join [Environment]::NewLine) }
foreach ($part in @($ast.EndBlock.Statements | Where-Object {
    ($_ -is [Management.Automation.Language.AssignmentStatementAst] -and
        $_.Left.Extent.Text -ceq '$RendererNames') -or
    ($_ -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $_.Name -cin @('Assert-Plain', 'Test-Package', 'Get-PlainFiles', 'Test-RendererBundle'))
})) {
    . ([scriptblock]::Create($part.Extent.Text))
}

$root = Join-Path $env:RUNNER_TEMP ('cl-manifest-test-' + [guid]::NewGuid().ToString('N'))
$bundle = Join-Path $root 'renderer'
$package = Join-Path $root 'setup'
[void][IO.Directory]::CreateDirectory($bundle)
[void][IO.Directory]::CreateDirectory($package)
$manifestPath = Join-Path $package 'windows-full-artifacts.json'
$script:HeldFiles = [Collections.Generic.List[IO.FileStream]]::new()
function Close-FixtureFiles {
    foreach ($stream in $script:HeldFiles) { $stream.Dispose() }
    $script:HeldFiles.Clear()
}
try {
    $pins = [ordered]@{}
    foreach ($name in $RendererNames) {
        $file = Join-Path $bundle $name
        [IO.File]::WriteAllText($file, 'fixture')
        $pins[$name] = @{ size = 7; sha256 = (Get-FileHash -LiteralPath $file).Hash.ToLowerInvariant() }
    }
    $manifest = @{ schema = 1; files = $pins } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($manifestPath, $manifest)
    $expected = (Get-FileHash -LiteralPath $manifestPath).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $package 'FILES.sha256'), "$expected *windows-full-artifacts.json`n")
    Test-Package $package
    if ((Test-RendererBundle $bundle $manifestPath) -cne $expected) {
        throw 'Launcher did not return the digest of the held package manifest.'
    }
    Close-FixtureFiles

    # A same-size substitution must fail even if the expected name remains.
    [IO.File]::WriteAllText((Join-Path $bundle 'build-provenance.json'), 'changed')
    $message = ''
    try {
        [void](Test-RendererBundle $bundle $manifestPath)
    } catch { $message = $_.Exception.Message }
    if ($message -cnotlike '*differs from the reviewed manifest*') { throw "Unexpected result: $message" }
    Close-FixtureFiles

    [IO.File]::WriteAllText($manifestPath, $manifest + ' ')
    $message = ''
    try { Test-Package $package } catch { $message = $_.Exception.Message }
    if ($message -cnotlike '*Unexpected or changed package file*') { throw "Unexpected result: $message" }
} finally {
    Close-FixtureFiles
    [IO.Directory]::Delete($root, $true)
}
Write-Output 'PASS: launcher uses the verified manifest and refuses substituted provenance or changed manifest bytes.'
