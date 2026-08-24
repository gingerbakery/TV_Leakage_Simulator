param(
    [switch]$Install,
    [switch]$RuntimeOnly,
    [string]$ApprovedDriverInstallerPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = [System.IO.Path]::GetFullPath(
    (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$MinimumDriverVersion = [version]"580.0"
$RequiredCudaWingetVersion = "13.1"
$RequiredCudaDisplayVersion = "13.1.1"
$RequiredPythonVersion = "3.13.15"
$RequiredNodeVersion = "v24.19.0"
$MinimumAcceptedNodeVersion = [version]"24.11.0"

function Get-DeliveryPath {
    if ($RuntimeOnly) {
        return [pscustomobject]@{
            Kind = "runtime_only"
            Label = "GPU runtime only (explicit -RuntimeOnly)"
            RequiresSourceTools = $false
        }
    }

    $SourceMarkersPresent = (
        (Test-Path -LiteralPath (Join-Path $Root ".git")) -and
        (Test-Path -LiteralPath (Join-Path $Root "requirements-dev.txt") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Root "requirements-gpu-cuda.txt") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Root "frontend") -PathType Container) -and
        (Test-Path -LiteralPath (Join-Path $Root "run_web_gpu.bat") -PathType Leaf)
    )
    if ($SourceMarkersPresent) {
        return [pscustomobject]@{
            Kind = "source"
            Label = "Git source checkout"
            RequiresSourceTools = $true
        }
    }

    $GpuZipMarkersPresent = (
        (Test-Path -LiteralPath (Join-Path $Root "LeakageSimulator.exe") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Root "_tools") -PathType Container) -and
        (Test-Path -LiteralPath (Join-Path $Root "CHECK_GPU_CUDA.bat") -PathType Leaf)
    )
    if ($GpuZipMarkersPresent) {
        return [pscustomobject]@{
            Kind = "gpu_zip"
            Label = "Extracted GPU CUDA ZIP"
            RequiresSourceTools = $false
        }
    }

    throw "Delivery path could not be identified from this folder. [ACTION] Run this helper from the Git source root or extracted GPU ZIP root. For an IT-managed runtime-only deployment, rerun setup_windows_gpu.ps1 with -RuntimeOnly."
}

function Test-IsAdministrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    return $Principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Get-NvidiaStatus {
    $ControllerNames = @()
    try {
        $ControllerNames = @(
            Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop |
                Where-Object { $_.Name -match "NVIDIA" } |
                ForEach-Object { [string]$_.Name }
        )
    }
    catch {
        # nvidia-smi remains authoritative. Company policy can deny CIM access.
        $ControllerNames = @()
    }

    $SmiPath = $null
    $SmiCommand = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($SmiCommand) {
        $SmiPath = $SmiCommand.Source
    }
    else {
        $DefaultSmi = Join-Path $env:ProgramFiles "NVIDIA Corporation\NVSMI\nvidia-smi.exe"
        if (Test-Path -LiteralPath $DefaultSmi -PathType Leaf) {
            $SmiPath = $DefaultSmi
        }
    }

    $SmiNames = @()
    $ComputeCapabilities = @()
    $DriverVersionText = $null
    if ($SmiPath) {
        try {
            $SmiOutput = @(
                & $SmiPath --query-gpu=name,driver_version --format=csv,noheader 2>$null
            )
            $SmiExitCode = $LASTEXITCODE
            if ($SmiExitCode -eq 0) {
                foreach ($Line in $SmiOutput) {
                    $Parts = ([string]$Line) -split ",", 2
                    if ($Parts.Count -eq 2) {
                        $SmiNames += $Parts[0].Trim()
                        if (-not $DriverVersionText) {
                            $DriverVersionText = $Parts[1].Trim()
                        }
                    }
                }
            }
            $ComputeOutput = @(
                & $SmiPath --query-gpu=compute_cap --format=csv,noheader 2>$null
            )
            if ($LASTEXITCODE -eq 0) {
                $ComputeCapabilities = @(
                    $ComputeOutput |
                        ForEach-Object { ([string]$_).Trim() } |
                        Where-Object { $_ }
                )
            }
        }
        catch {
            $SmiNames = @()
            $ComputeCapabilities = @()
            $DriverVersionText = $null
        }
    }

    $GpuNames = @(
        if ($SmiNames.Count -gt 0) {
            $SmiNames
        }
        else {
            $ControllerNames
        }
    )

    $ParsedDriverVersion = $null
    if ($DriverVersionText) {
        try {
            $ParsedDriverVersion = [version]$DriverVersionText
        }
        catch {
            $ParsedDriverVersion = $null
        }
    }

    [pscustomobject]@{
        GpuDetected = ($GpuNames.Count -gt 0)
        GpuNames = @($GpuNames)
        ComputeCapabilities = @($ComputeCapabilities)
        SmiPath = $SmiPath
        DriverVersion = $ParsedDriverVersion
        DriverVersionText = $DriverVersionText
        DriverCompatible = (
            $null -ne $ParsedDriverVersion -and
            $ParsedDriverVersion -ge $MinimumDriverVersion
        )
    }
}

