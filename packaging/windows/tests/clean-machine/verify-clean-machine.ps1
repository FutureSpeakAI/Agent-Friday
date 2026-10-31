#Requires -Version 5.1
<#
    One clean-machine leg (north star §34.3): install the setup program on a
    machine that has never seen Friday, in THIS process's account, start the
    installed server, check what a first-time person gets, and uninstall
    keeping the person's data. (The two Windows-image legs run the full
    verify-clean-install.ps1 instead.) The legs here differ in where they run:

      standard-user a standard (not administrator) Windows account whose name,
                    and so whose profile folder, is not plain ASCII; started by
                    run-as-standard-user.ps1
      locale        a non-English user locale, set before this script starts
                    (-ExpectCulture names it; the check fails on any other).
                    The ANSI code page is not changed: that needs a reboot.
      memory-cap    Friday's server and everything it starts run inside a
                    Windows job object whose commit is capped at -MemoryCapMiB.
                    The leg passes only if the server keeps serving and its peak
                    stays under 90% of the cap (the cap itself can never be
                    exceeded, so reaching it is the failure).

    It refuses to run anywhere but a disposable GitHub-hosted runner: it
    installs into the real profile and removes what it installed. A run as the
    standard user cannot see the runner's environment (that account has its
    own), so run-as-standard-user.ps1 passes -RunnerMarker, a file holding
    -RunId. That only guards against running this by accident; anyone who
    writes the file can pass it.

    The model page is answered with the cloud option (/ModelsCloud=1) and the
    memory and judgment tiers are skipped, so nothing large is downloaded.
    Every check is written to RESULT.json, whatever happens; a check that
    could not run is written as failed with the reason:

      account     who ran it, whether that account is an administrator, and
                  whether its profile path is plain ASCII
      locale      the culture this process ran under
      install     the setup program exited 0 and Friday's files are in place
      health      /api/health answers and reports this release
      setup-chat  first run opens on the setup chat's first stage
      memory      (memory-cap only) still serving after the first boot's
                  background work; peak under 90% of the cap
      boot-errors the first boot logged no Traceback and no ERROR or CRITICAL line
      uninstall   the install folder and Apps entry are gone and ~/.friday is
                  kept, byte for byte
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $Setup,
    [Parameter(Mandatory)][string] $WorkDir,
    [Parameter(Mandatory)][ValidateSet('standard-user', 'locale', 'memory-cap')][string] $Variant,
    [int] $Port = 3000,
    [int] $BootSeconds = 480,
    [int] $MemoryCapMiB = 0,
    [string] $ExpectCulture = '',
    [string] $RunnerMarker = '',
    [string] $RunId = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$onRunner = ($env:GITHUB_ACTIONS -eq 'true') -and ($env:RUNNER_ENVIRONMENT -eq 'github-hosted')
if (-not $onRunner -and $RunnerMarker -and $RunId -and (Test-Path -LiteralPath $RunnerMarker)) {
    $onRunner = ((Get-Content -LiteralPath $RunnerMarker -Raw).Trim() -eq $RunId)
}
if (-not $onRunner) {
    throw 'verify-clean-machine.ps1 installs into this profile and removes what it installed; it runs only on a disposable GitHub-hosted runner.'
}
if ($Variant -eq 'memory-cap' -and $MemoryCapMiB -le 0) { throw 'the memory-cap leg needs -MemoryCapMiB' }
if ($Variant -eq 'locale' -and -not $ExpectCulture) { throw 'the locale leg needs -ExpectCulture' }

$Results = [ordered]@{}
function Check([string] $name, [bool] $ok, [string] $detail) {
    $Results[$name] = [ordered]@{ ok = $ok; detail = $detail }
    $mark = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1}: {2}" -f $mark, $name, $detail)
}
function Save-Result {
    $out = [ordered]@{
        variant = $Variant
        image   = [string]$env:ImageOS
        os      = [string](Get-CimInstance Win32_OperatingSystem).Caption
        build   = [string][Environment]::OSVersion.Version
        checks  = $Results
    }
    $out | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $WorkDir 'RESULT.json') -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$InstallRoot = Join-Path $env:LOCALAPPDATA 'AgentFriday'
