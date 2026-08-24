param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8788,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$VenvDirectory = [System.IO.Path]::GetFullPath((Join-Path $Root ".venv-gpu"))
$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
$RequirementsDev = Join-Path $Root "requirements-dev.txt"
$RequirementsGpu = Join-Path $Root "requirements-gpu-cuda.txt"
$RequirementsMarker = Join-Path $VenvDirectory ".leakage-requirements.sha256"
$BootToken = [System.Guid]::NewGuid().ToString("N")

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    Write-Host "[$Label] $Executable $($Arguments -join ' ')"
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Find-Python313 {
    $PythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        & $PythonLauncher.Source -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32 else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @($PythonLauncher.Source, "-3.13")
        }
    }

    $SystemPython = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($SystemPython) {
        & $SystemPython.Source -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32 else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @($SystemPython.Source)
        }
    }

    throw "Python 3.13 64-bit was not found. [ACTION] Follow docs\WINDOWS_GPU_SETUP.md, enable the py launcher, then run run_web_gpu.bat again."
}

function Get-RequirementsFingerprint {
    $DevHash = (Get-FileHash -LiteralPath $RequirementsDev -Algorithm SHA256).Hash.ToLowerInvariant()
    $GpuHash = (Get-FileHash -LiteralPath $RequirementsGpu -Algorithm SHA256).Hash.ToLowerInvariant()
    return "python=3.13;requirements-dev=$DevHash;requirements-gpu-cuda=$GpuHash"
}

function Remove-GeneratedGpuVenv {
    if ((Split-Path -Parent $VenvDirectory) -ne $Root -or (Split-Path -Leaf $VenvDirectory) -ne ".venv-gpu") {
        throw "Refusing to remove an unexpected virtual-environment path: $VenvDirectory"
    }
    if (Test-Path -LiteralPath $VenvDirectory) {
        Write-Host "[PYTHON] Rebuilding the generated .venv-gpu environment so pulled requirements cannot stay stale."
        Remove-Item -LiteralPath $VenvDirectory -Recurse -Force
    }
}

function Assert-LoopbackPortAvailable([int]$CandidatePort) {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $CandidatePort
    )
    try {
        $listener.Server.ExclusiveAddressUse = $true
        $listener.Start()
    }
    catch {
        throw "Port $CandidatePort is already occupied on 127.0.0.1. [ACTION] Stop the existing server or run run_web_gpu.bat with another port. No browser or GPU server was started."
    }
    finally {
        $listener.Stop()
    }
}