function Test-DirectoryFilePattern {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [Parameter(Mandatory = $true)]
        [string]$Filter
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return $false
    }
    return $null -ne (
        Get-ChildItem -LiteralPath $Directory -Filter $Filter -File `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1
    )
}

function Get-CudaToolkitStatus {
    $CandidateRoots = @(
        [Environment]::GetEnvironmentVariable("CUDA_PATH_V13_1", "Machine"),
        [Environment]::GetEnvironmentVariable("CUDA_PATH_V13_1", "User"),
        [Environment]::GetEnvironmentVariable("CUDA_PATH", "Machine"),
        [Environment]::GetEnvironmentVariable("CUDA_PATH", "User"),
        $env:CUDA_PATH,
        (Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA\v13.1")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    $NvccPaths = @()
    foreach ($CandidateRoot in ($CandidateRoots | Select-Object -Unique)) {
        $CandidateNvcc = Join-Path $CandidateRoot "bin\nvcc.exe"
        if (Test-Path -LiteralPath $CandidateNvcc -PathType Leaf) {
            $NvccPaths += $CandidateNvcc
        }
    }

    $PathNvcc = Get-Command "nvcc.exe" -ErrorAction SilentlyContinue
    if ($PathNvcc) {
        $NvccPaths += $PathNvcc.Source
    }

    $ObservedVersion = $null
    $ObservedNvcc = $null
    $ObservedRoot = $null
    $RuntimePresent = $false
    $NvvmPresent = $false
    $LibdevicePresent = $false
    foreach ($NvccPath in ($NvccPaths | Select-Object -Unique)) {
        try {
            $NvccOutput = (& $NvccPath --version 2>&1 | Out-String).Trim()
            $NvccExitCode = $LASTEXITCODE
            if ($NvccExitCode -ne 0) {
                continue
            }

            $ReleaseMatch = [regex]::Match(
                $NvccOutput,
                "release\s+([0-9]+\.[0-9]+)"
            )
            if ($ReleaseMatch.Success) {
                $ObservedVersion = $ReleaseMatch.Groups[1].Value
                $ObservedNvcc = $NvccPath
                $ObservedRoot = Split-Path -Parent (Split-Path -Parent $NvccPath)
                $RuntimePresent = Test-DirectoryFilePattern `
                    (Join-Path $ObservedRoot "bin\x64") "cudart64_*.dll"
                $NvvmPresent = Test-DirectoryFilePattern `
                    (Join-Path $ObservedRoot "nvvm\bin\x64") "nvvm*.dll"
                $LibdevicePresent = Test-DirectoryFilePattern `
                    (Join-Path $ObservedRoot "nvvm\libdevice") "libdevice*.bc"
            }
            if (
                $ObservedVersion -eq $RequiredCudaWingetVersion -and
                $RuntimePresent -and
                $NvvmPresent -and
                $LibdevicePresent
            ) {
                break
            }
        }
        catch {
            continue
        }
    }

    [pscustomobject]@{
        Ready = (
            $ObservedVersion -eq $RequiredCudaWingetVersion -and
            $RuntimePresent -and
            $NvvmPresent -and
            $LibdevicePresent
        )
        Version = $ObservedVersion
        NvccPath = $ObservedNvcc
        Root = $ObservedRoot
        RuntimePresent = $RuntimePresent
        NvvmPresent = $NvvmPresent
        LibdevicePresent = $LibdevicePresent
    }
}