$FridayDir   = Join-Path $env:USERPROFILE '.friday'
$ServerLog   = Join-Path $WorkDir 'server.log'
if (Test-Path $InstallRoot) { throw "not a fresh machine: $InstallRoot exists" }
if (Test-Path $FridayDir)   { throw "not a fresh machine: $FridayDir exists" }
$AppsKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{BE782F40-3D1C-4F12-8635-4F7561938F75}_is1'
$py = Join-Path $InstallRoot 'python\python.exe'
$env:FRIDAY_PORT = "$Port"
$env:PYTHONUTF8 = '1'

$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$pyproject = Get-Content -LiteralPath (Join-Path $RepoRoot 'pyproject.toml') -Raw
$vm = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $vm.Success) { throw 'cannot read the expected version from pyproject.toml' }
$ExpectedVersion = $vm.Groups[1].Value

# Every check the variant must produce starts as "did not run"; the flow below
# replaces each with its real result, and RESULT.json is written whatever happens.
$must = @('account', 'locale', 'install', 'health', 'setup-chat', 'boot-errors', 'uninstall')
if ($Variant -eq 'memory-cap') { $must += 'memory' }
foreach ($m in $must) { $Results[$m] = [ordered]@{ ok = $false; detail = 'did not run' } }

$server = $null
$job = $null
try {
    # ── Who and where ──────────────────────────────────────────────────────
    $me = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal $me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $asciiProfile = ($env:USERPROFILE -cmatch '^[\x20-\x7E]+$')
    # The account's own profile, from Windows, not from the environment: a
    # child started with -Credential can inherit the caller's USERPROFILE and
    # LOCALAPPDATA, and an install would then land in the administrator's profile.
    $ownProfile = ''
    try {
        $ownProfile = [string](Get-CimInstance Win32_UserProfile -ErrorAction Stop |
            Where-Object { $_.SID -eq $me.User.Value } | Select-Object -First 1).LocalPath
    } catch { }
    $inOwnProfile = $ownProfile -and ($env:USERPROFILE -ieq $ownProfile) -and
                    ($env:LOCALAPPDATA -like "$ownProfile\*")
    $accountOk = if ($Variant -eq 'standard-user') { (-not $isAdmin) -and (-not $asciiProfile) -and $inOwnProfile } else { $true }
    Check 'account' $accountOk ("user: $($me.Name); administrator: $isAdmin; profile: $env:USERPROFILE; own profile: $ownProfile; LOCALAPPDATA: $env:LOCALAPPDATA; plain ASCII path: $asciiProfile")

    $culture = [Globalization.CultureInfo]::CurrentCulture.Name
    $localeOk = if ($Variant -eq 'locale') { $culture -ceq $ExpectCulture } else { $true }
    Check 'locale' $localeOk ("culture: $culture (expected: $(if ($ExpectCulture) { $ExpectCulture } else { 'any' })); UI culture: $([Globalization.CultureInfo]::CurrentUICulture.Name); ANSI code page: $([Text.Encoding]::Default.CodePage); decimal separator: '$([Globalization.CultureInfo]::CurrentCulture.NumberFormat.NumberDecimalSeparator)'")

    # ── Install ────────────────────────────────────────────────────────────
    $setupLog = Join-Path $WorkDir 'setup.log'
    $p = Start-Process -FilePath $Setup -PassThru -Wait `
         -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=`"$setupLog`"",
                         '/ModelsCloud=1', '/SkipMemory=1', '/SkipJudgment=1')
    Check 'install' (($p.ExitCode -eq 0) -and (Test-Path $py) -and (Test-Path (Join-Path $InstallRoot 'app\server.py'))) `
          "exit $($p.ExitCode); interpreter present: $(Test-Path $py)"
    if (-not (Test-Path $py)) { throw 'the setup program left no interpreter' }

    # ── A job object for the memory-cap leg ────────────────────────────────
    if ($Variant -eq 'memory-cap') {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FridayJob {
    [StructLayout(LayoutKind.Sequential)] struct BASIC { public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass; }
    [StructLayout(LayoutKind.Sequential)] struct IO { public ulong a, b, c, d, e, f; }
    [StructLayout(LayoutKind.Sequential)] struct EXTENDED { public BASIC Basic; public IO Io; public UIntPtr ProcessMemoryLimit; public UIntPtr JobMemoryLimit; public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed; }
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int cls, ref EXTENDED info, uint len);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool QueryInformationJobObject(IntPtr job, int cls, out EXTENDED info, uint len, IntPtr ret);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
    const uint JOB_MEMORY = 0x200, KILL_ON_CLOSE = 0x2000;
    public static IntPtr Create(ulong capBytes) {
        IntPtr j = CreateJobObject(IntPtr.Zero, null);
        if (j == IntPtr.Zero) throw new Exception("CreateJobObject " + Marshal.GetLastWin32Error());
        EXTENDED e = new EXTENDED();
        e.Basic.LimitFlags = JOB_MEMORY | KILL_ON_CLOSE;
        e.JobMemoryLimit = new UIntPtr(capBytes);
        if (!SetInformationJobObject(j, 9, ref e, (uint)Marshal.SizeOf(e))) throw new Exception("SetInformationJobObject " + Marshal.GetLastWin32Error());
        return j;
    }
    public static void Assign(IntPtr j, IntPtr proc) {
        if (!AssignProcessToJobObject(j, proc)) throw new Exception("AssignProcessToJobObject " + Marshal.GetLastWin32Error());
    }
    public static ulong PeakBytes(IntPtr j) {
        EXTENDED e;
        if (!QueryInformationJobObject(j, 9, out e, (uint)Marshal.SizeOf(typeof(EXTENDED)), IntPtr.Zero)) throw new Exception("QueryInformationJobObject " + Marshal.GetLastWin32Error());
        return e.PeakJobMemoryUsed.ToUInt64();
    }
}
'@
        $job = [FridayJob]::Create([uint64]$MemoryCapMiB * 1MB)
    }

    # ── First boot ─────────────────────────────────────────────────────────
    $server = Start-Process -FilePath $py -ArgumentList @('server.py') `
        -WorkingDirectory (Join-Path $InstallRoot 'app') -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $ServerLog -RedirectStandardError "$ServerLog.err"
    # Assigned at once. The interpreter starts no child process in its first
    # moments (imports come first), so everything the server starts inherits
    # the cap; the job's own accounting is what the peak is read from.
    if ($job) { [FridayJob]::Assign($job, $server.Handle) }

    $base = "http://127.0.0.1:$Port"
    $page = $null
    $deadline = (Get-Date).AddSeconds($BootSeconds)
    while ((Get-Date) -lt $deadline -and -not $server.HasExited) {
        try {
            $page = Invoke-WebRequest -Uri "$base/" -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 10
            if ($page.StatusCode -eq 200) { break }
        } catch { Start-Sleep -Seconds 3 }
    }
    $served = ($null -ne $page) -and ($page.StatusCode -eq 200)
    $hdr = @{}
    if ($served) { $hdr = @{ 'X-Friday-Token' = [regex]::Match($page.Content, 'window\.__FRIDAY_API_TOKEN="([^"]+)"').Groups[1].Value } }
    function Get-Api([string] $path) {
        return (Invoke-WebRequest -Uri "$base$path" -Headers $hdr -UseBasicParsing -TimeoutSec 60).Content | ConvertFrom-Json
    }

    $health = $null
    $why = if ($server.HasExited) { "the server exited with $($server.ExitCode) before answering" } else { "no answer from $base/ within $BootSeconds s" }
    if ($served) { try { $health = Get-Api '/api/health' } catch { $why = "/api/health: $($_.Exception.Message)" } }
    Check 'health' (($null -ne $health) -and ([string]$health.version -eq $ExpectedVersion)) `
          $(if ($health) { "version: $($health.version) (expected $ExpectedVersion)" } else { $why })

    $chat = $null
    $why = 'the server was not serving'
    if ($served) { try { $chat = Get-Api '/api/setup-chat/state' } catch { $why = "/api/setup-chat/state: $($_.Exception.Message)" } }
    Check 'setup-chat' (($null -ne $chat) -and ($chat.stage -eq 'welcome') -and (-not $chat.completed)) `
          $(if ($chat) { "stage: $($chat.stage); completed: $($chat.completed)" } else { $why })

    if ($job) {
        # Let the first boot's background work run inside the cap, then ask again.
        $after = $null
        if ($served) {
            Start-Sleep -Seconds 45
            try { $after = Get-Api '/api/health' } catch { }
        }
        $peakMiB = [math]::Round([FridayJob]::PeakBytes($job) / 1MB)
        $headroomOk = $peakMiB -lt [math]::Floor($MemoryCapMiB * 0.9)
        Check 'memory' ((-not $server.HasExited) -and ($null -ne $after) -and $headroomOk) `
              "cap: $MemoryCapMiB MiB; peak commit of Friday's processes: $peakMiB MiB (must stay under 90%: $headroomOk); still serving after first-boot work: $((-not $server.HasExited) -and ($null -ne $after))"
    }
}
catch {
    Write-Host "the leg stopped early: $($_.Exception.Message)"
}
finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallRoot, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 3

    # ── No errors in the first boot ────────────────────────────────────────
    # The same rule as verify-clean-install.ps1: a Traceback, or a line at ERROR
    # or CRITICAL, fails the check. The one allowed line is the memory tier's
    # import error, allowed only when that tier is absent (/SkipMemory=1).
    if (Test-Path -LiteralPath $ServerLog) {
        $bootLogs = @($ServerLog, "$ServerLog.err")
        $fridayLog = Join-Path $FridayDir 'friday.log'
        if (Test-Path -LiteralPath $fridayLog) {
            $copy = Join-Path $WorkDir 'friday-first-boot.log'
            Copy-Item -LiteralPath $fridayLog -Destination $copy -Force
            $bootLogs += $copy
        }
        $errorLevelLine = '^(?:\[FRIDAY\] |\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} +)?(?:ERROR|CRITICAL)\b'
        $tracebackLine  = 'Traceback \(most recent call last\)'
        $allowed = @()
        if (Test-Path $py) {
            & $py -c "import importlib.util, sys; sys.exit(3 if importlib.util.find_spec('sentence_transformers') is None else 0)"
            if ($LASTEXITCODE -eq 3) {
                $allowed += "Couldn't check Friday's health: privacy_classifier\.layer3\.error=ModuleNotFoundError: No module named 'sentence_transformers'\s*$"
            }
        }
        $bootErrors = @()
        foreach ($f in $bootLogs) {
            if (-not (Test-Path -LiteralPath $f)) { continue }
            $n = 0
            foreach ($line in (Get-Content -LiteralPath $f -Encoding UTF8)) {
                $n++
                $isTrace = $line -cmatch $tracebackLine
                if (-not $isTrace -and ($line -cnotmatch $errorLevelLine)) { continue }
                if ((-not $isTrace) -and (@($allowed | Where-Object { $line -cmatch $_ }).Count -gt 0)) { continue }
                $bootErrors += ('{0}:{1}: {2}' -f (Split-Path -Leaf $f), $n, $line.Trim())
            }
        }
        foreach ($e in ($bootErrors | Select-Object -First 40)) { Write-Host "  first-boot error: $e" }
        Check 'boot-errors' ($bootErrors.Count -eq 0) "errors: $($bootErrors.Count)"
    }

    # ── Uninstall, keeping the person's data ───────────────────────────────
    $uninstaller = Join-Path $InstallRoot 'unins000.exe'
    $hadUninstaller = Test-Path -LiteralPath $uninstaller
    if ($hadUninstaller) {
        $sentinel = Join-Path $FridayDir 'ci-sentinel.txt'
        New-Item -ItemType Directory -Force -Path $FridayDir | Out-Null
        Set-Content -LiteralPath $sentinel -Value "kept on purpose $(Get-Date -Format o)" -Encoding UTF8
        $sentinelHash = (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
        Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$(Join-Path $WorkDir 'uninstall.log')`"") | Out-Null
        $gone = (Get-Date).AddSeconds(300)
        while ((Get-Date) -lt $gone -and ((Test-Path $InstallRoot) -or (Test-Path $AppsKey))) { Start-Sleep -Seconds 3 }
        $kept = (Test-Path -LiteralPath $sentinel) -and ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -eq $sentinelHash)
        Check 'uninstall' ((-not (Test-Path $InstallRoot)) -and (-not (Test-Path $AppsKey)) -and $kept) `
              "install folder gone: $(-not (Test-Path $InstallRoot)); Apps entry gone: $(-not (Test-Path $AppsKey)); data kept byte for byte: $kept"
    }
    Save-Result
}

$failed = @($Results.Keys | Where-Object { -not $Results[$_].ok })
if ($failed.Count) { Write-Host "FAILED: $($failed -join ', ')"; exit 1 }
Write-Host "All clean-machine checks passed ($Variant)."
