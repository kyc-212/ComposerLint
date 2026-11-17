#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms

$root = $PSScriptRoot
$layout = Join-Path $root 'SetupGUI.xaml'
$window = [Windows.Markup.XamlReader]::Parse([IO.File]::ReadAllText($layout))
$ui = @{}
foreach ($control in @('Privacy','BrandIcon','Platform','StepList','Page','Version','StepCounter','Heading',
        'Subtitle','ChoosePanel','Full','TextOnly','Requirements','Destination','ToolPanel','BrowseCache','CachePath',
        'ClearCache','RendererPanel','BrowseRenderer','ClearRenderer','RendererPath','WorkPanel','Status','BusyBar','Details','Answer','LogPanel',
        'Log','Primary','Back')) {
    $ui[$control] = $window.FindName($control)
}
$icon = [Windows.Media.Imaging.BitmapImage]::new([Uri]::new((Join-Path $root 'setup-icon.png')))
$window.Icon = $icon
$ui.BrandIcon.Source = $icon
$meta = Get-Content -LiteralPath (Join-Path $root 'graphical-preview.json') -Raw | ConvertFrom-Json
$ui.Version.Text = 'v' + $meta.version
$ui.Platform.Text = 'SETUP  /  WINDOWS ' + ([string]$env:PROCESSOR_ARCHITECTURE).Replace('AMD64', 'X64')

$labels = @('Choose your setup', 'Prepare tools', 'Review installation', 'Install & verify', 'Connect to your AI client')
$descriptions = @('Full experience or text analysis', 'Check the tools and files you need',
    'Approve your installation plan', 'Install privately and run checks', 'Copy your connection settings')
$rows = @()
for ($index = 0; $index -lt $labels.Count; $index++) {
    $row = [Windows.Controls.DockPanel]::new()
    $row.Margin = '0,0,0,15'
    $badge = [Windows.Controls.Border]::new()
    $badge.Width = 29
    $badge.Height = 29
    $badge.CornerRadius = '15'
    $badge.Margin = '0,0,10,0'
    $badge.VerticalAlignment = 'Top'
    $number = [Windows.Controls.TextBlock]::new()
    $number.HorizontalAlignment = 'Center'
    $number.VerticalAlignment = 'Center'
    $number.FontWeight = 'Bold'
    $number.Text = [string]($index + 1)
    $badge.Child = $number
    $label = [Windows.Controls.TextBlock]::new()
    $label.Text = $labels[$index]
    $label.FontWeight = 'SemiBold'
    $description = [Windows.Controls.TextBlock]::new()
    $description.Text = $descriptions[$index]
    $description.FontSize = 12
    $description.Foreground = '#ABC1C9'
    $words = [Windows.Controls.StackPanel]::new()
    [void]$words.Children.Add($label)
    [void]$words.Children.Add($description)
    [void]$row.Children.Add($badge)
    [void]$row.Children.Add($words)
    [void]$ui.StepList.Children.Add($row)
    $rows += @{ badge = $badge; number = $number; label = $label }
}

$state = @{
    step = 1; mode = 'Full'; cache = ''; renderer = ''; pending = $null; session = $null
    engine = $null; job = $null; result = $null; failed = $false; terminal = $null
}
$issues = @()
$local = [Environment]::GetFolderPath('LocalApplicationData', 'DoNotVerify')
if ($local) { $ui.Destination.Text = Join-Path $local 'ComposerLint\setup' }
try {
    $disk = if ($local -match '^[A-Za-z]:\\') { [IO.DriveInfo]::new([IO.Path]::GetPathRoot($local)) }
    $hasNtfs = $disk -and $disk.DriveType -eq 'Fixed' -and $disk.DriveFormat -eq 'NTFS'
} catch { $hasNtfs = $false }
if (!$hasNtfs) {
    $issues += 'Use a Windows profile stored on a local NTFS drive.'
}
if (![Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -cne 'AMD64') {
    $issues += 'Open this preview on 64-bit x64 Windows.'
}
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $issues += 'Close this window and reopen setup as your regular user.'
}
if (!(Test-Path -LiteralPath (Join-Path ([Environment]::SystemDirectory) 'tar.exe') -PathType Leaf)) {
    $issues += 'Windows tar is missing. Ask your administrator to restore the built-in archive tools.'
}
$ui.Requirements.Text = if ($issues.Count) { $issues -join "`n" } else {
    'Windows x64 and NTFS detected. Setup checks folder permissions before installing. It prepares a private Python runtime and uses verified local files when available.'
}