function Get-PythonStatus {
    $Candidates = @()
    $PythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        $Candidates += [pscustomobject]@{
            Path = $PythonLauncher.Source
            Prefix = @("-3.13")
        }
    }

    $SystemPython = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($SystemPython) {
        $Candidates += [pscustomobject]@{
            Path = $SystemPython.Source
            Prefix = @()
        }
    }

    foreach ($Candidate in $Candidates) {
        try {
            $PythonArguments = @($Candidate.Prefix) + @(
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{sys.maxsize > 2**32}')"
            )
            $PythonOutput = (
                & $Candidate.Path @PythonArguments 2>$null |
                    Select-Object -Last 1
            )
            $PythonExitCode = $LASTEXITCODE
            if ($PythonExitCode -ne 0 -or -not $PythonOutput) {
                continue
            }

            $Parts = ([string]$PythonOutput).Trim() -split "\|", 2
            if ($Parts.Count -ne 2) {
                continue
            }
            $VersionText = $Parts[0]
            $Is64Bit = ($Parts[1] -eq "True")
            $ParsedVersion = $null
            try {
                $ParsedVersion = [version]$VersionText
            }
            catch {
                continue
            }
            if (
                $ParsedVersion.Major -eq 3 -and
                $ParsedVersion.Minor -eq 13 -and
                $Is64Bit
            ) {
                return [pscustomobject]@{
                    Ready = $true
                    Version = $VersionText
                    Is64Bit = $true
                    Executable = $Candidate.Path
                }
            }
        }
        catch {
            continue
        }
    }

    [pscustomobject]@{
        Ready = $false
        Version = $null
        Is64Bit = $false
        Executable = $null
    }
}

function Get-NodeStatus {
    $NodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
    $NpmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    $NodeVersion = $null
    $NodeArchitecture = $null
    $NpmVersion = $null
    if ($NodeCommand) {
        try {
            $NodeVersion = (& $NodeCommand.Source --version 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                $NodeVersion = $null
            }
            else {
                $NodeArchitecture = (
                    & $NodeCommand.Source -p "process.arch" 2>$null |
                        Out-String
                ).Trim()
                if ($LASTEXITCODE -ne 0) {
                    $NodeArchitecture = $null
                }
            }
        }
        catch {
            $NodeVersion = $null
            $NodeArchitecture = $null
        }
    }

    if ($NpmCommand) {
        try {
            $NpmVersion = (& $NpmCommand.Source --version 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or -not $NpmVersion) {
                $NpmVersion = $null
            }
        }
        catch {
            $NpmVersion = $null
        }
    }

    $ParsedNodeVersion = $null
    if ($NodeVersion) {
        try {
            $ParsedNodeVersion = [version]$NodeVersion.TrimStart("v")
        }
        catch {
            $ParsedNodeVersion = $null
        }
    }
    [pscustomobject]@{
        Ready = (
            $null -ne $ParsedNodeVersion -and
            $ParsedNodeVersion.Major -eq 24 -and
            $ParsedNodeVersion -ge $MinimumAcceptedNodeVersion -and
            $NodeArchitecture -eq "x64" -and
            $null -ne $NpmVersion
        )
        Version = $NodeVersion
        Architecture = $NodeArchitecture
        NodePath = if ($NodeCommand) { $NodeCommand.Source } else { $null }
        NpmPath = if ($NpmCommand) { $NpmCommand.Source } else { $null }
        NpmVersion = $NpmVersion
    }
}

function New-SkippedSourceToolStatus {
    [pscustomobject]@{
        Ready = $true
        Skipped = $true
        Version = $null
    }
}

function Get-SetupStatus {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$RequiresSourceTools
    )

    $Windows64Bit = (
        $env:OS -eq "Windows_NT" -and
        [Environment]::Is64BitOperatingSystem
    )
    $Nvidia = Get-NvidiaStatus
    $Cuda = Get-CudaToolkitStatus
    $Python = if ($RequiresSourceTools) {
        Get-PythonStatus
    }
    else {
        New-SkippedSourceToolStatus
    }
    $Node = if ($RequiresSourceTools) {
        Get-NodeStatus
    }
    else {
        New-SkippedSourceToolStatus
    }
    $SourceToolsReady = (
        -not $RequiresSourceTools -or
        ($Python.Ready -and $Node.Ready)
    )

    [pscustomobject]@{
        Windows64Bit = $Windows64Bit
        Nvidia = $Nvidia
        Cuda = $Cuda
        Python = $Python
        Node = $Node
        RequiresSourceTools = $RequiresSourceTools
        SourceToolsReady = $SourceToolsReady
        AllReady = (
            $Windows64Bit -and
            $Nvidia.GpuDetected -and
            $Nvidia.DriverCompatible -and
            $Cuda.Ready -and
            $SourceToolsReady
        )
    }
}

