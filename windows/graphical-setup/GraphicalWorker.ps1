# Loaded by the verified Setup.ps1 runner, on its background runspace.
function Send-SetupMessage([string]$Type, [hashtable]$Fields) {
    $Fields.type = $Type
    $GraphicalSession.events.Enqueue($Fields)
}

function Wait-SetupDecision([string]$Type, [string]$Id, [hashtable]$Fields) {
    $Fields.plan_id = $Id
    Send-SetupMessage $Type $Fields
    $reply = $null
    if (!$GraphicalSession.replies.TryTake([ref]$reply, 600000)) {
        return @{ action = 'cancel'; plan_id = $Id }
    }
    if ($reply.plan_id -cne $Id) { throw 'The response did not match the displayed plan.' }
    return $reply
}

function Request-SetupDecision([string]$Type, [string]$Message) {
    $id = [Guid]::NewGuid().ToString('N')
    $reply = Wait-SetupDecision $Type $id @{ message = $Message }
    return $reply.action -ceq 'approve'
}

function Invoke-GraphicalBackend([string]$Python, [string]$Archive) {
    $entry = Join-Path $PSScriptRoot 'GraphicalBackend.py'
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.Arguments = "-I -S -B -u -X utf8 `"$entry`""
    $start.WorkingDirectory = Split-Path -Parent $Archive
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = [Diagnostics.Process]::Start($start)
    $errorReader = $process.StandardError.ReadToEndAsync()
    $completion = $null
    $cancelled = $false
    try {
        # JSON input is ASCII-escaped explicitly for Windows PowerShell 5.1.
        $config = @{ mode = $Mode; renderer = $RendererBundle; cache = $GraphicalSession.cache; bootstrap = $Archive }
        Write-BackendInput $process $config
        while ($null -ne ($line = $process.StandardOutput.ReadLine())) {
            if ($line.Length -gt 128KB) { throw 'Oversized installer event.' }
            $event = ConvertFrom-Json -InputObject $line
            if ($event.protocol -ne 1) { throw 'Unsupported installer event version.' }
            if ($completion -or $cancelled) { throw 'Unexpected event after setup finished.' }
            switch ($event.type) {
                'plan' {
                    $reply = Wait-SetupDecision 'plan' $event.plan_id @{ result = $event }
                    Write-BackendInput $process $reply
                }
                'choice' {
                    $reply = Wait-SetupDecision 'choice' $event.plan_id @{ message = $event.message }
                    Write-BackendInput $process $reply
                }
                'log' { Send-SetupMessage 'log' @{ message = $event.message } }
                'progress' { Send-SetupMessage 'progress' @{ message = $event.message } }
                'complete' { $completion = $event }
                'cancelled' { $cancelled = $true }
                'error' { Send-SetupMessage 'log' @{ message = $event.message } }
                default { throw 'Unknown installer event.' }
            }
        }
        $process.WaitForExit()
        $diagnostics = $errorReader.GetAwaiter().GetResult()
        if ($diagnostics) { Send-SetupMessage 'log' @{ message = $diagnostics } }
        if ($process.ExitCode -ne 0 -or (!$completion -and !$cancelled)) { return 1 }
        $GraphicalSession.complete = $completion
        return 0
    } finally {
        try { $process.StandardInput.Close() } finally {
            if (!$process.HasExited) {
                # Let the installer reach its own cleanup, including native worker reaping.
                [void]$process.StandardOutput.ReadToEnd()
                $process.WaitForExit()
            }
            $process.Dispose()
        }
    }
}

function Write-BackendInput([Diagnostics.Process]$Process, [hashtable]$InputMessage) {
    $json = ConvertTo-Json -InputObject $InputMessage -Compress
    $ascii = [regex]::Replace($json, '[^\x00-\x7F]', { param($match) '\u{0:x4}' -f [int][char]$match.Value })
    $Process.StandardInput.WriteLine($ascii)
    $Process.StandardInput.Flush()
}