function Show-Step([int]$Number, [string]$Heading, [string]$Subtitle) {
    $state.step = $Number
    $ui.StepCounter.Text = "STEP $Number OF $($labels.Count)"
    $ui.Heading.Text = $Heading
    $ui.Subtitle.Text = $Subtitle
    for ($position = 0; $position -lt $rows.Count; $position++) {
        $active = ($position + 1 -eq $Number)
        $rows[$position].badge.Background = if ($active) { '#54C6AE' } else { '#213D4E' }
        $rows[$position].number.Foreground = if ($active) { '#133039' } else { '#BCD1D8' }
        $rows[$position].label.Foreground = if ($active) { 'White' } else { '#B8C8CE' }
    }
    $ui.ChoosePanel.Visibility = if ($Number -eq 1) { 'Visible' } else { 'Collapsed' }
    $ui.ToolPanel.Visibility = if ($Number -eq 2 -and !$state.engine -and !$state.failed) { 'Visible' } else { 'Collapsed' }
    $ui.RendererPanel.Visibility = if ($state.mode -eq 'Full') { 'Visible' } else { 'Collapsed' }
    $ui.WorkPanel.Visibility = if ($Number -gt 1 -and ($state.engine -or $state.failed -or $state.result)) {
        'Visible'
    } else { 'Collapsed' }
    $ui.Page.ScrollToTop()
}

function Show-Choose {
    Show-Step 1 'Make room for your next idea.' 'A guided installation for your music workspace.'
    $ui.Primary.Content = 'Review setup'
    $ui.Primary.IsEnabled = !$issues.Count
    $ui.Back.Content = 'Close'
    $ui.Back.IsEnabled = $true
}

function Show-Tools {
    Show-Step 2 'Prepare tools' 'Check what is available and review where any missing files come from.'
    $ui.Primary.Content = 'Check tools'
    $ui.Primary.IsEnabled = $true
    $ui.Back.Content = 'Back'
    $ui.Back.IsEnabled = $true
}

function Set-Busy([string]$Message) {
    $ui.Status.Text = $Message
    $ui.BusyBar.Visibility = 'Visible'
    $ui.Details.Visibility = 'Collapsed'
    $ui.Answer.Visibility = 'Collapsed'
    $ui.Primary.IsEnabled = $false
    $ui.Back.IsEnabled = $false
}

function Start-Installer {
    $state.failed = $false
    $state.result = $null
    $state.terminal = $null
    $state.session = [hashtable]::Synchronized(@{
        cache = $state.cache; complete = $null
        events = [Collections.Concurrent.ConcurrentQueue[object]]::new()
        replies = [Collections.Concurrent.BlockingCollection[object]]::new()
    })
    $state.engine = [PowerShell]::Create()
    [void]$state.engine.AddCommand((Join-Path $root 'Setup.ps1')).AddParameter('Mode', $state.mode)
    [void]$state.engine.AddParameter('GraphicalSession', $state.session)
    if ($state.mode -eq 'Full' -and $state.renderer) {
        [void]$state.engine.AddParameter('RendererBundle', $state.renderer)
    }
    $ui.Log.Clear()
    $ui.LogPanel.IsExpanded = $false
    Show-Step 2 'Prepare tools' 'Setup checks the extracted files before preparing private Python.'
    Set-Busy 'Checking this setup package and your installation folder...'
    $state.job = $state.engine.BeginInvoke()
}