function Write-SetupStatus {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Status
    )

    $OsText = if ($Status.Windows64Bit) { "PASS" } else { "FAIL" }
    Write-Host "[CHECK] Windows x64: $OsText"

    if ($Status.Nvidia.GpuDetected) {
        Write-Host "[CHECK] NVIDIA GPU: $($Status.Nvidia.GpuNames -join ', ')"
        if ($Status.Nvidia.ComputeCapabilities.Count -gt 0) {
            Write-Host "[CHECK] Compute capability: $($Status.Nvidia.ComputeCapabilities -join ', ')"
        }
    }
    else {
        Write-Host "[CHECK] NVIDIA GPU: NOT FOUND" -ForegroundColor Red
    }

    if ($Status.Nvidia.DriverCompatible) {
        Write-Host "[CHECK] NVIDIA driver: $($Status.Nvidia.DriverVersionText) (PASS, minimum 580)"
    }
    else {
        $DriverText = if ($Status.Nvidia.DriverVersionText) {
            $Status.Nvidia.DriverVersionText
        }
        else {
            "NOT AVAILABLE"
        }
        Write-Host "[CHECK] NVIDIA driver: $DriverText (FAIL, minimum 580)" -ForegroundColor Red
    }

    if ($Status.Cuda.Ready) {
        Write-Host "[CHECK] CUDA Toolkit: $RequiredCudaDisplayVersion / nvcc release $($Status.Cuda.Version) (PASS)"
    }
    else {
        $CudaText = if ($Status.Cuda.Version) { $Status.Cuda.Version } else { "NOT FOUND" }
        Write-Host "[CHECK] CUDA Toolkit: $CudaText (FAIL, required 13.1.1)" -ForegroundColor Red
        if ($Status.Cuda.Version -eq $RequiredCudaWingetVersion) {
            Write-Host "[CHECK] CUDA layout: runtime=$($Status.Cuda.RuntimePresent), NVVM=$($Status.Cuda.NvvmPresent), libdevice=$($Status.Cuda.LibdevicePresent)" -ForegroundColor Red
        }
    }

    if ($Status.RequiresSourceTools) {
        if ($Status.Python.Ready) {
            Write-Host "[CHECK] Python: $($Status.Python.Version) x64 (PASS)"
        }
        else {
            Write-Host "[CHECK] Python: FAIL (required Python 3.13.x x64; installer pins 3.13.15)" -ForegroundColor Red
        }

        if ($Status.Node.Ready) {
            Write-Host "[CHECK] Node.js: $($Status.Node.Version) $($Status.Node.Architecture), npm $($Status.Node.NpmVersion) (PASS)"
        }
        else {
            Write-Host "[CHECK] Node.js: FAIL (required x64 LTS v24.11.0 or newer v24.x with npm; installer pins v24.19.0)" -ForegroundColor Red
        }
    }
    else {
        Write-Host "[CHECK] Python and Node.js: SKIPPED (bundled GPU runtime; not an install target)"
    }
}

function Write-NextStep {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Delivery
    )

    switch ($Delivery.Kind) {
        "source" {
            Write-Host "[NEXT] Run run_web_gpu.bat. Only its production Ray/BVH CUDA preflight can verify GPU readiness."
        }
        "gpu_zip" {
            Write-Host "[NEXT] Run CHECK_GPU_CUDA.bat, require its final [OK], then start LeakageSimulator.exe."
        }
        default {
            Write-Host "[NEXT] Runtime-only prerequisites passed. Run the production GPU checker supplied by the application owner; this helper does not verify GPU execution."
        }
    }
}

function Assert-InstallMode {
    if (-not $Install) {
        throw "[INTERNAL SAFETY] A mutating install function was reached without explicit -Install mode. No installation was attempted."
    }
}

function Assert-Administrator {
    if (-not (Test-IsAdministrator)) {
        throw "Installation requires an elevated PowerShell terminal. [ACTION] Ask your company administrator to approve this setup, open PowerShell as Administrator, and rerun setup_windows_gpu.bat -Install. UAC is not bypassed."
    }
}

function Test-IsFullyQualifiedWindowsPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    if ($Path -match "^[A-Za-z]:[\\/]") {
        return $true
    }
    if ($Path -match "^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)") {
        return $true
    }
    return $false
}

