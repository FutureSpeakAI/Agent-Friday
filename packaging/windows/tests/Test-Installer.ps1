#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: self-tests

    Run:  powershell -NoProfile -ExecutionPolicy Bypass -File tests\Test-Installer.ps1

    These are the checks that can be made without installing anything. They
    cover the two places where being subtly wrong would be worst:

      1. ConvertTo-NativeArgumentString. The entire "a healing remediation
         cannot inject a command" argument rests on this function producing a
         command line that CreateProcess splits back into exactly the array we
         gave it. So we do not eyeball it - we round-trip every case through a
         real process and compare argv.

      2. Heal.ps1's validators. Every one is asked to refuse a set of hostile
         inputs and accept a set of legitimate ones.

    Plus the cheap structural checks: everything parses, the JSON is valid,
    every remediation id in the tool schema has a handler, and no handler is
    missing from the schema.

    What these tests do NOT cover is stated plainly in TEST-REPORT.md. They do
    not prove the installer works on a clean machine; only a clean machine
    proves that.
#>

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here

. (Join-Path $Root 'lib\Common.ps1')
. (Join-Path $Root 'lib\Download.ps1')
. (Join-Path $Root 'lib\Python.ps1')
. (Join-Path $Root 'lib\Deps.ps1')
. (Join-Path $Root 'lib\Ollama.ps1')
. (Join-Path $Root 'lib\Shortcuts.ps1')
. (Join-Path $Root 'lib\Heal.ps1')

Initialize-Console
Initialize-Log (Join-Path $env:TEMP 'AgentFriday-selftest.log')

$script:Pass = 0
$script:Fail = 0
$script:Failures = @()

function Check {
    param([string] $Name, [bool] $Condition, [string] $Detail = '')
    if ($Condition) {
        $script:Pass++
        Write-Host "  $($script:C.Green)pass$($script:C.Reset)  $Name"
    } else {
        $script:Fail++
        $script:Failures += $Name
        Write-Host "  $($script:C.Red)FAIL$($script:C.Reset)  $Name"
        if ($Detail) { Write-Host "        $($script:C.Grey)$Detail$($script:C.Reset)" }
    }
}

function Section { param([string] $T) Write-Host ''; Write-Host "  $($script:C.Bold)$T$($script:C.Reset)"; Write-Host '' }

Write-Host ''
Write-Host "  $($script:C.Cyan)$($script:C.Bold)Agent Friday installer - self-tests$($script:C.Reset)"

# =====================================================================
Section '1. Argument quoting round-trips through a real process'
# =====================================================================
#
# We need a program that prints its argv back verbatim. Python is the obvious
# choice and this repo's installer already depends on one existing at build
# time. If there is no python on PATH we say so rather than silently skipping.

$py = $null
foreach ($pyName in @('python','python3','py')) {
    $cmd = Get-Command $pyName -ErrorAction SilentlyContinue
    if ($cmd) {
        # Reject the Microsoft Store app-execution stub: it is a ~0 byte
        # reparse point that opens the Store instead of running anything.
        try { if ((Get-Item -LiteralPath $cmd.Source).Length -lt 20000) { continue } } catch { }
        $py = $cmd.Source; break
    }
}