function Show-Request($Event) {
    $state.pending = $Event
    $ui.BusyBar.Visibility = 'Collapsed'
    $ui.Details.Visibility = 'Visible'
    $ui.Answer.Clear()
    $ui.Back.Content = 'Cancel'
    $ui.Back.IsEnabled = $true
    $ui.Primary.IsEnabled = $true
    switch ($Event.type) {
        'bootstrap_plan' {
            Show-Step 2 'Prepare tools' 'Review the Python file needed to prepare your installation.'
            $ui.Status.Text = 'Private Python'
            $ui.Details.Text = $Event.message
            $ui.Primary.Content = 'Prepare Python'
        }
        'plan' {
            Show-Step 3 'Review installation' 'Check where ComposerLint will be installed and approve the exact file list.'
            $plan = $Event.result
            $renderer = if ($plan.renderer) { "Renderer: $($plan.renderer)" } else { 'Text analysis only' }
            $ui.Status.Text = "Install to $($plan.destination)`n$renderer"
            $ui.Details.Text = ($plan.assets | ForEach-Object {
                "$($_.action): $($_.filename) ($($_.size) bytes)`n$($_.url)`nSHA-256 $($_.sha256)"
            }) -join "`n`n"
            $ui.Primary.Content = 'Install'
        }
        'choice' {
            $ui.Status.Text = 'The installer needs your decision about a renderer build tool.'
            $ui.Details.Text = $Event.message
            $ui.Answer.Visibility = 'Visible'
            $ui.Primary.Content = 'Continue'
            [void]$ui.Answer.Focus()
        }
    }
}

function Reply-Installer([bool]$Approved) {
    $event = $state.pending
    if (!$event) { return }
    $state.pending = $null
    $reply = @{ plan_id = $event.plan_id; action = 'cancel' }
    if ($Approved) {
        $reply.action = 'approve'
        if ($event.type -eq 'choice') {
            $reply.action = 'answer'
            $reply.answer = $ui.Answer.Text.Trim()
        }
    }
    $state.session.replies.Add($reply)
    if ($Approved -and $event.type -eq 'plan') {
        Show-Step 4 'Install & verify' 'Keep this window open while ComposerLint sets up and checks the private environment.'
    }
    Set-Busy $(if ($Approved) { 'Continuing with setup...' } else { 'Cancelling at this approval step...' })
}

function Add-Log([string]$Message) {
    $ui.Log.AppendText($Message + "`n")
    if ($ui.Log.Text.Length -gt 42000) {
        $ui.Log.Text = $ui.Log.Text.Substring($ui.Log.Text.Length - 32000)
    }
    $ui.Log.ScrollToEnd()
}

function Show-Complete($Result) {
    $state.result = $Result
    Show-Step 5 'Connect to your AI client' 'Add ComposerLint as a local stdio server in your AI client, then reconnect.'
    $settings = [ordered]@{ command = $Result.python; args = @($Result.arguments) }
    if ($Result.renderer) {
        $settings.env = @{ COMPOSERLINT_RENDERER_CONFIG = $Result.renderer }
    }
    $state.result = [pscustomobject]@{
        settings = (ConvertTo-Json -InputObject $settings -Depth 5)
        skills = $Result.skills
    }
    $ui.Status.Text = 'Private installation verified. Copy these server settings into your client.'
    $ui.Details.Text = $state.result.settings + "`n`nSkills folder: " + $state.result.skills
    $ui.Details.Visibility = 'Visible'
    $ui.Primary.Content = 'Copy server settings'
    $ui.Primary.IsEnabled = $true
    $ui.Back.Content = 'Close'
    $ui.Back.IsEnabled = $true
}

function Finish-Installer {
    $ui.BusyBar.Visibility = 'Collapsed'
    $ui.Answer.Visibility = 'Collapsed'
    $state.pending = $null
    $event = $state.terminal
    if ($event -and $event.type -eq 'complete') {
        Show-Complete $event.result
    } elseif ($event -and $event.type -eq 'cancelled') {
        Show-Tools
    } else {
        $state.failed = $true
        Show-Step $state.step 'Setup stopped' 'Review the log, correct the issue and check the setup again.'
        $ui.Status.Text = if ($event) { $event.message } else { 'Setup exited before it could verify a completed installation.' }
        $ui.Details.Visibility = 'Collapsed'
        $ui.LogPanel.IsExpanded = $true
        $ui.Primary.Content = 'Review tools'
        $ui.Primary.IsEnabled = $true
        $ui.Back.Content = 'Close'
        $ui.Back.IsEnabled = $true
    }
}