function Resolve-ApprovedDriverInstaller {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-IsFullyQualifiedWindowsPath $Path)) {
        throw "The IT-approved NVIDIA driver installer path must be absolute: $Path"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "The IT-approved NVIDIA driver installer was not found: $Path"
    }

    $ResolvedPath = (Resolve-Path -LiteralPath $Path).ProviderPath
    if ([System.IO.Path]::GetExtension($ResolvedPath) -ne ".exe") {
        throw "The IT-approved NVIDIA driver installer must be an .exe file: $ResolvedPath"
    }

    $Signature = Get-AuthenticodeSignature -LiteralPath $ResolvedPath
    if (
        $Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $Signature.SignerCertificate -or
        $Signature.SignerCertificate.Subject -notmatch "NVIDIA"
    ) {
        throw "The supplied driver installer is not validly signed by NVIDIA. [ACTION] Obtain an RTX Enterprise Production Branch or Studio driver for this GPU from company IT."
    }

    return $ResolvedPath
}

function Invoke-ApprovedDriverInstaller {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Assert-InstallMode
    $ResolvedInstaller = Resolve-ApprovedDriverInstaller $Path
    Write-Host "[INSTALL] Running the IT-approved NVIDIA driver installer without automatic reboot."
    & $ResolvedInstaller -s -n Display.Driver
    $DriverExitCode = $LASTEXITCODE
    if ($DriverExitCode -eq 0) {
        return [pscustomobject]@{ RebootRequired = $false }
    }
    if ($DriverExitCode -eq 1) {
        return [pscustomobject]@{ RebootRequired = $true }
    }

    throw "The NVIDIA driver installer failed with exit code $DriverExitCode. No package setup will continue."
}

function Get-WingetCommand {
    $WingetCommand = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $WingetCommand) {
        throw "Windows Package Manager (winget) is unavailable or blocked. [ACTION] Ask company IT to deploy the pinned prerequisites from docs\WINDOWS_GPU_SETUP.md."
    }
    return $WingetCommand.Source
}

function Invoke-WingetPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Id,
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [ValidateSet("user", "machine")]
        [string]$Scope
    )

    Assert-InstallMode
    $Winget = Get-WingetCommand
    $WingetShowArguments = @(
        "show",
        "--id", $Id,
        "--exact",
        "--source", "winget",
        "--version", $Version,
        "--architecture", "x64",
        "--scope", $Scope,
        "--accept-source-agreements",
        "--disable-interactivity"
    )
    Write-Host "[VERIFY PACKAGE] winget source package $Id version $Version"
    & $Winget @WingetShowArguments
    $WingetShowExitCode = $LASTEXITCODE
    if ($WingetShowExitCode -ne 0) {
        throw "The exact winget package $Id version $Version was not verified in the winget source. Installation was not attempted."
    }

    $WingetArguments = @(
        "install",
        "--id", $Id,
        "--exact",
        "--source", "winget",
        "--version", $Version,
        "--architecture", "x64",
        "--scope", $Scope,
        "--silent",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity"
    )
    Write-Host "[INSTALL] winget package $Id version $Version ($Scope, x64)"
    & $Winget @WingetArguments
    $WingetExitCode = $LASTEXITCODE
    if ($WingetExitCode -ne 0) {
        throw "winget failed for $Id version $Version with exit code $WingetExitCode. No GPU-ready claim will be made."
    }
}

function Refresh-ProcessEnvironment {
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($MachinePath, $UserPath, $env:Path) -join ";"

    $CudaPath = [Environment]::GetEnvironmentVariable("CUDA_PATH_V13_1", "Machine")
    if (-not $CudaPath) {
        $CudaPath = [Environment]::GetEnvironmentVariable("CUDA_PATH", "Machine")
    }
    if ($CudaPath) {
        $env:CUDA_PATH = $CudaPath
    }
}

