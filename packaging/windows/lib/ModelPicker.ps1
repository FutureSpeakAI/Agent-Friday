#Requires -Version 5.1
<#
    Agent Friday - Windows installer :: ModelPicker.ps1

    What this computer can run, and which Bonsai models fit it. The installer's
    model page shows exactly what this returns and nothing else.

    The decision procedure is the `pick` section of
    src/agent_friday/resources/bonsai2-tiers.json (the same file the app ships).
    The thresholds below restate that file's `when` clauses; a test
    (tests/unit/test_installer_model_picker.py) fails if they drift.

    Standalone on purpose: it depends on nothing else in lib\, so the wizard can
    run it before anything has been installed. Everything is read locally and
    nothing is sent anywhere.

    Unknown is not a number. A graphics card whose memory cannot be read is
    treated as having none, so the pick falls to the processor tier rather than
    promising a speed the machine cannot give. Windows reports a card's memory
    through a 32-bit field that tops out at 4 GB, so an AMD or Intel card is
    read from the driver's own 64-bit value where it exists, and otherwise left
    unknown.
#>

Set-StrictMode -Version 2.0

# ---- constants restated from bonsai2-tiers.json (a test holds them) -------
$script:Pick = @{
    OsReserveMib       = 6144    # reserves.os_reserve_mib.windows
    DisplayReserveMib  = 2560    # reserves.display_reserve_mib.windows
    FridayFootprintMib = 1500    # reserves.friday_footprint_mib.value
    DiskFloorMib       = 10240   # reserves.disk_floor_mib.value
    FridayDiskGb       = 8       # install.ps1's preflight estimate ($neededGb) for Agent Friday itself
    RuntimeAllowanceMib = 650    # the largest runtime asset the app may fetch (CUDA build + its runtime)
    ComputeBufferMib   = 400     # reserves.compute_buffer_mib.ub_512
    WeightsPtq10Mib    = 5671    # pick.derived.weights_mib (PTQ1_0)
    WeightsPq20Mib     = 6872    # pick.derived.weights_mib (PQ2_0)
    LesserHeadroomMib  = 1475    # KV + compute + runtime for the older, smaller models
    MinRamFloorMib     = 15500   # T1: 16 GB machines report a little under 16384
    MinRamBigMib       = 30000   # T2
    BandwidthT2GbS     = 70      # T2
}

function Get-DriveFreeMib {
    <#  Free space, in MiB, on the drive that holds $Path (which need not exist). #>
    param([Parameter(Mandatory)][string] $Path)
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
        $root = [System.IO.Path]::GetPathRoot($full)
        $di = New-Object System.IO.DriveInfo($root)
        return [int64]([math]::Floor($di.AvailableFreeSpace / 1MB))
    } catch { return [int64]0 }
}

function Get-NvidiaSmiPath {
    $cmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("$env:SystemRoot\System32\nvidia-smi.exe",
                     "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe")) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

function Test-ProcessorFeature {
    param([Parameter(Mandatory)][int] $Feature)
    try {
        if (-not ('AgentFriday.Cpu' -as [type])) {
            Add-Type -Namespace 'AgentFriday' -Name 'Cpu' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern bool IsProcessorFeaturePresent(uint ProcessorFeature);
'@ -ErrorAction Stop
        }
        return [bool][AgentFriday.Cpu]::IsProcessorFeaturePresent([uint32]$Feature)
    } catch { return $false }
}