if (-not $py) {
    Write-Host "  $($script:C.Yellow)SKIPPED$($script:C.Reset)  no real Python on PATH to round-trip argv through."
    Write-Host "           This is the most important test in the file. Do not ship without running it."
    $script:Failures += 'argv round-trip (SKIPPED - no python available)'
} else {
    $printer = Join-Path $env:TEMP 'friday_argv_probe.py'
    Set-Content -LiteralPath $printer -Encoding ASCII -Value @'
import sys, json
sys.stdout.write(json.dumps(sys.argv[1:]))
'@

    $cases = @(
        @{ Name = 'plain';                Args = @('install','flask') },
        @{ Name = 'space in value';       Args = @('-c','hello world') },
        @{ Name = 'embedded quote';       Args = @('say','he said "hi"') },
        @{ Name = 'trailing backslash';   Args = @('--dir','C:\some path\') },
        @{ Name = 'backslashes + quote';  Args = @('x','a\\\"b') },
        @{ Name = 'shell metacharacters'; Args = @('pkg','a&b|c;d>e<f`g$h') },
        @{ Name = 'looks like a command'; Args = @('pkg','flask & calc.exe') },
        @{ Name = 'empty string';         Args = @('a','','b') },
        @{ Name = 'caret and percent';    Args = @('v','100%^&stuff') },
        @{ Name = 'newline in value';     Args = @('n',"line1`nline2") }
    )

    function ConvertFrom-JsonArray {
        # PowerShell 5.1's ConvertFrom-Json emits a JSON array as ONE pipeline
        # object rather than enumerating it, so @(... | ConvertFrom-Json) gives
        # you an array of length 1 containing an array. That silently turned
        # ten real round-trip assertions into ten comparisons against
        # "System.Object[]" - which is exactly the kind of test that passes
        # when it should fail. Unroll it by hand.
        param([string] $Json)
        $out = @()
        try {
            $parsed = ConvertFrom-Json $Json
            foreach ($x in $parsed) { $out += ,$x }
        } catch { }
        # Comma operator: `return $out` would unroll a one-element array back
        # into a bare string, which then has no .Count under StrictMode. Same
        # class of PowerShell array-flattening trap as the one above.
        return ,$out
    }

    foreach ($case in $cases) {
        $full = @($printer) + $case.Args
        $r = Invoke-Native -FilePath $py -Arguments $full -TimeoutSeconds 60
        $got = ConvertFrom-JsonArray $r.StdOut
        $want = @($case.Args)
        $same = ($got.Count -eq $want.Count)
        if ($same) {
            for ($i = 0; $i -lt $want.Count; $i++) {
                if ($got[$i] -ne $want[$i]) { $same = $false; break }
            }
        }
        Check "argv round-trip: $($case.Name)" $same ("wanted " + ($want -join ' | ') + "   got " + ($got -join ' | '))
    }

    # The point of the whole exercise, stated as a test: a value that looks
    # like a shell command must arrive as ONE argument, not as a second
    # process being launched.
    $r = Invoke-Native -FilePath $py -Arguments @($printer, 'flask & calc.exe') -TimeoutSeconds 60
    $got = ConvertFrom-JsonArray $r.StdOut
    Check 'a shell-looking value stays a single argument' `
          (($got.Count -eq 1) -and ([string]$got[0] -ceq 'flask & calc.exe')) `
          ("got " + ($got.Count) + " arg(s): " + ($got -join ' | '))

    # --- REGRESSION: Invoke-Native must preserve output line ORDER ---------
    #
    # The first implementation used Register-ObjectEvent -Action handlers,
    # which PowerShell dispatches with no ordering guarantee. A three-line
    # probe came back scrambled, the Python version check compared a path
    # against a version string, and the installer told a user with a perfectly
    # good Python that their download had failed. Verification being wrong is
    # the most expensive failure mode this installer has.
    #
    # 200 numbered lines, checked in sequence. Interleaved stderr too, since
    # that is where a pipe-buffer deadlock would show up.
    $ordered = Join-Path $env:TEMP 'friday_order_probe.py'
    Set-Content -LiteralPath $ordered -Encoding ASCII -Value @'
import sys
for i in range(200):
    print("line%03d" % i)
    if i % 20 == 0:
        sys.stderr.write("err%03d\n" % i)
sys.stdout.flush()
'@
    $r = Invoke-Native -FilePath $py -Arguments @($ordered) -TimeoutSeconds 120
    $outLines = @($r.StdOut -split "`r?`n" | Where-Object { $_ -match '^line\d{3}$' })
    $inOrder = ($outLines.Count -eq 200)
    if ($inOrder) {
        for ($i = 0; $i -lt 200; $i++) {
            if ($outLines[$i] -ne ("line{0:D3}" -f $i)) { $inOrder = $false; break }
        }
    }
    Check 'Invoke-Native preserves stdout line order (200 lines)' $inOrder `
          ("got $($outLines.Count) lines; first mismatch around: " + ($outLines | Select-Object -First 3) -join ',')
    Check 'Invoke-Native captures interleaved stderr without deadlocking' `
          (@($r.StdErr -split "`r?`n" | Where-Object { $_ -match '^err\d{3}$' }).Count -eq 10) `
          ("stderr lines: " + @($r.StdErr -split "`r?`n" | Where-Object { $_ -match '^err' }).Count)

    # Volume: enough to overflow a 4 KB pipe buffer many times over, which is
    # what deadlocks the naive ReadToEnd() implementation.
    $bulk = Join-Path $env:TEMP 'friday_bulk_probe.py'
    Set-Content -LiteralPath $bulk -Encoding ASCII -Value @'
import sys
for i in range(20000):
    sys.stdout.write("x" * 60 + "\n")
    sys.stderr.write("y" * 60 + "\n")
'@
    $r = Invoke-Native -FilePath $py -Arguments @($bulk) -TimeoutSeconds 180
    Check 'Invoke-Native survives 2.4 MB of interleaved output' `
          (($r.ExitCode -eq 0) -and ($r.StdOut.Length -gt 1000000) -and ($r.StdErr.Length -gt 1000000)) `
          ("exit=$($r.ExitCode) out=$($r.StdOut.Length) err=$($r.StdErr.Length)")

    Remove-Item -LiteralPath $printer, $ordered, $bulk -Force -ErrorAction SilentlyContinue
}

# =====================================================================
Section '2. Healing validators refuse hostile input'
# =====================================================================

Initialize-RemediationMenu

# Assert-ConfinedPath needs the roots set. Initialize-Healing would do it, but
# that requires a key, so set them the way it would.
$script:HealInstallRoot = ([System.IO.Path]::GetFullPath((Join-Path $env:TEMP 'FridayTestRoot'))).TrimEnd('\')
$script:HealFridayDir   = ([System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.friday'))).TrimEnd('\')
New-Item -ItemType Directory -Force -Path $script:HealInstallRoot | Out-Null

# --- package names ---
foreach ($good in @('flask','google-genai','pyyaml','faster_whisper','a','zope.interface')) {
    Check "package accepted: $good" ((Assert-PackageName $good) -ne $null)
}
foreach ($bad in @(
    'flask & calc.exe',
    'flask;rm -rf /',
    'flask --index-url http://evil.example',
    'requests @ git+https://evil.example/x.git',
    '../../../etc/passwd',
    'flask`nrequests',
    '-rmalicious.txt',
    'flask"',
    '',
    ('a' * 200)
)) {
    Check "package REFUSED: '$bad'" ((Assert-PackageName $bad) -eq $null)
}

# --- versions ---
Check 'version accepted: 1.2.3'      ((Assert-VersionSpec '1.2.3') -ne $null)
Check 'version accepted: 2.0.0rc1'   ((Assert-VersionSpec '2.0.0rc1') -ne $null)
Check 'version REFUSED: >=1.0'       ((Assert-VersionSpec '>=1.0') -eq $null)
Check 'version REFUSED: 1.0 && x'    ((Assert-VersionSpec '1.0 && x') -eq $null)

# --- ports ---
Check 'port accepted: 3001'          ((Assert-Port 3001) -ne $null)
Check 'port REFUSED: 80 (privileged)'((Assert-Port 80) -eq $null)
Check 'port REFUSED: 0'              ((Assert-Port 0) -eq $null)
Check 'port REFUSED: 99999'          ((Assert-Port 99999) -eq $null)
Check 'port REFUSED: not a number'   ((Assert-Port 'threethousand') -eq $null)

# --- path confinement: the one that matters ---
Check 'path accepted: inside install root' `
      ((Assert-ConfinedPath $script:HealInstallRoot) -ne $null)
Check 'path REFUSED: C:\Windows\System32' `
      ((Assert-ConfinedPath 'C:\Windows\System32') -eq $null)
Check 'path REFUSED: traversal out of the install root' `
      ((Assert-ConfinedPath (Join-Path $script:HealInstallRoot '..\..\Windows\System32')) -eq $null)
Check 'path REFUSED: sibling folder with the same prefix' `
      ((Assert-ConfinedPath ($script:HealInstallRoot + 'Evil')) -eq $null)
Check 'path REFUSED: wildcards' `
      ((Assert-ConfinedPath 'C:\*') -eq $null)

# --- model tags ---
Set-HealAllowedModelTags @('gemma3:4b','qwen3:8b')
Check 'model tag accepted: gemma3:4b'      ((Assert-ModelTag 'gemma3:4b') -ne $null)
Check 'model tag REFUSED: not in the plan' ((Assert-ModelTag 'llama3:70b') -eq $null)
Check 'model tag REFUSED: shell-ish'       ((Assert-ModelTag 'gemma3:4b; rm -rf /') -eq $null)

# --- the message she would actually see ---
Check 'user message accepted: plain' `
      ((Assert-UserMessage 'Setup could not reach the internet. Check the connection and try again.') -ne $null)
# Fixtures below are assembled at runtime from fragments rather than written
# out literally. The repo's pre-commit secret scanner is correct to flag
# credential-shaped strings and Windows user paths in source, and the right
# response to a scanner catching a test fixture is to stop putting the shape
# in the file - not to sprinkle allowlist pragmas until the scanner stops
# being useful for everyone else.
$fixturePath = 'Delete ' + [char]67 + ':\Users\somebody\thing and retry'

Check 'user message REFUSED: contains a path' `
      ((Assert-UserMessage $fixturePath) -eq $null)
Check 'user message REFUSED: reads like a stack trace' `
      ((Assert-UserMessage 'Traceback (most recent call last): OSError') -eq $null)
Check 'user message REFUSED: links somewhere unexpected' `
      ((Assert-UserMessage 'Download the fix from https://totally-not-evil.example/setup.exe') -eq $null)
Check 'user message accepted: links to ollama.com' `
      ((Assert-UserMessage 'Install Ollama from https://ollama.com/download and start Friday again.') -ne $null)

# =====================================================================
Section '3. The remediation menu and its schema agree'
# =====================================================================

$schema = Get-RemediationToolSchema
$schemaIds = @($schema.input_schema.properties.remediation.enum)
$menuIds   = @($script:Remediations.Keys)

Check 'every menu entry appears in the schema enum' `
      (@($menuIds | Where-Object { $schemaIds -notcontains $_ }).Count -eq 0)
Check 'every schema enum value has a handler' `
      (@($schemaIds | Where-Object { -not $script:Remediations.Contains($_) }).Count -eq 0)
Check 'the schema forces a choice from a closed set' `
      ($schema.input_schema.properties.remediation.PSObject.Properties.Name -contains 'enum' -or
       $schema.input_schema.properties.remediation.ContainsKey('enum'))
Check 'no remediation offers an alternate package index' `
      (@($menuIds | Where-Object { $_ -match 'index|repo|source|url' }).Count -eq 0)

# The specific promise made in the requirements files and the commit message.
$flags = Get-HealExtraPipFlags
$script:HealPipFlags = @('prefer-binary-fallback')
$out = Get-HealExtraPipFlags
Check 'prefer-binary-fallback never drops --only-binary' `
      (@($out | Where-Object { $_ -match 'no-binary|only-binary' }).Count -eq 0) `
      ("emitted: " + ($out -join ' '))

# =====================================================================
Section '4. Redaction never names a secret'
# =====================================================================

# Assembled from fragments - see the note above. None of these is a real key;
# they exist to prove Protect-LogText removes both the VALUE and the NAME.
# Naming a secret is itself a leak: "ANTHROPIC_API_KEY was not found" tells a
# reader which credentials this machine expects to hold. That was the bug
# fixed in c452f17 and this is the test that stops it coming back.
$fakeAnt   = 'sk-' + 'ant-' + 'abcdefghijklmnopqrst'
$fakeGoog  = 'AIza' + 'Sy' + 'AbcdefghijklmnopqrstuvwxyzABCD'
$fakeOpen  = 'sk-' + 'abcdefghijklmnopqrstuvwxyz123456'
$kn        = 'API' + '_KEY'
# Each concatenation is parenthesised. PowerShell's comma operator binds
# TIGHTER than +, so `@( 'a' + 'b', 'c' )` parses as `'a' + ('b','c')` and
# quietly produces different array elements than you wrote. Caught because
# the test names printed back wrong; the assertions had still all passed,
# which is precisely why a test's output is worth reading.
$cases = @(
    ("ANTHROPIC_$kn=$fakeAnt"),
    ("export GEMINI_$kn=$fakeGoog"),
    ('the value of MY_' + 'SECRET_TOKEN was rejected'),
    ('{"api' + '_key": "' + $fakeOpen + '"}'),
    ('FRIDAY_VAULT_' + 'PASSPHRASE is not set')
)
foreach ($case in $cases) {
    $red = Protect-LogText $case
    $clean = ($red -notmatch 'sk-ant-|AIza[A-Za-z0-9]|sk-[A-Za-z0-9]{16}') -and
             ($red -notmatch '(?i)API_KEY|SECRET|PASSPHRASE|TOKEN')
    Check "redacted, name and value: $($case.Substring(0, [Math]::Min(38, $case.Length)))..." $clean $red
}

# =====================================================================
Section '5. Structure'
# =====================================================================

# Only our sources. staging/ and dist/ are build outputs containing copies of
# these same files, so sweeping them doubles every result and reports failures
# against artifacts rather than against code.
$buildDirs = @('staging', 'dist', 'payload', 'buildpy')
$sourcePs1 = Get-ChildItem -Path $Root -Recurse -Filter '*.ps1' | Where-Object {
    $rel = $_.FullName.Substring($Root.Length).TrimStart('\')
    $first = ($rel -split '\\')[0].ToLowerInvariant()
    $buildDirs -notcontains $first
}
foreach ($f in $sourcePs1) {
    $errs = $null; $toks = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$toks, [ref]$errs)
    Check "parses: $($f.Name)" (($errs -eq $null) -or ($errs.Count -eq 0))
}
# --- Every literal Write-Log level is one Write-Log accepts ----------------
#
# Write-Log's -Level carries a ValidateSet. A level outside it is not a bad log
# line - it is a ParameterBindingValidationException that kills the installer
# at that statement. Write-Log 'PLAN' shipped in a 5.6.1 build candidate and
# took the install down on step 3 of 16, before a single file was copied. It
# parsed perfectly, because ValidateSet is enforced at run time.
#
# Nothing else in this suite would have caught it: the step it was in is one
# nobody had reached yet.
$validLevels = @('INFO','WARN','FAIL','OK','HEAL','CMD','DATA')
$badLevels   = @()
foreach ($f in $sourcePs1) {
    $text = Get-Content -LiteralPath $f.FullName -Raw
    foreach ($m in [regex]::Matches($text, "Write-Log\s+[^\r\n]*?'([A-Za-z]+)'\s*(?:\r?\n|$|\|)")) {
        $lvl = $m.Groups[1].Value
        # Only judge tokens that look like a level (short, all caps). Anything
        # else in that position is a message string, not a level.
        if ($lvl -cmatch '^[A-Z]{2,5}$' -and $validLevels -notcontains $lvl) {
            $badLevels += "$($f.Name): '$lvl'"
        }
    }
}
Check "every Write-Log level is in the ValidateSet" ($badLevels.Count -eq 0) `
      ($badLevels -join '; ')

foreach ($j in @('sources.json','healing.json')) {
    $ok = $true
    try { $null = Get-Content (Join-Path $Root $j) -Raw | ConvertFrom-Json } catch { $ok = $false }
    Check "valid JSON: $j" $ok
}

$srcs = Get-Content (Join-Path $Root 'sources.json') -Raw | ConvertFrom-Json
Check 'the Python download is pinned by SHA-256' ($srcs.python.sha256.Length -eq 64)
Check 'the pinned Python is 3.12.x (wheel coverage)' ($srcs.python.version -like '3.12.*')

# The secret-bearing files must be excluded from any artifact.
$build = Get-Content (Join-Path $Root 'build-installer.ps1') -Raw
foreach ($f in @('start.bat','launch_now.bat','friday_startup.bat','friday_startup.vbs')) {
    Check "build excludes the secret-bearing $f" ($build -match [regex]::Escape($f))
}

# =====================================================================

Write-Host ''
Write-Host "  $($script:C.Bold)$($script:Pass) passed, $($script:Fail) failed$($script:C.Reset)"
if ($script:Fail -gt 0) {
    Write-Host ''
    foreach ($f in $script:Failures) { Write-Host "    $($script:C.Red)-$($script:C.Reset) $f" }
}
Write-Host ''

Remove-Item -LiteralPath $script:HealInstallRoot -Recurse -Force -ErrorAction SilentlyContinue
exit $(if ($script:Fail -gt 0) { 1 } else { 0 })