try {
    if (-not $PreflightOnly) {
        Assert-LoopbackPortAvailable $Port
    }

    foreach ($RequiredFile in @($RequirementsDev, $RequirementsGpu)) {
        if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
            throw "Required dependency file is missing: $RequiredFile"
        }
    }

    $Fingerprint = Get-RequirementsFingerprint
    $RebuildVenv = $false
    if (Test-Path -LiteralPath $VenvDirectory) {
        if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
            $RebuildVenv = $true
        }
        else {
            & $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32 else 1)" 2>$null
            if ($LASTEXITCODE -ne 0) {
                $RebuildVenv = $true
            }
        }

        $StoredFingerprint = if (Test-Path -LiteralPath $RequirementsMarker) {
            (Get-Content -LiteralPath $RequirementsMarker -Raw).Trim()
        }
        else {
            ""
        }
        if ($StoredFingerprint -ne $Fingerprint) {
            $RebuildVenv = $true
        }
    }

    if ($RebuildVenv) {
        Remove-GeneratedGpuVenv
    }

    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        $BasePython = @(Find-Python313)
        $BaseExecutable = $BasePython[0]
        $BaseArguments = @()
        if ($BasePython.Count -gt 1) {
            $BaseArguments += $BasePython[1..($BasePython.Count - 1)]
        }
        $BaseArguments += @("-m", "venv", $VenvDirectory)
        Invoke-Checked "PYTHON CREATE" $BaseExecutable @BaseArguments
    }

    # Always run the resolver and exact-pin verifier. An existing environment is
    # never accepted merely because its directory already exists.
    Invoke-Checked "PYTHON PIP" $VenvPython -m pip install --disable-pip-version-check --upgrade pip
    Invoke-Checked "PYTHON SYNC" $VenvPython -m pip install --disable-pip-version-check -r $RequirementsDev -r $RequirementsGpu
    Invoke-Checked "PYTHON CHECK" $VenvPython -m pip check
    Invoke-Checked "PYTHON VERIFY" $VenvPython (Join-Path $Root "scripts\verify_source_requirements.py") --requirements $RequirementsDev --requirements $RequirementsGpu --human
    Set-Content -LiteralPath $RequirementsMarker -Value $Fingerprint -Encoding ascii

    $Npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "Node.js/npm was not found. [ACTION] Follow docs\WINDOWS_GPU_SETUP.md, install the documented Node.js LTS release, then run run_web_gpu.bat again."
    }

    # npm ci removes packages that are no longer in package-lock.json. This is
    # deliberately run after every pull so stale node_modules cannot mask changes.
    Invoke-Checked "FRONTEND SYNC" $Npm.Source --prefix (Join-Path $Root "frontend") ci --no-audit --no-fund
    Invoke-Checked "FRONTEND BUILD" $Npm.Source --prefix (Join-Path $Root "frontend") run build

    Write-Host ""
    Write-Host "[GPU PREFLIGHT] Executing the production FP64 Ray/BVH CUDA path before server startup."
    Invoke-Checked "GPU PREFLIGHT" $VenvPython (Join-Path $Root "scripts\verify_gpu_cuda_runtime.py") --mode device --human

    if ($PreflightOnly) {
        Write-Host ""
        Write-Host "[GPU VERIFIED] Source setup and production Ray/BVH CUDA preflight passed."
        Write-Host "[STATUS] PreflightOnly was requested, so the server was not started."
        exit 0
    }

    # Check again immediately before opening a browser to close the setup-time
    # race. The opener must never attach to an older process on the same port.
    Assert-LoopbackPortAvailable $Port

    $env:LEAKAGE_WEB_PORT = [string]$Port
    $env:LEAKAGE_BOOT_TOKEN = $BootToken
    $env:PATH = "$(Join-Path $VenvDirectory 'Scripts');$env:PATH"
    $Url = "http://127.0.0.1:$Port/"
    $OpenWhenReady = Join-Path $Root "scripts\open_web_when_ready.py"
    $OpenArguments = @(
        "`"$OpenWhenReady`"",
        "`"$Url`"",
        "--expected-boot-token",
        "`"$BootToken`""
    )
    Start-Process -FilePath $VenvPython -ArgumentList $OpenArguments -WindowStyle Hidden

    Write-Host ""
    Write-Host "[GPU VERIFIED] The production Ray/BVH CUDA kernel passed. The server is now starting."
    Write-Host "[GPU VERIFIED] Device details are printed in the preflight block above."
    Write-Host "[READY] Browser: $Url"
    Write-Host "[READY] In Ray Tracing, select 연산 장치 > NVIDIA GPU."
    Write-Host "[READY] Keep this window open and confirm the result's Compute row after every run."
    Write-Host ""

    & $VenvPython (Join-Path $Root "run_web.py") --port $Port --strict-port
    if ($LASTEXITCODE -ne 0) {
        throw "Server stopped with exit code $LASTEXITCODE."
    }
}
catch {
    Write-Host ""
    Write-Host "[GPU SOURCE FAILED] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[ACTION] The GPU server was not started. Fix the reported item and rerun run_web_gpu.bat." -ForegroundColor Yellow
    Write-Host "[GUIDE] Prerequisite installation and AI workflow: docs\WINDOWS_GPU_SETUP.md" -ForegroundColor Yellow
    Write-Host "[ACTION] Use run_web.bat only when intentional CPU fallback is acceptable." -ForegroundColor Yellow
    exit 1
}

exit 0