function Test-FridayIsRunning {
    <#  True when a process runs from Friday's install folder or from the model
        runtime under ~\.friday. Its own seats are what an earlier Friday keeps
        in video memory; setup stops it before it installs anything, so that use
        must not make a good card look full. #>
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'AgentFriday') }
    if ($env:USERPROFILE) { $roots += (Join-Path $env:USERPROFILE '.friday\runtime') }
    foreach ($p in @(Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if (-not $p.Path) { continue }
            foreach ($r in $roots) {
                if ($p.Path.StartsWith($r + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
            }
        } catch { }
    }
    return $false
}

function Get-GpuFacts {
    <#  The largest usable card: name, vendor, total and idle-used VRAM in MiB.
        vram_known is $false when the number is a guess. #>
    $gpu = [ordered]@{ name = ''; vendor = ''; vram_mib = 0; idle_used_mib = 0; vram_known = $false; note = '' }

    $smi = Get-NvidiaSmiPath
    if ($smi) {
        try {
            $rows = @(& $smi '--query-gpu=name,memory.total,memory.used' '--format=csv,noheader,nounits' 2>$null)
            $best = $null
            foreach ($line in $rows) {
                $f = @(("$line").Split(',') | ForEach-Object { $_.Trim() })
                if ($f.Count -lt 3 -or $f[1] -notmatch '^\d+$') { continue }
                $total = [int64]$f[1]
                if ($null -eq $best -or $total -gt $best.vram_mib) {
                    $used = 0
                    if ($f[2] -match '^\d+$') { $used = [int64]$f[2] }
                    $best = [ordered]@{ name = $f[0]; vendor = 'nvidia'; vram_mib = $total;
                                        idle_used_mib = $used; vram_known = $true; note = 'nvidia-smi' }
                }
            }
            if ($best) {
                if ($best.idle_used_mib -gt 0 -and (Test-FridayIsRunning)) {
                    $best.note = 'nvidia-smi; an earlier Friday is running, so the memory it holds is not counted'
                    $best.idle_used_mib = 0
                }
                return $best
            }
        } catch { }
    }

    try {
        $cards = @(Get-CimInstance Win32_VideoController -ErrorAction Stop |
                   Where-Object { $_.Name -and $_.Name -notmatch 'Basic Display|Remote|Virtual|Hyper-V|Parsec' })
        $pick = $null
        foreach ($c in $cards) {
            $adapter = 0
            if ($c.AdapterRAM) { $adapter = [int64]$c.AdapterRAM }
            if ($adapter -lt 0) { $adapter += 4294967296 }
            if ($null -eq $pick -or $adapter -gt $pick.adapter) { $pick = @{ card = $c; adapter = $adapter } }
        }
        if ($pick) {
            $c = $pick.card
            $vendor = 'other'
            if ($c.Name -match 'NVIDIA|GeForce|RTX|Quadro') { $vendor = 'nvidia' }
            elseif ($c.Name -match 'AMD|Radeon') { $vendor = 'amd' }
            elseif ($c.Name -match 'Intel') { $vendor = 'intel' }
            $gpu.name = [string]$c.Name
            $gpu.vendor = $vendor
            # Win32_VideoController.AdapterRAM is a 32-bit field: a 12 GB card
            # reads as 4 GB or less. Ask the driver's own 64-bit value first.
            $real = Get-DriverVideoMemoryMib -CardName $c.Name
            if ($real -gt 0) {
                $gpu.vram_mib = $real; $gpu.vram_known = $true; $gpu.note = 'driver memory size'
            } elseif ($vendor -eq 'nvidia') {
                # nvidia-smi is missing, so the driver is not installed properly: do not trust the field.
                $gpu.note = 'NVIDIA card without nvidia-smi; memory unknown'
            } else {
                $gpu.note = 'Windows reports at most 4 GB for this card; real memory unknown'
            }
        }
    } catch { }
    return $gpu
}

function Get-DriverVideoMemoryMib {
    param([string] $CardName)
    try {
        $base = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
        foreach ($k in @(Get-ChildItem -LiteralPath $base -ErrorAction Stop | Where-Object { $_.PSChildName -match '^\d{4}$' })) {
            $p = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $p) { continue }
            $desc = ''
            if ($p.PSObject.Properties.Match('DriverDesc').Count) { $desc = [string]$p.DriverDesc }
            if ($desc -ne $CardName) { continue }
            if ($p.PSObject.Properties.Match('HardwareInformation.qwMemorySize').Count) {
                $v = $p.'HardwareInformation.qwMemorySize'
                if ($v -is [byte[]]) { $v = [BitConverter]::ToInt64($v, 0) }
                if ([int64]$v -gt 0) { return [int64]([math]::Floor([int64]$v / 1MB)) }
            }
        }
    } catch { }
    return [int64]0
}

