#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: Common.ps1

    The spine of the installer: logging, the human-facing console, and the
    step runner.

    ---------------------------------------------------------------------
    THE ONE RULE THIS FILE EXISTS TO ENFORCE
    ---------------------------------------------------------------------
    A step is NEVER reported as successful because its Action did not throw.
    It is reported as successful if, and only if, its -Verify block returns
    $true afterwards. The Action's own opinion of itself is discarded.

    That is deliberate and structural. Every "it said it worked and it had
    not" bug this project has hit came from trusting a command's exit code,
    or a function's return value, instead of going and looking. Invoke-Step
    goes and looks. If you are tempted to add a code path that marks a step
    complete without running Verify, you are reintroducing the defect.
    ---------------------------------------------------------------------

    Console output is split in two:
      * Say-*      -> what SHE sees. Plain English. No paths, no exit codes,
                      no stack traces, no jargon, ever.
      * Write-Log  -> what STEPHEN reads afterwards. Everything.

    PowerShell 5.1 compatible (Windows 11 ships 5.1 as powershell.exe).
    No ternaries, no null-coalescing, no `e escapes.
#>

Set-StrictMode -Version 2.0

# --- Script-scope state -------------------------------------------------

$script:LogPath      = $null
$script:StepNumber   = 0
$script:StepTotal    = 0
$script:Transcript   = New-Object System.Collections.ArrayList
$script:HealEvents   = New-Object System.Collections.ArrayList
$script:Warnings     = New-Object System.Collections.ArrayList
$script:StartedAt    = Get-Date

# ANSI. PS 5.1 has no `e escape, so build it from the char.
$script:ESC = [char]27
$script:C = @{
    Reset   = "$($script:ESC)[0m"
    Bold    = "$($script:ESC)[1m"
    Dim     = "$($script:ESC)[2m"
    Cyan    = "$($script:ESC)[36m"
    Green   = "$($script:ESC)[32m"
    Yellow  = "$($script:ESC)[33m"
    Red     = "$($script:ESC)[31m"
    Grey    = "$($script:ESC)[90m"
}

function Initialize-Console {
    <# Make the console safe for the characters we emit, and turn on ANSI if
       the host supports it. Windows Terminal and conhost on Win10+ both do
       once VT processing is enabled; if it is not, ANSI codes would print as
       garbage, so we blank the colour table rather than risk it. #>
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

    $vt = $false
    try {
        # $Host.UI.SupportsVirtualTerminal exists on PS 5.1 in Windows Terminal
        # and modern conhost. Absent/false means we must not emit escapes.
        if ($Host.UI.PSObject.Properties.Name -contains 'SupportsVirtualTerminal') {
            $vt = [bool]$Host.UI.SupportsVirtualTerminal
        }
    } catch { $vt = $false }

    if (-not $vt) {
        foreach ($k in @($script:C.Keys)) { $script:C[$k] = '' }
    }
}

# --- Logging (Stephen's side) -------------------------------------------

