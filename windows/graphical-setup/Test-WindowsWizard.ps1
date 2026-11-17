#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Desktop' -or
    [Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA' -or
    $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'Run this check with 64-bit Windows PowerShell 5.1 in STA mode.'
}

function Assert-Check([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

function Get-CheckedScript([string]$Name) {
    $tokens = $null
    $errors = $null
    $syntax = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot $Name), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors -join [Environment]::NewLine) }
    return $syntax
}

function Assert-InBounds($Element, $Surface, [Windows.Size]$Size) {
    Assert-Check ($null -ne $Element) 'An expected wizard control is absent.'
    $bounds = $Element.TransformToAncestor($Surface).TransformBounds(
        [Windows.Rect]::new($Element.RenderSize))
    $visible = $bounds.Width -gt 0 -and $bounds.Height -gt 0 -and
               $bounds.Left -ge 0 -and $bounds.Top -ge 0 -and
               $bounds.Right -le $Size.Width -and $bounds.Bottom -le $Size.Height
    Assert-Check $visible "$($Element.Name) was outside the wizard at $Size."
}

function Assert-IconRenders($Icon, [int]$Dpi) {
    $size = $Icon.RenderSize
    $width = [int][Math]::Ceiling($size.Width * $Dpi / 96)
    $height = [int][Math]::Ceiling($size.Height * $Dpi / 96)
    $rectangle = [Windows.Rect]::new($size)
    $brush = [Windows.Media.VisualBrush]::new($Icon)
    $brush.ViewboxUnits = 'Absolute'
    $brush.Viewbox = $rectangle
    $visual = [Windows.Media.DrawingVisual]::new()
    $drawing = $visual.RenderOpen()
    try { $drawing.DrawRectangle($brush, $null, $rectangle) } finally { $drawing.Close() }
    $artwork = [Windows.Media.Imaging.RenderTargetBitmap]::new(
        $width, $height, $Dpi, $Dpi, [Windows.Media.PixelFormats]::Pbgra32)
    $artwork.Render($visual)
    $pixels = [byte[]]::new($width * $height * 4)
    $artwork.CopyPixels($pixels, $width * 4, 0)
    $ink = 0
    for ($offset = 3; $offset -lt $pixels.Length; $offset += 4) {
        if ($pixels[$offset] -ge 128) { $ink++ }
    }
    $coverage = $ink / ($width * $height)
    $distinct = $coverage -gt 0.05 -and $coverage -lt 0.65
    Assert-Check $distinct "$($Icon.Name) did not render as a distinct icon at $Dpi DPI (coverage $coverage)."
}

$syntax = Get-CheckedScript 'SetupGUI.ps1'
[void](Get-CheckedScript 'GraphicalWorker.ps1')
Write-Host 'Windows PowerShell 5.1 parsed both wizard scripts.'

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$markup = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'SetupGUI.xaml'))
$window = [Windows.Markup.XamlReader]::Parse($markup)
try {
    Assert-Check ($window -is [Windows.Window]) 'Wizard markup did not create a WPF window.'
    $controls = $syntax.FindAll({
        param($node)
        $node -is [Management.Automation.Language.MemberExpressionAst] -and
        $node.Expression -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Expression.VariablePath.UserPath -eq 'ui' -and
        $node.Member -is [Management.Automation.Language.StringConstantExpressionAst]
    }, $true) | ForEach-Object { $_.Member.Value } | Sort-Object -Unique
    foreach ($name in $controls) {
        Assert-Check ($null -ne $window.FindName($name)) "Wizard control is missing: $name"
    }
    Assert-Check ($window.FindName('Full').IsChecked) 'Full mode should start selected.'
    $window.FindName('TextOnly').IsChecked = $true
    Assert-Check (!$window.FindName('Full').IsChecked) 'The two setup modes should be mutually exclusive.'
    foreach ($size in @([Windows.Size]::new(1100, 820), [Windows.Size]::new(750, 520))) {
        $surface = $window.Content
        $surface.Measure($size)
        $surface.Arrange([Windows.Rect]::new($size))
        $surface.UpdateLayout()
        Assert-InBounds ($window.FindName('Primary')) $surface $size
        foreach ($name in @('FullIcon', 'TextOnlyIcon', 'PrivacyIcon')) {
            $icon = $window.FindName($name)
            Assert-InBounds $icon $surface $size
            Assert-IconRenders $icon 96
            Assert-IconRenders $icon 144
        }
    }
    Write-Host 'WPF loaded the production layout. Control references, mode switching and primary action layout checks passed.'
    Write-Host 'All three polished icons rendered at 100% and 150% scaling at both window sizes.'
} finally { $window.Close() }

. (Join-Path $PSScriptRoot 'GraphicalWorker.ps1')
$GraphicalSession = @{
    events = [Collections.Concurrent.ConcurrentQueue[object]]::new()
    replies = [Collections.Concurrent.BlockingCollection[object]]::new()
}
try {
    $GraphicalSession.replies.Add(@{ action = 'approve'; plan_id = 'current' })
    $reply = Wait-SetupDecision 'plan' 'current' @{ message = 'review this plan' }
    Assert-Check ($reply.action -ceq 'approve') 'The matching decision was not returned.'
    $event = $null
    Assert-Check ($GraphicalSession.events.TryDequeue([ref]$event)) 'The plan was not sent to the interface.'
    Assert-Check ($event.type -ceq 'plan' -and $event.plan_id -ceq 'current') 'The plan identity changed in transit.'
    $GraphicalSession.replies.Add(@{ action = 'approve'; plan_id = 'previous' })
    $rejected = $false
    try { [void](Wait-SetupDecision 'plan' 'current' @{}) } catch {
        if ($_.Exception.Message -cne 'The response did not match the displayed plan.') { throw }
        $rejected = $true
    }
    Assert-Check $rejected 'A stale decision was accepted.'
    Write-Host 'Native approval transport accepted a matching plan and refused a stale plan.'
} finally { $GraphicalSession.replies.Dispose() }