function Get-MemoryBandwidthGbS {
    <#  An estimate from the installed memory modules (speed x 8 bytes x channels),
        or 0 when it cannot be read. Dual channel is assumed for two or more
        modules; that is an estimate and the pick treats it as one. #>
    try {
        $mods = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop)
        if ($mods.Count -eq 0) { return 0.0 }
        $speed = 0
        foreach ($m in $mods) {
            $s = 0
            if ($m.PSObject.Properties.Match('ConfiguredClockSpeed').Count -and $m.ConfiguredClockSpeed) { $s = [int]$m.ConfiguredClockSpeed }
            elseif ($m.Speed) { $s = [int]$m.Speed }
            if ($s -gt $speed) { $speed = $s }
        }
        $channels = 1
        if ($mods.Count -ge 2) { $channels = 2 }
        return [math]::Round($speed * 8 * $channels / 1000.0, 1)
    } catch { return 0.0 }
}

function Get-HardwareFacts {
    <#  Everything the pick reads. -ModelsPath is where model files will be
        written (the disk that matters). #>
    param([string] $ModelsPath = '')
    if (-not $ModelsPath) { $ModelsPath = Join-Path $env:USERPROFILE '.friday' }

    $ramMib = [int64]0
    try { $ramMib = [int64]([math]::Floor((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1MB)) } catch { }
    $cores = 0; $threads = 0; $cpuName = ''
    try {
        foreach ($c in @(Get-CimInstance Win32_Processor -ErrorAction Stop)) {
            $cores += [int]$c.NumberOfCores
            $threads += [int]$c.NumberOfLogicalProcessors
            if (-not $cpuName) { $cpuName = ([string]$c.Name).Trim() }
        }
    } catch { }
    $gpu = Get-GpuFacts
    return [ordered]@{
        os_family       = 'windows'
        ram_mib         = $ramMib
        cpu_name        = $cpuName
        cpu_cores       = $cores
        cpu_threads     = $threads
        avx2            = (Test-ProcessorFeature -Feature 40)   # PF_AVX2_INSTRUCTIONS_AVAILABLE
        bandwidth_gb_s  = (Get-MemoryBandwidthGbS)
        gpu_name        = $gpu.name
        gpu_vendor      = $gpu.vendor
        vram_mib        = [int64]$gpu.vram_mib
        vram_known      = [bool]$gpu.vram_known
        gpu_note        = $gpu.note
        idle_used_mib   = [int64]$gpu.idle_used_mib
        disk_free_mib   = (Get-DriveFreeMib -Path $ModelsPath)
    }
}

function Get-UsableVramMib {
    param([Parameter(Mandatory)] $Facts)
    if (-not $Facts.vram_known -or [int64]$Facts.vram_mib -le 0) { return [int64]0 }
    $reserve = [math]::Max([int64]$script:Pick.DisplayReserveMib, [int64]$Facts.idle_used_mib)
    return [int64]([int64]$Facts.vram_mib - $reserve)
}

function Get-RamBudgetMib {
    param([Parameter(Mandatory)] $Facts)
    return [int64]([int64]$Facts.ram_mib - $script:Pick.OsReserveMib - $script:Pick.FridayFootprintMib)
}

function Test-CardPrefersPq20 {
    <#  tiers.json pick.packing_rule: PQ2_0 on Ampere, Hopper, Blackwell and RDNA
        parts; PTQ1_0 on Ada (it measured faster there), on the L4 and on every
        processor tier. #>
    param([string] $GpuName)
    if (-not $GpuName) { return $false }
    return [bool]($GpuName -match 'RTX\s*(30|50)\d\d|RTX\s*A\d{4}|RTX\s*PRO|\bA(100|40|30)\b|\bH(100|200)\b|\bB(100|200)\b|RX\s*[679]\d00')
}

function Select-BonsaiTier {
    <#  The first matching rule of tiers.json `pick.rules`, restricted to the
        rows that exist on Windows. Returns the rule id and its output. #>
    param([Parameter(Mandatory)] $Facts)
    $usable = Get-UsableVramMib -Facts $Facts
    $ram = [int64]$Facts.ram_mib
    $avx2 = [bool]$Facts.avx2
    $cores = [int]$Facts.cpu_cores
    $pq = (Test-CardPrefersPq20 -GpuName $Facts.gpu_name)

    function Row($id, $pack, $ngl, $ctx, $kv, $slots, $batch, $mm, $profile) {
        return [ordered]@{ tier = $id; packing = $pack; n_gpu_layers = $ngl; context = $ctx; kv = $kv
                           slots = $slots; batch = $batch; mmproj = $mm; profile = $profile }
    }

    if ($usable -ge 28000) { return Row 'T8' 'PQ2_0' 99 262144 'f16' 2 '-b 4096 -ub 2048' $true 'gpu' }
    $byRule = 'PTQ1_0'; if ($pq) { $byRule = 'PQ2_0' }
    if ($usable -ge 20000) { return Row 'T7' $byRule 99 196608 'q8_0' 2 '-b 4096 -ub 2048' $true 'gpu' }
    if ($usable -ge 13000) { return Row 'T6' $byRule 99 131072 'q8_0' 2 '-b 4096 -ub 2048' $true 'gpu' }
    if ($usable -ge 9000)  { return Row 'T5' 'PTQ1_0' 99 131072 'q4_0' 1 '-b 4096 -ub 512' 'on_demand' 'gpu' }
    if ($usable -ge 6300)  { return Row 'T4' 'PTQ1_0' 99 16384 'q8_0' 1 '-b 2048 -ub 512' $false 'gpu' }
    if ($usable -ge 3000 -and $ram -ge $script:Pick.MinRamFloorMib -and $avx2) {
        # estimate_method.n_gpu_layers: floor(64 * (usable - kv - compute) / weights), clamped to [0, 64]
        $kv = 256   # 8192 tokens at 32 KiB per token (q8_0)
        $ngl = [int][math]::Floor(64 * ($usable - $kv - $script:Pick.ComputeBufferMib) / $script:Pick.WeightsPtq10Mib)
        $ngl = [math]::Max(0, [math]::Min(64, $ngl))
        if ($ngl -ge 64) { $ngl = 99 }
        return Row 'T4a' 'PTQ1_0' $ngl 8192 'q8_0' 1 '-b 2048 -ub 512' $false 'cpu'
    }
    if ($ram -ge $script:Pick.MinRamBigMib -and $avx2 -and [double]$Facts.bandwidth_gb_s -ge $script:Pick.BandwidthT2GbS) {
        return Row 'T2' 'PTQ1_0' 0 32768 'f16' 1 '-b 2048 -ub 512' $false 'cpu'
    }
    if ($ram -ge $script:Pick.MinRamFloorMib -and $avx2 -and $cores -ge 4) {
        return Row 'T1' 'PTQ1_0' 0 8192 'f16' 1 '-b 2048 -ub 512' $false 'cpu'
    }
    return [ordered]@{ tier = 'T0'; packing = $null; n_gpu_layers = 0; context = 0; kv = ''
                       slots = 0; batch = ''; mmproj = $false; profile = 'below-floor' }
}

function Get-FileSizeLine {
    param([double] $Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
    return ('{0:N0} MB' -f ($Bytes / 1MB))
}

function New-Option {
    param([string] $Seat, $Model, $File, [bool] $Older, [string] $Note)
    $bytes = [double]$File.bytes
    return [ordered]@{
        seat = $Seat; id = [string]$Model.id; packing = [string]$File.packing; label = [string]$Model.label
        bytes = [int64]$bytes; size_text = (Get-FileSizeLine $bytes); recommended = $false
        older_generation = $Older; companions = $false; note = $Note; serve = $null
    }
}

function Get-ModelOptions {
    <#  The models that fit, per seat, with the recommendation marked.

        Returns @{ tier; pick; deep = @(...); fast = @(...); notes = @(...) }.
        Each option: seat, id, packing, label, bytes (everything that will be
        downloaded for it), size_text, recommended, older_generation, note,
        companions, serve (the pick's serving numbers for the 27B).

        An option is selectable only if the machine can hold it AND the disk keeps
        the 10 GB floor after Agent Friday itself and the download (residency
        rule R8). Every option that does not fit is returned in `unfit`, with the
        reason in plain words, so no seat is ever empty without saying why.
        When a seat has nothing selectable, `notes` carries one plain message
        with the real numbers. #>
    param([Parameter(Mandatory)] $Facts,
          [Parameter(Mandatory)][string] $ShortlistPath,
          [string] $VoiceFrontPath = '')

    $short = Get-Content -LiteralPath $ShortlistPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $pick = Select-BonsaiTier -Facts $Facts
    $ramBudget = Get-RamBudgetMib -Facts $Facts
    $diskFree = [int64]$Facts.disk_free_mib
    $notes = @()
    $deep = @(); $fast = @()
    $unfit = New-Object System.Collections.Generic.List[object]
    $fridayDiskMib = [int64]$script:Pick.FridayDiskGb * 1024
    $floorGb = [int]($script:Pick.DiskFloorMib / 1024)

    function Fits-Disk([double] $bytes) {
        return (($diskFree - $fridayDiskMib - [math]::Ceiling($bytes / 1MB) - $script:Pick.RuntimeAllowanceMib) -ge $script:Pick.DiskFloorMib)
    }

    function Add-Unfit([string] $seat, [string] $id, [string] $label, [double] $bytes, [string] $kind, [string] $reason) {
        $unfit.Add([ordered]@{ seat = $seat; id = $id; label = $label; size_text = (Get-FileSizeLine $bytes)
                               kind = $kind; reason = $reason })
    }

    function Disk-Reason([double] $bytes) {
        # Short on purpose: it sits beside the model's name in the list. The
        # arithmetic (Agent Friday's own space, the floor) is in the message below.
        $gb = [int][math]::Ceiling(($fridayDiskMib + [math]::Ceiling($bytes / 1MB) + $script:Pick.RuntimeAllowanceMib + $script:Pick.DiskFloorMib) / 1024)
        return ('needs {0} GB free' -f $gb)
    }

    function Ram-Reason([double] $modelMib) {
        $gb = [int][math]::Ceiling(($modelMib + $script:Pick.OsReserveMib + $script:Pick.FridayFootprintMib) / 1024)
        return ('needs {0} GB of memory' -f $gb)
    }

    function Find-File($model, $packing) {
        foreach ($f in @($model.files)) { if ($f.packing -eq $packing) { return $f } }
        return $null
    }

    foreach ($m in @($short.models)) {
        $roles = @($m.roles)
        $isOlder = ($m.PSObject.Properties.Match('generation_note').Count -gt 0 -and $m.generation_note)
        if ($m.id -eq 'bonsai2:27b') {
            if ($pick.tier -eq 'T0') {
                $d27 = $null
                foreach ($cand in @($m.files)) { if ($cand.PSObject.Properties.Match('default').Count -gt 0 -and $cand.default) { $d27 = $cand } }
                if ($d27) {
                    $why = 'needs 16 GB of memory and a processor from the last several years'
                    if ([int64]$Facts.ram_mib -lt $script:Pick.MinRamFloorMib) { $why = 'needs 16 GB of memory' }
                    elseif (-not [bool]$Facts.avx2) { $why = 'needs a processor with AVX2 support' }
                    elseif ([int]$Facts.cpu_cores -lt 4) { $why = 'needs a processor with at least 4 cores' }
                    Add-Unfit 'deep_thinker' ([string]$m.id) 'Bonsai 2 27B' ([double]$d27.bytes) 'memory' $why
                }
                continue
            }
            $f = Find-File $m $pick.packing
            if (-not $f) { continue }
            $bytes = [double]$f.bytes
            $withComp = $false
            if ($pick.mmproj -eq $true -or $pick.mmproj -eq 'on_demand') { $withComp = $true }
            if ($withComp) { foreach ($c in @($m.companions)) { $bytes += [double]$c.bytes } }
            if (-not (Fits-Disk $bytes)) { Add-Unfit 'deep_thinker' ([string]$m.id) 'Bonsai 2 27B' $bytes 'disk' (Disk-Reason $bytes); continue }
            $kind = 'on the graphics card'
            if ($pick.profile -eq 'cpu') { $kind = 'on the processor' }
            if ($pick.tier -eq 'T4a') { $kind = 'split between the graphics card and the processor' }
            $deep += [ordered]@{
                seat = 'deep_thinker'; id = $m.id; packing = $pick.packing; label = 'Bonsai 2 27B'
                bytes = [int64]$bytes; size_text = (Get-FileSizeLine $bytes); recommended = $true
                older_generation = $false; companions = $withComp
                note = "The model Friday is tuned for. Runs $kind on this computer."
                serve = $pick
            }
            continue
        }
        if ($m.id -notlike 'ternary-bonsai:*') { continue }
        $f = $null
        foreach ($cand in @($m.files)) {
            if ($cand.PSObject.Properties.Match('default').Count -gt 0 -and $cand.default) { $f = $cand }
        }
        if (-not $f) { continue }
        $bytes = [double]$f.bytes
        $need = [math]::Ceiling($bytes / 1MB) + $script:Pick.LesserHeadroomMib
        $isLesser = ($roles -contains 'lesser_brain')
        if ($ramBudget -lt $need) {
            if ($isLesser) { Add-Unfit 'deep_thinker' ([string]$m.id) ([string]$m.label) $bytes 'memory' (Ram-Reason $need) }
            continue
        }
        if (-not (Fits-Disk $bytes)) {
            if ($isLesser) { Add-Unfit 'deep_thinker' ([string]$m.id) ([string]$m.label) $bytes 'disk' (Disk-Reason $bytes) }
            continue
        }
        if ($isLesser) {
            $deep += (New-Option -Seat 'deep_thinker' -Model $m -File $f -Older $isOlder `
                        -Note 'Lighter than Bonsai 2 27B and less capable. An earlier Bonsai generation.')
        }
    }

    # The fast responder is the local VOICE FRONT: the Qwen3 models the voice
    # stack serves (voice_front.FRONT_MODELS), not a Bonsai model. Each choice
    # also downloads the speech ear and its runtime, and the size shown includes
    # them. A front is listed only if it fits (memory and the disk floor).
    #   solo         (4B)   the deep thinker is parked while a call runs
    #   co_resident  (1.7B) stays loaded beside the deep thinker
    # Recommended: the 4B where there is room to spare (a big card, or 16 GB of
    # memory budget), else the 1.7B, the one that fits beside the deep thinker
    # on smaller machines. This restates voice_front's own roles.
    if ($VoiceFrontPath -and (Test-Path -LiteralPath $VoiceFrontPath)) {
        $vf = Get-Content -LiteralPath $VoiceFrontPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $earBytes = [double]0
        foreach ($c in @($vf.companions)) { $earBytes += [double]$c.bytes }
        foreach ($front in @($vf.fronts)) {
            $bytes = [double]$front.front_bytes + $earBytes
            $need = [math]::Ceiling([double]$front.front_bytes / 1MB) + $script:Pick.LesserHeadroomMib
            if ($ramBudget -lt $need) {
                Add-Unfit 'fast_responder' ([string]$front.id) ([string]$front.label) $bytes 'memory' (Ram-Reason $need)
                continue
            }
            if (-not (Fits-Disk $bytes)) {
                Add-Unfit 'fast_responder' ([string]$front.id) ([string]$front.label) $bytes 'disk' (Disk-Reason $bytes)
                continue
            }
            $earMb = [math]::Round($earBytes / 1MB)
            $fast += [ordered]@{
                seat = 'fast_responder'; id = [string]$front.id; packing = 'Q4_K_M'; label = [string]$front.label
                bytes = [int64]$bytes; size_text = (Get-FileSizeLine $bytes); recommended = $false
                older_generation = $false; companions = $false
                note = ("{0} Includes the speech ear ({1} MB)." -f $front.note, $earMb)
                serve = $null; role = [string]$front.role
            }
        }
        $roomy = ((Get-UsableVramMib -Facts $Facts) -ge 13000) -or ($ramBudget -ge 16000)
        $pickId = 'qwen3-1.7b'
        if ($roomy -and ($fast | Where-Object { $_.id -eq 'qwen3-4b-instruct-2507' })) { $pickId = 'qwen3-4b-instruct-2507' }
        foreach ($o in $fast) { $o.recommended = ($o.id -eq $pickId) }
        if ($fast.Count -gt 0 -and -not ($fast | Where-Object { $_.recommended })) { $fast[0].recommended = $true }
    }

    # Recommendations. Deep: Bonsai 2 27B when it fits, else the largest lighter model.
    if ($deep.Count -gt 0 -and -not ($deep | Where-Object { $_.recommended })) {
        $big = $deep | Sort-Object { $_.bytes } -Descending | Select-Object -First 1
        $big.recommended = $true
    }

    # Order: recommended first within each seat, then by size.
    $deep = @($deep | Sort-Object @{ Expression = { -[int][bool]$_.recommended } }, @{ Expression = { $_.bytes } })
    $fast = @($fast | Sort-Object @{ Expression = { -[int][bool]$_.recommended } }, @{ Expression = { $_.bytes } })

    if ($deep.Count -eq 0 -or $fast.Count -eq 0) {
        $diskStopped = @($unfit | Where-Object { $_.kind -eq 'disk' }).Count -gt 0
        if ($pick.tier -eq 'T0') {
            $notes += 'This computer is below what local models need (16 GB of memory and a recent processor). A cloud model works on any computer, and you can add local models later in Settings -> Models.'
        } elseif ($diskStopped) {
            $what = 'no local model fits right now'
            if ($deep.Count -eq 0 -and $fast.Count -gt 0) { $what = 'no deep thinker fits right now, and both jobs need a local model' }
            if ($fast.Count -eq 0 -and $deep.Count -gt 0) { $what = 'no fast responder fits right now, and both jobs need a local model' }
            $notes += ('This drive has {0:N1} GB free. Agent Friday itself needs about {1} GB, and local models need their size plus {2} GB left free afterwards so Windows keeps working, so {3}. ' -f
                       ($diskFree / 1024.0), $script:Pick.FridayDiskGb, $floorGb, $what) +
                      'You can use a cloud model now and add local models later in Settings -> Models after you free some space.'
        } else {
            $notes += 'No local model fits this computer for both jobs. A cloud model works on any computer, and you can add local models later in Settings -> Models.'
        }
    }
    return [ordered]@{ tier = $pick.tier; pick = $pick; deep = $deep; fast = $fast; unfit = $unfit.ToArray(); notes = $notes; ram_budget_mib = $ramBudget }
}

function Write-ModelOptionsFile {
    <#  The file the wizard reads: one record per line, fields separated by |.
        FACT|name|value   what was detected, for the page's "this computer" line
        TIER|id           the pick
        NOTE|text
        UNFIT|seat|id|label|size_text|reason   an option that does not fit, listed greyed with its reason
        OPT|seat|id|packing|label|bytes|size_text|recommended|older|companions|note|pickjson #>
    param([Parameter(Mandatory)] $Facts, [Parameter(Mandatory)] $Options, [Parameter(Mandatory)][string] $OutFile)
    $clean = { param($s) ("$s" -replace '[\r\n|]', ' ').Trim() }
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($k in $Facts.Keys) { $lines.Add(('FACT|{0}|{1}' -f $k, (& $clean $Facts[$k]))) }
    $lines.Add('TIER|' + $Options.tier)
    foreach ($n in $Options.notes) { $lines.Add('NOTE|' + (& $clean $n)) }
    foreach ($u in @($Options.unfit)) {
        $lines.Add(('UNFIT|{0}|{1}|{2}|{3}|{4}' -f $u.seat, $u.id, (& $clean $u.label), $u.size_text, (& $clean $u.reason)))
    }
    foreach ($o in @($Options.deep) + @($Options.fast)) {
        $pj = ''
        if ($o.serve) { $pj = ($o.serve | ConvertTo-Json -Compress) }
        $lines.Add(('OPT|{0}|{1}|{2}|{3}|{4}|{5}|{6}|{7}|{8}|{9}|{10}' -f $o.seat, $o.id, $o.packing, (& $clean $o.label),
                    $o.bytes, $o.size_text, [int][bool]$o.recommended, [int][bool]$o.older_generation,
                    [int][bool]$o.companions, (& $clean $o.note), (& $clean $pj)))
    }
    [System.IO.File]::WriteAllLines($OutFile, $lines.ToArray(), (New-Object System.Text.UTF8Encoding($false)))
}