try {
    $Delivery = Get-DeliveryPath
    Write-Host "[GPU SETUP] Safe Windows prerequisite check for NVIDIA CUDA acceleration."
    Write-Host "[GUIDE] docs\WINDOWS_GPU_SETUP.md"
    Write-Host "[DELIVERY] $($Delivery.Label)"
    if (-not $Install) {
        Write-Host "[MODE] CHECK ONLY. No package, driver, system setting, or reboot will be changed."
    }
    else {
        Write-Host "[MODE] INSTALL. Only prerequisites for the detected delivery path may be installed."
    }
    Write-Host ""

    $Status = Get-SetupStatus -RequiresSourceTools $Delivery.RequiresSourceTools
    Write-SetupStatus $Status

    if ($Status.AllReady) {
        Write-Host ""
        Write-Host "[GPU PREREQUISITES READY] External prerequisites are present; GPU execution is not verified yet."
        Write-NextStep $Delivery
        exit 0
    }

    if (-not $Install) {
        Write-Host ""
        Write-Host "[GPU SETUP NOT READY] One or more required prerequisites failed." -ForegroundColor Red
        if (-not $Status.Nvidia.DriverCompatible) {
            Write-Host "[ACTION] Ask company IT for an NVIDIA RTX Enterprise Production Branch or Studio driver version 580 or newer."
            Write-Host '[ACTION] After approval, rerun: setup_windows_gpu.bat -Install "C:\IT-approved\NVIDIA\setup.exe"'
        }
        Write-Host "[ACTION] To install only pinned prerequisites for this delivery path, rerun setup_windows_gpu.bat -Install from an elevated terminal."
        Write-Host "[SAFETY] No CPU fallback is treated as GPU success."
        exit 1
    }

    if (-not $Status.Windows64Bit) {
        throw "64-bit Windows is required. Installation was not attempted."
    }
    if (-not $Status.Nvidia.GpuDetected) {
        throw "No NVIDIA GPU was detected. Installation was not attempted and CPU fallback is not GPU success."
    }

    Assert-Administrator

    if (-not $Status.Nvidia.DriverCompatible) {
        if ([string]::IsNullOrWhiteSpace($ApprovedDriverInstallerPath)) {
            throw "NVIDIA driver 580 or newer is required. This script never downloads a driver. [ACTION] Obtain an IT-approved RTX Enterprise Production Branch or Studio installer, then pass its absolute path with -ApprovedDriverInstallerPath."
        }

        $DriverResult = Invoke-ApprovedDriverInstaller $ApprovedDriverInstallerPath
        if ($DriverResult.RebootRequired) {
            Write-Host ""
            Write-Host "[REBOOT REQUIRED] The signed NVIDIA driver installed successfully but Windows must be restarted." -ForegroundColor Yellow
            Write-Host "[ACTION] Save work and restart manually. This script never reboots automatically. Rerun the check after sign-in."
            exit 2
        }

        Refresh-ProcessEnvironment
        $Status = Get-SetupStatus -RequiresSourceTools $Delivery.RequiresSourceTools
        if (-not $Status.Nvidia.DriverCompatible) {
            throw "The driver installer returned success, but nvidia-smi did not verify driver 580 or newer. [ACTION] Restart manually if IT requires it, then rerun the check."
        }
    }
    elseif (-not [string]::IsNullOrWhiteSpace($ApprovedDriverInstallerPath)) {
        Write-Host "[SKIP] Existing NVIDIA driver $($Status.Nvidia.DriverVersionText) already meets the minimum; the supplied driver installer was not run."
    }

    if (-not $Status.Cuda.Ready) {
        Invoke-WingetPackage -Id "Nvidia.CUDA" -Version $RequiredCudaWingetVersion -Scope "machine"
    }
    if ($Delivery.RequiresSourceTools -and -not $Status.Python.Ready) {
        Invoke-WingetPackage -Id "Python.Python.3.13" -Version $RequiredPythonVersion -Scope "machine"
    }
    if ($Delivery.RequiresSourceTools -and -not $Status.Node.Ready) {
        Invoke-WingetPackage -Id "OpenJS.NodeJS.LTS" -Version ($RequiredNodeVersion.TrimStart("v")) -Scope "machine"
    }

    Refresh-ProcessEnvironment
    $FinalStatus = Get-SetupStatus -RequiresSourceTools $Delivery.RequiresSourceTools
    Write-Host ""
    Write-Host "[VERIFY] Rechecking every prerequisite after installation."
    Write-SetupStatus $FinalStatus
    if (-not $FinalStatus.AllReady) {
        throw "Pinned installation completed, but the full prerequisite contract is still not satisfied. Restart manually if instructed by company IT, then rerun the check. GPU readiness was not verified."
    }

    Write-Host ""
    Write-Host "[GPU PREREQUISITES READY] External prerequisites are present; GPU execution is not verified yet."
    Write-NextStep $Delivery
    exit 0
}
catch {
    Write-Host ""
    Write-Host "[GPU SETUP FAILED] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[GUIDE] Follow docs\WINDOWS_GPU_SETUP.md and preserve the original error." -ForegroundColor Yellow
    Write-Host "[SAFETY] No automatic reboot, arbitrary driver download, or CPU-success substitution was performed." -ForegroundColor Yellow
    exit 1
}