$timer = [Windows.Threading.DispatcherTimer]::new()
$timer.Interval = [TimeSpan]::FromMilliseconds(90)
$timer.Add_Tick({
    if (!$state.engine) { return }
    $event = $null
    for ($count = 0; $count -lt 80 -and $state.session.events.TryDequeue([ref]$event); $count++) {
        switch ($event.type) {
            { $_ -in 'bootstrap_plan', 'plan', 'choice' } { Show-Request $event }
            'progress' { $ui.Status.Text = $event.message; Add-Log $event.message }
            'log' { Add-Log $event.message }
            default { $state.terminal = $event }
        }
    }
    if ($state.job.IsCompleted -and $state.session.events.IsEmpty) {
        try { [void]$state.engine.EndInvoke($state.job) } catch { Add-Log $_.Exception.Message }
        foreach ($item in $state.engine.Streams.Error) { Add-Log ([string]$item) }
        foreach ($item in $state.engine.Streams.Warning) { Add-Log ([string]$item) }
        $state.engine.Dispose()
        $state.session.replies.Dispose()
        $state.engine = $null
        Finish-Installer
    }
})

$ui.BrowseCache.Add_Click({
    $dialog = [Windows.Forms.FolderBrowserDialog]::new()
    $dialog.Description = 'Choose the folder containing reviewed setup files'
    $dialog.ShowNewFolderButton = $false
    try {
        if ($dialog.ShowDialog() -eq 'OK') {
            $state.cache = $dialog.SelectedPath
            $ui.CachePath.Text = $state.cache
            $ui.ClearCache.IsEnabled = $true
        }
    } finally { $dialog.Dispose() }
})
$ui.ClearCache.Add_Click({
    $state.cache = ''
    $ui.CachePath.Text = ''
    $ui.ClearCache.IsEnabled = $false
})
$ui.BrowseRenderer.Add_Click({
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.Title = 'Choose a reviewed renderer bundle'
    $dialog.Filter = 'Renderer ZIP (*.zip)|*.zip'
    if ($dialog.ShowDialog($window)) {
        $state.renderer = $dialog.FileName
        $ui.RendererPath.Text = $state.renderer
        $ui.ClearRenderer.IsEnabled = $true
    }
})
$ui.ClearRenderer.Add_Click({
    $state.renderer = ''
    $ui.RendererPath.Text = ''
    $ui.ClearRenderer.IsEnabled = $false
})
$ui.Primary.Add_Click({
    if ($state.pending) { Reply-Installer $true; return }
    if ($state.result) {
        try {
            [Windows.Clipboard]::SetText($state.result.settings)
            $ui.Status.Text = 'Server settings copied. Paste them into your AI client.'
        } catch { $ui.Status.Text = 'Clipboard unavailable. Select the settings below to copy them manually.' }
    } elseif ($state.failed) {
        $state.failed = $false
        Show-Tools
    } elseif ($state.step -eq 1) {
        $state.mode = if ($ui.Full.IsChecked) { 'Full' } else { 'Text' }
        Show-Tools
    } elseif (!$state.engine) {
        Start-Installer
    }
})
$ui.Back.Add_Click({
    if ($state.pending) { Reply-Installer $false }
    elseif ($state.step -eq 2 -and !$state.failed) { Show-Choose }
    else { $window.Close() }
})
$window.Add_Closing({
    param($sender, $closing)
    if ($state.engine) {
        $closing.Cancel = $true
        if ($state.pending) { Reply-Installer $false }
        else { $ui.Status.Text = 'Setup is busy. Leave this window open until the current operation finishes.' }
    }
})
$window.Add_SizeChanged({ $ui.Privacy.Visibility = if ($window.ActualHeight -lt 590) { 'Collapsed' } else { 'Visible' } })
Show-Choose
$timer.Start()
try { [void]$window.ShowDialog() } finally { $timer.Stop() }
