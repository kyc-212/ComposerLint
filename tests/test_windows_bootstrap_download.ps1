#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Load only the production body-copy function and deadline, without running setup
# or downloading anything. Simulate a monotonic clock driven by incoming chunks.
$path = Join-Path $PSScriptRoot '../windows/terminal-package/Setup.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors -join [Environment]::NewLine) }
foreach ($part in @($ast.EndBlock.Statements | Where-Object {
    ($_ -is [Management.Automation.Language.AssignmentStatementAst] -and
        $_.Left.Extent.Text -ceq '$DownloadDeadlineSeconds') -or
    ($_ -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $_.Name -ceq 'Copy-PinnedPythonBody')
})) {
    . ([scriptblock]::Create($part.Extent.Text))
}

function Test-Transfer([int]$PinnedSize, [int[]]$Times, [int]$EofAt, [string]$Failure,
                       [int]$ExpectedBytes, [int]$ExpectedTime) {
    $PythonSize = $PinnedSize
    $clock = [pscustomobject]@{ Elapsed = [pscustomobject]@{ TotalSeconds = 0 } }
    $inputStream = [pscustomobject]@{ Clock = $clock; Times = $Times; EofAt = $EofAt; Index = 0 }
    Add-Member -InputObject $inputStream -MemberType ScriptMethod -Name Read -Value {
        param($Buffer, $Offset, $Count)
        if ($this.Index -eq $this.Times.Count) {
            $this.Clock.Elapsed.TotalSeconds = $this.EofAt
            $this.Index++
            return 0
        }
        if ($this.Index -gt $this.Times.Count) { throw 'Read again after EOF.' }
        $this.Clock.Elapsed.TotalSeconds = $this.Times[$this.Index]
        $this.Index++
        $Buffer[$Offset] = 65
        return 1
    }
    $output = [IO.MemoryStream]::new()
    try {
        $message = ''
        try {
            Copy-PinnedPythonBody $inputStream $output $clock
        } catch { $message = $_.Exception.Message }
        if ($message -cne $Failure -or $output.Length -ne $ExpectedBytes -or
            $clock.Elapsed.TotalSeconds -ne $ExpectedTime) {
            throw "Unexpected transfer result: time=$($clock.Elapsed.TotalSeconds), bytes=$($output.Length), error='$message'."
        }
        if (!$Failure -and [Text.Encoding]::ASCII.GetString($output.ToArray()) -cne ('A' * $PinnedSize)) {
            throw 'Successful transfer returned the wrong bytes.'
        }
    } finally { $output.Dispose() }
}

$active = @(1..89 | ForEach-Object { 10 * $_ })
$timedOut = 'The Python download exceeded its reviewed time limit.'
Test-Transfer 89 $active 899 '' 89 899
Test-Transfer 90 ($active + @(900)) 901 $timedOut 89 900
Test-Transfer 89 $active 900 $timedOut 89 900
Test-Transfer 3 @(10, 20) 21 'The Python download did not match its reviewed size.' 2 21
Test-Transfer 1 @(10, 20) 21 'The Python download exceeded its reviewed size.' 1 20
Write-Output 'PASS: active bootstrap transfer, deadline on data and EOF, and exact size refusals.'