function Initialize-Log {
    param([Parameter(Mandatory)][string] $Path)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $script:LogPath = $Path
    $header = @(
        "Agent Friday - Windows install log",
        "Started      : $((Get-Date).ToString('o'))",
        "Machine      : $env:COMPUTERNAME",
        "User         : $env:USERNAME",
        "OS           : $((Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption)",
        "PowerShell   : $($PSVersionTable.PSVersion)",
        "Installer    : $PSCommandPath",
        ("-" * 72)
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($Path, $header + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Write-Log {
    <#  Everything goes here. This is the file Stephen reads. It is allowed to
        be technical, verbose and ugly. It is NOT allowed to contain secrets -
        see Protect-LogText, which every caller of Write-Log routes through. #>
    param(
        [Parameter(Mandatory)][string] $Message,
        [ValidateSet('INFO','WARN','FAIL','OK','HEAL','CMD','DATA')][string] $Level = 'INFO'
    )
    $line = "{0}  {1,-4}  {2}" -f (Get-Date -Format 'HH:mm:ss'), $Level, (Protect-LogText $Message)
    [void]$script:Transcript.Add($line)
    if ($script:LogPath) {
        try {
            Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
        } catch { }
    }
}

function Protect-LogText {
    <#  Redact anything that looks like a credential before it can reach the
        log, the console, or the healing prompt.

        Note carefully what this does NOT do: it does not print the NAME of a
        secret either. A log line reading "ANTHROPIC_API_KEY was not found"
        tells a reader which secrets exist on this machine and is exactly the
        leak that was fixed on 2026-08-21 (commit c452f17). We redact the
        value AND generalise the name to "<credential>".

        This is a belt-and-braces layer. The correct primary defence is not
        passing secrets to loggable surfaces in the first place. #>
    param([string] $Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $t = $Text

    # Provider key shapes, longest-first so a longer prefix wins.
    $t = [regex]::Replace($t, 'sk-ant-[A-Za-z0-9_\-]{8,}',      '<redacted-credential>')
    $t = [regex]::Replace($t, 'sk-[A-Za-z0-9_\-]{16,}',         '<redacted-credential>')
    $t = [regex]::Replace($t, 'AIza[A-Za-z0-9_\-]{20,}',        '<redacted-credential>')
    $t = [regex]::Replace($t, 'gh[pousr]_[A-Za-z0-9]{16,}',     '<redacted-credential>')
    $t = [regex]::Replace($t, 'xox[baprs]-[A-Za-z0-9\-]{10,}',  '<redacted-credential>')

    # NAME=VALUE / "name": "value" shapes for anything key-ish. Both the name
    # and the value are generalised.
    $t = [regex]::Replace($t,
        '(?i)\b[A-Za-z0-9_]*(?:api[_\-]?key|apikey|secret|token|passphrase|password|passwd|credential)[A-Za-z0-9_]*\b\s*[:=]\s*"?[^\s",;}]+',
        '<credential> = <redacted>')

    # Bare mention of a credential identifier with no value attached.
    $t = [regex]::Replace($t,
        '(?i)\b[A-Za-z0-9_]*(?:API[_\-]?KEY|SECRET|PASSPHRASE)[A-Za-z0-9_]*\b',
        '<credential>')

    return $t
}

# --- Console (her side) --------------------------------------------------

function Say-Banner {
    param([string] $Version = '')
    Write-Host ''
    Write-Host "  $($script:C.Cyan)$($script:C.Bold)Agent Friday$($script:C.Reset)"
    if ($Version) { Write-Host "  $($script:C.Grey)version $Version$($script:C.Reset)" }
    Write-Host ''
}

function Say {
    param([string] $Text = '')
    Write-Host "  $Text"
}

function Say-Step {
    <# Announces a step to her, in her language. Also opens the log section. #>
    param([Parameter(Mandatory)][string] $Title)
    $script:StepNumber++
    $n = "{0}/{1}" -f $script:StepNumber, $script:StepTotal
    Write-Host ''
    Write-Host "  $($script:C.Grey)[$n]$($script:C.Reset) $($script:C.Bold)$Title$($script:C.Reset)"
    Write-Log ("=" * 72)
    Write-Log "STEP $n : $Title"
}

function Say-Detail  { param([string]$Text) Write-Host "        $($script:C.Grey)$Text$($script:C.Reset)" }
function Say-Ok      { param([string]$Text) Write-Host "        $($script:C.Green)OK$($script:C.Reset)  $Text" }
function Say-Working { param([string]$Text) Write-Host "        $($script:C.Grey)... $Text$($script:C.Reset)" }
function Say-Note    { param([string]$Text) Write-Host "        $($script:C.Yellow)Note$($script:C.Reset)  $Text" }

function Say-Problem {
    <#  The only way a problem is ever shown to her. Plain English, and it
        always ends with something she can actually do. If you find yourself
        wanting to put an exit code or a path in here, put it in Write-Log
        instead. #>
    param(
        [Parameter(Mandatory)][string] $What,
        [Parameter(Mandatory)][string] $WhatToDo
    )
    Write-Host ''
    Write-Host "  $($script:C.Yellow)$($script:C.Bold)Something didn't work.$($script:C.Reset)"
    Write-Host ''
    Write-Host "  $What"
    Write-Host ''
    Write-Host "  $($script:C.Bold)What to do:$($script:C.Reset) $WhatToDo"
    Write-Host ''
}

function Add-InstallWarning {
    <# A thing that did not stop the install but that Stephen must see. #>
    param([Parameter(Mandatory)][string] $Text)
    [void]$script:Warnings.Add($Text)
    Write-Log $Text 'WARN'
}

function Get-InstallWarnings {
    # `return @($x)` on an EMPTY collection returns nothing at all, so the
    # caller's `$warns = Get-InstallWarnings` binds $null and the next
    # `$warns.Count` throws under StrictMode. That crashed the build script at
    # the very end of an otherwise successful run - and would have crashed the
    # installer on the last line of a clean install, which is the worst
    # possible place for it.
    #
    # The comma operator wraps the array so it survives the return unflattened.
    # Every Get-* in this project that returns a collection does this.
    return ,@($script:Warnings)
}

# --- Running external commands ------------------------------------------

function ConvertTo-NativeArgumentString {
    <#  Turn an argument ARRAY into the single command-line string that
        CreateProcess wants, using Microsoft's documented argv quoting rules
        (backslashes before a quote are doubled; the quote is escaped).

        WHY THIS EXISTS, AND WHY IT IS STILL SAFE
        -----------------------------------------
        ProcessStartInfo.ArgumentList - the API that takes a real array and
        does this for you - was added in .NET Core 2.1. Windows PowerShell 5.1
        runs on .NET Framework, where it does not exist. This installer targets
        5.1 because that is what a stock Windows 11 machine has. So we must
        compose a string; there is no array option available.

        That is NOT the same thing as composing a shell command line, and the
        difference is the whole security argument:

          * Invoke-Native always sets UseShellExecute = $false, so this string
            is handed to CreateProcess directly. No cmd.exe, no PowerShell
            parser, no shell of any kind is involved.
          * Therefore & | ; > < ` $ ( ) are NOT metacharacters here. They are
            ordinary bytes in an argument. There is nothing for them to inject
            into.
          * The only real risk is ARGUMENT BOUNDARY injection - a value
            containing a quote or a space splitting itself into two arguments.
            Correct quoting below prevents that, and Heal.ps1's validators
            reject quotes and spaces before a value ever reaches here anyway.
            Two independent layers, either of which is sufficient.

        If you ever change Invoke-Native to use cmd.exe /c, Start-Process
        without -ArgumentList, or Invoke-Expression, every sentence above stops
        being true and you have created a command-injection path.
    #>
    param([string[]] $Arguments)
    $parts = @()
    foreach ($a in $Arguments) {
        $s = [string]$a
        if ($s -eq '') { $parts += '""'; continue }
        if ($s -notmatch '[\s"]') { $parts += $s; continue }

        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"')
        $i = 0
        while ($i -lt $s.Length) {
            $slashes = 0
            while ($i -lt $s.Length -and $s[$i] -eq '\') { $slashes++; $i++ }
            if ($i -eq $s.Length) {
                # Trailing backslashes must be doubled so they do not escape
                # the closing quote we are about to append.
                [void]$sb.Append('\' * ($slashes * 2))
            }
            elseif ($s[$i] -eq '"') {
                [void]$sb.Append('\' * ($slashes * 2 + 1))
                [void]$sb.Append('"')
                $i++
            }
            else {
                [void]$sb.Append('\' * $slashes)
                [void]$sb.Append($s[$i])
                $i++
            }
        }
        [void]$sb.Append('"')
        $parts += $sb.ToString()
    }
    return ($parts -join ' ')
}

function Invoke-Native {
    <#  Run an executable. Callers pass an argument ARRAY; it is converted to
        a properly quoted command line by ConvertTo-NativeArgumentString and
        handed to CreateProcess with UseShellExecute = $false, so no shell
        ever sees it. Read that function's header before changing this one.

        Returns a PSCustomObject: ExitCode, StdOut, StdErr, Combined.
        Never throws on a non-zero exit; the caller decides what that means.
    #>
    param(
        [Parameter(Mandatory)][string]   $FilePath,
        [string[]]                       $Arguments = @(),
        [int]                            $TimeoutSeconds = 3600,
        [string]                         $WorkingDirectory = $null,
        [hashtable]                      $Environment = $null
    )

    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()

    Write-Log ("$FilePath " + ($Arguments -join ' ')) 'CMD'

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $FilePath
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.CreateNoWindow         = $true
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    $psi.Arguments = ConvertTo-NativeArgumentString $Arguments
    if ($Environment) {
        # EnvironmentVariables (StringDictionary), not Environment. The latter
        # is another .NET Core-era addition that PowerShell 5.1's .NET
        # Framework host does not reliably have - the same class of trap as
        # ArgumentList above.
        foreach ($k in $Environment.Keys) { $psi.EnvironmentVariables[[string]$k] = [string]$Environment[$k] }
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    $sbOut = New-Object System.Text.StringBuilder
    $sbErr = New-Object System.Text.StringBuilder
    $onOut = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
        if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) }
    } -MessageData $sbOut
    $onErr = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) }
    } -MessageData $sbErr

    $exit = -1
    try {
        [void]$proc.Start()
        $proc.BeginOutputReadLine()
        $proc.BeginErrorReadLine()
        if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
            try { $proc.Kill() } catch { }
            Write-Log "TIMEOUT after ${TimeoutSeconds}s: $FilePath" 'FAIL'
            $exit = 124
        } else {
            # WaitForExit(ms) can return before the async readers drain.
            $proc.WaitForExit()
            $exit = $proc.ExitCode
        }
    } catch {
        Write-Log "Could not start ${FilePath}: $($_.Exception.Message)" 'FAIL'
        $exit = 127
    } finally {
        Unregister-Event -SourceIdentifier $onOut.Name -ErrorAction SilentlyContinue
        Unregister-Event -SourceIdentifier $onErr.Name -ErrorAction SilentlyContinue
        Remove-Job -Id $onOut.Id -Force -ErrorAction SilentlyContinue
        Remove-Job -Id $onErr.Id -Force -ErrorAction SilentlyContinue
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }

    $so = $sbOut.ToString()
    $se = $sbErr.ToString()
    $combined = ($so + "`n" + $se).Trim()

    Write-Log "exit=$exit" 'DATA'
    if ($combined) {
        foreach ($l in ($combined -split "`r?`n" | Select-Object -Last 60)) {
            if ($l.Trim()) { Write-Log "  | $l" 'DATA' }
        }
    }

    return [PSCustomObject]@{
        ExitCode = $exit
        StdOut   = $so
        StdErr   = $se
        Combined = $combined
        Command  = "$FilePath " + ($Arguments -join ' ')
    }
}

# --- The step runner -----------------------------------------------------

function Set-StepTotal { param([int] $Total) $script:StepTotal = $Total }

function New-StepResult {
    # A PSCustomObject rather than a `class`. PowerShell 5.1 scopes classes to
    # the file that defines them and does not reliably surface them through
    # dot-sourcing, so a `class` here would parse fine and then fail at runtime
    # in install.ps1. Learned the boring way; please leave it as a hashtable.
    param([string] $Id)
    return [PSCustomObject]@{
        Succeeded = $false
        StepId    = $Id
        Attempts  = 0
        LastError = ''
        Skipped   = $false
    }
}

function Invoke-Step {
    <#
      .SYNOPSIS
        Run one installation step, then PROVE it worked before saying so.

      .DESCRIPTION
        Sequence:
          1. Run -Verify first. If it already passes, the step is a no-op and
             we say so. (Re-running the installer must be safe.)
          2. Run -Action.
          3. Run -Verify. Only a $true from Verify counts as success.
          4. If Verify is false and healing is armed, ask for a diagnosis,
             apply ONE bounded remediation, then GOTO 3. Capped.
          5. If it still fails: -Optional means warn and continue; otherwise
             show -HumanFailure / -HumanFix and stop the installer.

        -Action's return value, exceptions and exit codes are recorded in the
        log and used as diagnosis input. They are NEVER used to decide whether
        the step succeeded. See the header of this file.

      .PARAMETER Optional
        The install can complete usefully without this. A failure becomes a
        warning in Stephen's report and a calm sentence for her, not a stop.
    #>
    param(
        [Parameter(Mandatory)][string]      $Id,
        [Parameter(Mandatory)][string]      $Title,
        [Parameter(Mandatory)][scriptblock] $Action,
        [Parameter(Mandatory)][scriptblock] $Verify,
        [string]      $HumanFailure = 'A part of the setup did not finish.',
        [string]      $HumanFix     = 'Run the installer again. If it stops in the same place, send Stephen the log file named at the end of this window.',
        [string]      $VerifyDescription = '',
        [switch]      $Optional,
        [switch]      $Quiet,
        [int]         $MaxHealAttempts = 3
    )

    if (-not $Quiet) { Say-Step $Title }

    $result = New-StepResult -Id $Id

    # ---- 1. Already done? -------------------------------------------------
    $already = $false
    try { $already = [bool](& $Verify) } catch { $already = $false }
    if ($already) {
        Write-Log "$Id : verify passed before action - already in place, nothing to do." 'OK'
        if (-not $Quiet) { Say-Ok 'Already set up.' }
        $result.Succeeded = $true
        return $result
    }

    $lastErrorText = ''
    $attempt = 0

    while ($true) {
        $attempt++
        $result.Attempts = $attempt

        # ---- 2. Do the thing ---------------------------------------------
        Write-Log "$Id : attempt $attempt - running action"
        $actionThrew = $null
        try {
            $out = & $Action
            if ($out -ne $null) {
                # Capture whatever the action reported, purely as evidence.
                $txt = ($out | Out-String).Trim()
                if ($txt) { $lastErrorText = $txt }
            }
        } catch {
            $actionThrew = $_
            $lastErrorText = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
            Write-Log "$Id : action threw - $lastErrorText" 'WARN'
        }

        # ---- 3. PROVE it ---------------------------------------------------
        $ok = $false
        $verifyThrew = ''
        try { $ok = [bool](& $Verify) }
        catch { $ok = $false; $verifyThrew = $_.Exception.Message }

        if ($ok) {
            Write-Log "$Id : VERIFIED" 'OK'
            if (-not $Quiet) { Say-Ok 'Done.' }
            $result.Succeeded = $true
            return $result
        }

        $detail = "check did not pass"
        if ($VerifyDescription) { $detail = $VerifyDescription }
        if ($verifyThrew) { $detail = "$detail ($verifyThrew)" }
        Write-Log "$Id : NOT VERIFIED - $detail" 'FAIL'
        if ($actionThrew -eq $null -and -not $lastErrorText) {
            $lastErrorText = "The command reported no error, but the check afterwards still failed: $detail"
        }
        $result.LastError = $lastErrorText

        # ---- 4. Heal, maybe ------------------------------------------------
        $canHeal = (Test-HealingArmed) -and ($attempt -le $MaxHealAttempts)
        if (-not $canHeal) {
            if ($attempt -gt $MaxHealAttempts) {
                Write-Log "$Id : heal attempts exhausted ($MaxHealAttempts)" 'FAIL'
            }
            break
        }

        if (-not $Quiet) { Say-Working 'Just a moment - sorting something out.' }

        $healed = Invoke-Healing -StepId $Id -StepTitle $Title `
                                 -ErrorText $lastErrorText `
                                 -VerifyDescription $detail `
                                 -Attempt $attempt `
                                 -StepIsOptional:$Optional
        if (-not $healed) {
            Write-Log "$Id : healing produced no applicable remediation - stopping the loop" 'FAIL'
            break
        }

        # Loop back to 3 via 2: the next iteration re-runs Action then Verify.
        # A remediation that fixed the environment will now let Action succeed;
        # a remediation that fixed the artefact will let Verify pass. Either
        # way the ONLY thing that ends this loop successfully is Verify.
    }

    # ---- 5. It genuinely did not work -----------------------------------
    if ($Optional) {
        Add-InstallWarning "OPTIONAL STEP FAILED [$Id] '$Title' after $($result.Attempts) attempt(s). Last error: $lastErrorText"
        if (-not $Quiet) { Say-Note 'Skipped this part - Friday will still work without it.' }
        $result.Succeeded = $false
        $result.Skipped   = $true
        return $result
    }

    Write-Log "$Id : FATAL - install cannot continue" 'FAIL'
    $result.Succeeded = $false
    Say-Problem -What $HumanFailure -WhatToDo $HumanFix
    Complete-Install -Failed -FailedStep $Id
    exit 1
}

# --- Heal plumbing (real implementations live in Heal.ps1) ---------------
# Defined here as no-ops so Common.ps1 is usable on its own and so a build
# that does not ship Heal.ps1 degrades to "no healing" rather than crashing.

if (-not (Get-Command Test-HealingArmed -ErrorAction SilentlyContinue)) {
    function Test-HealingArmed { return $false }
}
if (-not (Get-Command Invoke-Healing -ErrorAction SilentlyContinue)) {
    function Invoke-Healing { param($StepId,$StepTitle,$ErrorText,$VerifyDescription,$Attempt,$StepIsOptional) return $false }
}

function Add-HealEvent {
    <# Called by Heal.ps1. Kept here so the final report can be written even
       if healing was never armed. #>
    param([Parameter(Mandatory)][hashtable] $Event)
    [void]$script:HealEvents.Add([PSCustomObject]$Event)
}
function Get-HealEvents { return ,@($script:HealEvents) }   # see Get-InstallWarnings

# --- Finishing -----------------------------------------------------------

function Complete-Install {
    <# Writes the report Stephen reads. Called on success AND on failure -
       a failed install is exactly when the report matters most. #>
    param(
        [switch] $Failed,
        [string] $FailedStep = '',
        [string] $ReportPath = ''
    )

    $elapsed = (Get-Date) - $script:StartedAt
    $heals   = Get-HealEvents
    $warns   = Get-InstallWarnings

    if (-not $ReportPath -and $script:LogPath) {
        $ReportPath = Join-Path (Split-Path -Parent $script:LogPath) 'LAST-INSTALL-REPORT.md'
    }
    if (-not $ReportPath) { return }

    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add('# Agent Friday - install report')
    [void]$lines.Add('')
    if ($Failed) {
        [void]$lines.Add("**Result: FAILED** at step ``$FailedStep``.")
    } else {
        [void]$lines.Add('**Result: completed.**')
    }
    [void]$lines.Add('')
    [void]$lines.Add("- Finished: $((Get-Date).ToString('u'))")
    [void]$lines.Add("- Took: $([int]$elapsed.TotalMinutes) min $($elapsed.Seconds) sec")
    [void]$lines.Add("- Machine: $env:COMPUTERNAME  /  $((Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption)")
    [void]$lines.Add("- Full log: $($script:LogPath)")
    [void]$lines.Add('')

    # --- Healing section --------------------------------------------------
    [void]$lines.Add('## Self-repair')
    [void]$lines.Add('')
    if (-not (Test-HealingArmed)) {
        [void]$lines.Add('Self-repair was not armed for this install (no key supplied, or consent declined).')
    } elseif ($heals.Count -eq 0) {
        [void]$lines.Add('Self-repair was armed and was never needed. Nothing was repaired.')
    } else {
        [void]$lines.Add("Self-repair ran **$($heals.Count)** time(s). Every one of these is a real defect that")
        [void]$lines.Add('bit a real machine. They are listed so they can be fixed properly rather than')
        [void]$lines.Add('quietly papered over on every future install.')
        [void]$lines.Add('')
        $i = 0
        foreach ($h in $heals) {
            $i++
            [void]$lines.Add("### $i. Step ``$($h.StepId)`` - attempt $($h.Attempt)")
            [void]$lines.Add('')
            [void]$lines.Add("- **Symptom the installer saw:** $($h.Symptom)")
            [void]$lines.Add("- **Diagnosis:** $($h.Diagnosis)")
            [void]$lines.Add("- **Remediation chosen:** ``$($h.Remediation)``")
            [void]$lines.Add("- **Parameters:** ``$($h.Parameters)``")
            [void]$lines.Add("- **Applied:** $($h.Applied)")
            [void]$lines.Add("- **Re-check after repair:** $($h.VerifiedAfter)")
            if ($h.PSObject.Properties.Name -contains 'Refused' -and $h.Refused) {
                [void]$lines.Add("- **REFUSED:** $($h.Refused)")
            }
            [void]$lines.Add('')
        }
        $inTok  = ($heals | Measure-Object -Property InputTokens  -Sum).Sum
        $outTok = ($heals | Measure-Object -Property OutputTokens -Sum).Sum
        $cost   = ($heals | Measure-Object -Property CostUsd      -Sum).Sum
        [void]$lines.Add('### Cost of self-repair')
        [void]$lines.Add('')
        [void]$lines.Add("Input tokens: $inTok  ·  Output tokens: $outTok")
        [void]$lines.Add(("Approximate cost: **`${0:N4}** USD, charged to the Anthropic key entered during setup." -f $cost))
        [void]$lines.Add('')
        [void]$lines.Add('That figure is an estimate from a rate table baked into the installer at build')
        [void]$lines.Add('time (`healing.json`). It is not a bill. Check the Anthropic console for the real one.')
    }
    [void]$lines.Add('')

    # --- Warnings ---------------------------------------------------------
    [void]$lines.Add('## Things that did not work but did not stop the install')
    [void]$lines.Add('')
    if ($warns.Count -eq 0) {
        [void]$lines.Add('None.')
    } else {
        foreach ($w in $warns) { [void]$lines.Add("- $w") }
    }
    [void]$lines.Add('')

    # --- Transcript -------------------------------------------------------
    [void]$lines.Add('## Full transcript')
    [void]$lines.Add('')
    [void]$lines.Add('```')
    foreach ($t in $script:Transcript) { [void]$lines.Add($t) }
    [void]$lines.Add('```')

    try {
        $text = ($lines -join "`r`n")
        [System.IO.File]::WriteAllText($ReportPath, $text, (New-Object System.Text.UTF8Encoding($false)))
        Write-Log "Report written: $ReportPath" 'OK'
    } catch {
        Write-Log "Could not write report: $($_.Exception.Message)" 'WARN'
    }
}
