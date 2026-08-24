param(
    [ValidateSet("lite", "gpu_cuda")]
    [string]$Edition = "lite",
    [string]$OutputName = "",
    [string]$SourcePythonDirectory = "",
    [string]$ReleaseDirectory = ""
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$IsGpuCudaEdition = $Edition -eq "gpu_cuda"

function Get-NormalizedFullPath([string]$Path) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Length -gt $pathRoot.Length) {
        $fullPath = $fullPath.TrimEnd(
            [char[]]@(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
        )
    }
    return $fullPath
}

function Assert-SafeOutputName([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name -in @(".", "..")) {
        throw "[PATH SAFETY] OutputName must be a non-empty package folder name, not '.' or '..'."
    }
    if ($Name.Trim() -ne $Name -or $Name.EndsWith(".")) {
        throw "[PATH SAFETY] OutputName cannot have leading/trailing whitespace or a trailing dot."
    }
    if (
        [System.IO.Path]::IsPathRooted($Name) -or
        $Name.Contains([System.IO.Path]::DirectorySeparatorChar) -or
        $Name.Contains([System.IO.Path]::AltDirectorySeparatorChar) -or
        $Name.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0
    ) {
        throw "[PATH SAFETY] OutputName must be one safe leaf name without a root or path separator."
    }
    $stem = $Name.Split(".")[0].ToUpperInvariant()
    $reservedNames = @(
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    )
    if ($reservedNames -contains $stem) {
        throw "[PATH SAFETY] OutputName cannot be a reserved Windows device name."
    }
}

function Assert-SafeDirectChildPath(
    [string]$ParentPath,
    [string]$CandidatePath,
    [string]$ExpectedLeaf,
    [string]$Label
) {
    $resolvedParent = Get-NormalizedFullPath $ParentPath
    $resolvedCandidate = Get-NormalizedFullPath $CandidatePath
    $candidateParent = Get-NormalizedFullPath ([System.IO.Path]::GetDirectoryName($resolvedCandidate))
    $candidateLeaf = [System.IO.Path]::GetFileName($resolvedCandidate)
    if (
        $resolvedCandidate -eq $resolvedParent -or
        $candidateParent -ne $resolvedParent -or
        $candidateLeaf -cne $ExpectedLeaf
    ) {
        throw "[PATH SAFETY] $Label must be the exact expected direct child of the release folder."
    }
    return $resolvedCandidate
}

function Assert-SafeDescendantPath(
    [string]$ParentPath,
    [string]$CandidatePath,
    [string]$ExpectedLeaf,
    [string]$Label
) {
    $resolvedParent = Get-NormalizedFullPath $ParentPath
    $resolvedCandidate = Get-NormalizedFullPath $CandidatePath
    $prefix = $resolvedParent + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not $resolvedCandidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        [System.IO.Path]::GetFileName($resolvedCandidate) -cne $ExpectedLeaf
    ) {
        throw "[PATH SAFETY] $Label must be the exact expected descendant of the package folder."
    }
    return $resolvedCandidate
}

function Assert-NotReparsePoint([string]$Path, [string]$Label) {
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "[PATH SAFETY] Refusing recursive deletion through a reparse point: $Label"
        }
    }
}

if (-not $OutputName) {
    $OutputName = if ($IsGpuCudaEdition) {
        "leakage_simulator_desktop_v1.0.0_gpu_cuda"
    }
    else {
        "leakage_simulator_desktop_v1.0.0_lite"
    }
}
$ReleaseRoot = if ($ReleaseDirectory) {
    Get-NormalizedFullPath $ReleaseDirectory
}
else {
    Get-NormalizedFullPath (Join-Path $Root "release")
}
if ($ReleaseRoot -eq [System.IO.Path]::GetPathRoot($ReleaseRoot)) {
    throw "[PATH SAFETY] ReleaseDirectory cannot be a filesystem root."
}
Assert-SafeOutputName $OutputName
$OutputDir = Assert-SafeDirectChildPath $ReleaseRoot (Join-Path $ReleaseRoot $OutputName) $OutputName "Output directory"
$ZipPath = Assert-SafeDirectChildPath $ReleaseRoot (Join-Path $ReleaseRoot "$OutputName.zip") "$OutputName.zip" "ZIP path"
$SourcePython = if ($SourcePythonDirectory) {
    [System.IO.Path]::GetFullPath($SourcePythonDirectory)
}
else {
    Join-Path $Root "_tools\python313"
}
$SourceSitePackages = Join-Path $SourcePython "Lib\site-packages"
$TargetPython = Join-Path $OutputDir "_tools\python313"
$TargetSitePackages = Join-Path $TargetPython "Lib\site-packages"
$LauncherSource = Join-Path $Root "desktop_launcher\LeakageSimulatorDesktop.cs"
$Compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

function Get-DirectorySizeMB([string]$Path) {
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File | Measure-Object Length -Sum).Sum
    return [math]::Round($sum / 1MB, 1)
}

function Copy-RequiredPath([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Required runtime path is missing: $Source"
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Test-PackagedWebServer([string]$PythonExe, [string]$AppRoot) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()

    $process = $null
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $PythonExe
        $startInfo.Arguments = '-u "{0}" --port {1} --strict-port' -f (Join-Path $AppRoot "run_web.py"), $port
        $startInfo.WorkingDirectory = $AppRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $process = [System.Diagnostics.Process]::Start($startInfo)

        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        $healthy = $false
        while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited) {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
                if ($health -match "^ok api_version=") {
                    $rootResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 5
                    if ($rootResponse.Content -match "<title>TV Leakage Simulator</title>") {
                        $healthy = $true
                        break
                    }
                }
            }
            catch {
            }
            Start-Sleep -Milliseconds 300
        }
        if (-not $healthy) {
            if (-not $process.HasExited) {
                $process.Kill()
                $process.WaitForExit()
            }
            $stdout = $process.StandardOutput.ReadToEnd()
            $stderr = $process.StandardError.ReadToEnd()
            throw "Packaged web server health validation failed.`nSTDOUT:`n$stdout`nSTDERR:`n$stderr"
        }
    }
    finally {
        if ($process -and -not $process.HasExited) {
            $process.Kill()
            $process.WaitForExit()
        }
    }
}

if (-not (Test-Path -LiteralPath $SourcePython)) {
    throw "Source embedded Python was not found: $SourcePython"
}
if (-not (Test-Path -LiteralPath $Compiler)) {
    throw "C# compiler was not found: $Compiler"
}

$resolvedRelease = Get-NormalizedFullPath $ReleaseRoot
$OutputDir = Assert-SafeDirectChildPath $resolvedRelease $OutputDir $OutputName "Output directory"

if (Test-Path -LiteralPath $OutputDir) {
    $OutputDir = Assert-SafeDirectChildPath $resolvedRelease $OutputDir $OutputName "Output directory before recursive deletion"
    Assert-NotReparsePoint $OutputDir "output directory"
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
if (Test-Path -LiteralPath $ZipPath) {
    $ZipPath = Assert-SafeDirectChildPath $resolvedRelease $ZipPath "$OutputName.zip" "ZIP before deletion"
    Remove-Item -LiteralPath $ZipPath -Force
}

New-Item -ItemType Directory -Path $TargetSitePackages -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir "outputs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir "_uploads") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir "desktop_runtime") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir "docs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputDir ".github") -Force | Out-Null
if ($IsGpuCudaEdition) {
    New-Item -ItemType Directory -Path (Join-Path $OutputDir "scripts") -Force | Out-Null
}

Write-Host "[1/9] Building React production UI..."
$FrontendDir = Join-Path $Root "frontend"
$FrontendDist = Join-Path $FrontendDir "dist"
& npm --prefix $FrontendDir run build
if ($LASTEXITCODE -ne 0) {
    throw "React production build failed."
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDist "index.html"))) {
    throw "React production index was not generated."
}

Write-Host "[2/9] Copying minimal Python runtime..."
Get-ChildItem -LiteralPath $SourcePython -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $TargetPython -Force
}

Write-Host "[3/9] Copying STEP, FastAPI and ray tracing dependencies..."
$RuntimePackages = @(
    "OCP",
    "cadquery_ocp",
    "cadquery_ocp-7.9.3.1.1.dist-info",
    "cadquery_ocp_proxy",
    "cadquery_ocp_proxy-7.9.3.1.1.dist-info",
    "numpy",
    "numpy-2.4.6.dist-info",
    "numpy.libs",
    "annotated_doc",
    "annotated_doc-0.0.4.dist-info",
    "annotated_types",
    "annotated_types-0.8.0.dist-info",
    "anyio",
    "anyio-4.14.2.dist-info",
    "click",
    "click-8.4.2.dist-info",
    "colorama",
    "colorama-0.4.6.dist-info",
    "fastapi",
    "fastapi-0.140.0.dist-info",
    "h11",
    "h11-0.16.0.dist-info",
    "httpcore2",
    "httpcore2-2.9.1.dist-info",
    "httpx2",
    "httpx2-2.9.1.dist-info",
    "idna",
    "idna-3.18.dist-info",
    "pydantic",
    "pydantic-2.13.4.dist-info",
    "pydantic_core",
    "pydantic_core-2.46.4.dist-info",
    "starlette",
    "starlette-1.3.1.dist-info",
    "truststore",
    "truststore-0.10.4.dist-info",
    "typing_extensions.py",
    "typing_extensions-4.16.0.dist-info",
    "typing_inspection",
    "typing_inspection-0.4.2.dist-info",
    "uvicorn",
    "uvicorn-0.51.0.dist-info"
)
if ($IsGpuCudaEdition) {
    $RuntimePackages += @(
        "llvmlite",
        "llvmlite-0.48.0.dist-info",
        "llvmlite.libs",
        "numba",
        "numba-0.66.0.dist-info"
    )
}
foreach ($package in $RuntimePackages) {
    Copy-RequiredPath (Join-Path $SourceSitePackages $package) $TargetSitePackages
}
$DependencyScript = Join-Path $Root "scripts\copy_pe_dependency_closure.py"
$DependencyManifest = Join-Path $OutputDir "runtime_dependency_manifest.json"
& (Join-Path $SourcePython "python.exe") $DependencyScript `
    --seed (Join-Path $SourceSitePackages "OCP\OCP.cp313-win_amd64.pyd") `
    --source-dir (Join-Path $SourceSitePackages "cadquery_ocp.libs") `
    --source-dir (Join-Path $SourceSitePackages "vtk.libs") `
    --target-root $TargetSitePackages `
    --manifest $DependencyManifest
if ($LASTEXITCODE -ne 0) {
    throw "OCP dependency closure copy failed."
}

Write-Host "[4/9] Copying simulator application files..."
Copy-RequiredPath (Join-Path $Root "src") $OutputDir
Copy-RequiredPath (Join-Path $Root "samples") $OutputDir
Copy-RequiredPath $FrontendDist (Join-Path $OutputDir "frontend\dist")
Copy-Item -LiteralPath (Join-Path $Root "run_web.py") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "run_api.py") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "check_cad_import.py") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "AGENTS.md") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "CLAUDE.md") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "GEMINI.md") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root ".github\copilot-instructions.md") -Destination (Join-Path $OutputDir ".github") -Force
Copy-Item -LiteralPath (Join-Path $Root "COMPANY_PC_QUICK_START.md") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "requirements-dev.txt") -Destination $OutputDir -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\cad-intersection-backend-contract.md") -Destination (Join-Path $OutputDir "docs") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\performance-acceleration-plan.md") -Destination (Join-Path $OutputDir "docs") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\desktop-exe-packaging.md") -Destination (Join-Path $OutputDir "docs") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\gpu-cuda-user-guide.md") -Destination (Join-Path $OutputDir "docs") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\ai-gpu-execution-runbook.md") -Destination (Join-Path $OutputDir "docs") -Force
Copy-Item -LiteralPath (Join-Path $Root "docs\WINDOWS_GPU_SETUP.md") -Destination (Join-Path $OutputDir "docs") -Force
if ($IsGpuCudaEdition) {
    Copy-Item -LiteralPath (Join-Path $Root "requirements-gpu-cuda.txt") -Destination $OutputDir -Force
    Copy-Item -LiteralPath (Join-Path $Root "scripts\verify_gpu_cuda_runtime.py") -Destination (Join-Path $OutputDir "scripts") -Force
    Copy-Item -LiteralPath (Join-Path $Root "CHECK_GPU_CUDA.bat") -Destination $OutputDir -Force
    Copy-Item -LiteralPath (Join-Path $Root "setup_windows_gpu.bat") -Destination $OutputDir -Force
    Copy-Item -LiteralPath (Join-Path $Root "setup_windows_gpu.ps1") -Destination $OutputDir -Force
}

$WebViewCandidates = @(
    (Join-Path $Root "release\leakage_simulator_desktop_v0.1"),
    "C:\Program Files\Microsoft Office\root\Office16\ADDINS\Microsoft Power Query for Excel Integrated\bin"
)
$WebViewSource = $null
foreach ($candidate in $WebViewCandidates) {
    if (
        (Test-Path -LiteralPath (Join-Path $candidate "Microsoft.Web.WebView2.Core.dll")) -and
        (Test-Path -LiteralPath (Join-Path $candidate "Microsoft.Web.WebView2.WinForms.dll"))
    ) {
        $WebViewSource = $candidate
        break
    }
}
if (-not $WebViewSource) {
    throw "WebView2 managed DLLs were not found."
}

Write-Host "[5/9] Building desktop launcher..."
$WebViewCore = Join-Path $WebViewSource "Microsoft.Web.WebView2.Core.dll"
$WebViewWinForms = Join-Path $WebViewSource "Microsoft.Web.WebView2.WinForms.dll"
$WebViewLoaderCandidates = @(
    (Join-Path $WebViewSource "WebView2Loader.dll"),
    "C:\Program Files\Microsoft Office\root\Office16\ADDINS\Microsoft Power Query for Excel Integrated\bin\WebView2Loader.dll"
)
$WebViewLoader = $WebViewLoaderCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $WebViewLoader) {
    throw "WebView2Loader.dll was not found."
}
$LauncherExe = Join-Path $OutputDir "LeakageSimulator.exe"
& $Compiler /nologo /target:winexe /platform:x64 /optimize+ /out:$LauncherExe `
    /r:System.dll `
    /r:System.Core.dll `
    /r:System.Drawing.dll `
    /r:System.Windows.Forms.dll `
    /r:$WebViewCore `
    /r:$WebViewWinForms `
    $LauncherSource
if ($LASTEXITCODE -ne 0) {
    throw "Desktop launcher compilation failed."
}
Copy-Item -LiteralPath $WebViewCore -Destination $OutputDir -Force
Copy-Item -LiteralPath $WebViewWinForms -Destination $OutputDir -Force
Copy-Item -LiteralPath $WebViewLoader -Destination $OutputDir -Force

$EditionLabel = if ($IsGpuCudaEdition) { "GPU CUDA" } else { "Lite" }
$GpuStartNote = if ($IsGpuCudaEdition) {
@"
- NVIDIA GPU mode requires a compatible NVIDIA display driver and local CUDA Toolkit.
- This build was validated for Numba 0.66.0, llvmlite 0.48.0 and CUDA Toolkit 13.1.
- If prerequisites are uncertain, run setup_windows_gpu.bat in its default read-only mode and follow docs/WINDOWS_GPU_SETUP.md. Install mode always requires explicit approval.
- Before selecting GPU mode, double-click CHECK_GPU_CUDA.bat and confirm the GPU name, Ray/BVH kernel PASS and final [OK].
- After ray tracing, confirm the Compute row shows the GPU name and at least one successful GPU batch.
- BVH/Rebuilt describes the acceleration structure build; it does not prove that this run used the GPU.
- If GPU initialization or execution fails, the simulator replays the logical batch on CPU.
- A git pull does not update this extracted EXE. Use a newly built GPU ZIP in a new folder.
"@
}
else {
@"
- This Lite edition intentionally excludes Numba/llvmlite; use CPU compute mode.
"@
}
@"
TV Leakage Simulator Desktop $EditionLabel v1.0.0

1. Double-click LeakageSimulator.exe.
2. Wait until the simulator window opens.
3. Import STEP/STP CAD from Model Import.
4. React UI, ROI, Material, Transform, Emitter, Receiver and ray result visualization are included.

Important:
- Keep all files and folders together.
- When using an AI assistant, open this package root and make it read AGENTS.md, docs/WINDOWS_GPU_SETUP.md and docs/ai-gpu-execution-runbook.md before it runs commands.
- A web AI without access to this folder cannot read those instructions automatically; attach the files or use the prompt in README.md.
- X_T direct import is not implemented in this lite build.
- If embedded WebView2 is unavailable, the launcher opens the local UI in the default browser.
$GpuStartNote
"@ | Set-Content -LiteralPath (Join-Path $OutputDir "START_HERE.txt") -Encoding utf8

Write-Host "[6/9] Validating minimal runtime, FastAPI and STEP import..."
$TargetPythonExe = Join-Path $TargetPython "python.exe"
& $TargetPythonExe -c "import OCP, fastapi, numpy, uvicorn; print('runtime ok', numpy.__version__, fastapi.__version__, uvicorn.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "Minimal Python runtime import validation failed."
}
if ($IsGpuCudaEdition) {
    $GpuSmokeScript = Join-Path $OutputDir "scripts\verify_gpu_cuda_runtime.py"
    $GpuSmokeManifest = Join-Path $OutputDir "gpu_cuda_runtime_manifest.json"
    & $TargetPythonExe $GpuSmokeScript --mode device --output $GpuSmokeManifest
    if ($LASTEXITCODE -ne 0) {
        throw "GPU CUDA dependency/device kernel validation failed."
    }
}
& $TargetPythonExe (Join-Path $OutputDir "check_cad_import.py") `
    --cad (Join-Path $OutputDir "samples\tv_leakage_full_assembled_no_gap.stp") `
    --output-dir (Join-Path $OutputDir "outputs") `
    --no-dialog
if ($LASTEXITCODE -ne 0) {
    throw "STEP import validation failed."
}
& $TargetPythonExe -m unittest discover -s (Join-Path $Root "tests") -p "test_*.py"
if ($LASTEXITCODE -ne 0) {
    throw "Ray tracing regression tests failed with the minimal runtime."
}

Write-Host "[7/9] Cleaning generated cache files..."
Get-ChildItem -LiteralPath $OutputDir -Recurse -Directory -Filter "__pycache__" | ForEach-Object {
    $safeCachePath = Assert-SafeDescendantPath $OutputDir $_.FullName "__pycache__" "Python cache directory"
    Assert-NotReparsePoint $safeCachePath "Python cache directory"
    Remove-Item -LiteralPath $safeCachePath -Recurse -Force
}
Get-ChildItem -LiteralPath (Join-Path $OutputDir "outputs") -File -ErrorAction SilentlyContinue | Remove-Item -Force

Write-Host "[8/9] Creating ZIP package..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $OutputDir,
    $ZipPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true
)
$archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    if ($archive.Entries.Count -lt 10) {
        throw "ZIP validation failed: too few entries."
    }
    $requiredEntries = @(
        "$OutputName/LeakageSimulator.exe",
        "$OutputName/run_web.py",
        "$OutputName/run_api.py",
        "$OutputName/frontend/dist/index.html",
        "$OutputName/_tools/python313/python.exe",
        "$OutputName/_tools/python313/Lib/site-packages/OCP/__init__.py",
        "$OutputName/_tools/python313/Lib/site-packages/fastapi/__init__.py",
        "$OutputName/WebView2Loader.dll",
        "$OutputName/AGENTS.md",
        "$OutputName/CLAUDE.md",
        "$OutputName/GEMINI.md",
        "$OutputName/.github/copilot-instructions.md",
        "$OutputName/docs/gpu-cuda-user-guide.md",
        "$OutputName/docs/ai-gpu-execution-runbook.md",
        "$OutputName/docs/WINDOWS_GPU_SETUP.md"
    )
    if ($IsGpuCudaEdition) {
        $requiredEntries += @(
            "$OutputName/_tools/python313/Lib/site-packages/numba/__init__.py",
            "$OutputName/_tools/python313/Lib/site-packages/llvmlite/__init__.py",
            "$OutputName/_tools/python313/Lib/site-packages/llvmlite/binding/llvmlite.dll",
            "$OutputName/scripts/verify_gpu_cuda_runtime.py",
            "$OutputName/CHECK_GPU_CUDA.bat",
            "$OutputName/setup_windows_gpu.bat",
            "$OutputName/setup_windows_gpu.ps1",
            "$OutputName/gpu_cuda_runtime_manifest.json"
        )
    }
    $entryNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
    foreach ($entry in $requiredEntries) {
        if ($entryNames -notcontains $entry) {
            throw "ZIP validation failed: missing $entry"
        }
    }
}
finally {
    $archive.Dispose()
}

Write-Host "[9/9] Extracting ZIP and validating integrated React server..."
$VerifyToken = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$VerifyLeaf = "_verify_" + $VerifyToken
$VerifyDir = Assert-SafeDirectChildPath $resolvedRelease (Join-Path $resolvedRelease $VerifyLeaf) $VerifyLeaf "Verification directory"
if (Test-Path -LiteralPath $VerifyDir) {
    $VerifyDir = Assert-SafeDirectChildPath $resolvedRelease $VerifyDir $VerifyLeaf "Verification directory before recursive deletion"
    Assert-NotReparsePoint $VerifyDir "verification directory"
    Remove-Item -LiteralPath $VerifyDir -Recurse -Force
}
New-Item -ItemType Directory -Path $VerifyDir -Force | Out-Null
try {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $VerifyDir)
    $ExtractedRoot = Join-Path $VerifyDir $OutputName
    $ExtractedPython = Join-Path $ExtractedRoot "_tools\python313\python.exe"
    & $ExtractedPython -c "import OCP, fastapi, numpy, uvicorn; print('extracted runtime ok', numpy.__version__, fastapi.__version__, uvicorn.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Extracted ZIP runtime validation failed."
    }
    if ($IsGpuCudaEdition) {
        & $ExtractedPython (Join-Path $ExtractedRoot "scripts\verify_gpu_cuda_runtime.py") --mode device
        if ($LASTEXITCODE -ne 0) {
            throw "Extracted ZIP GPU CUDA kernel validation failed."
        }
    }
    & $ExtractedPython (Join-Path $ExtractedRoot "check_cad_import.py") `
        --cad (Join-Path $ExtractedRoot "samples\tv_leakage_full_assembled_no_gap.stp") `
        --output-dir (Join-Path $ExtractedRoot "outputs") `
        --no-dialog
    if ($LASTEXITCODE -ne 0) {
        throw "Extracted ZIP STEP import validation failed."
    }
    Test-PackagedWebServer $ExtractedPython $ExtractedRoot
}
finally {
    if (Test-Path -LiteralPath $VerifyDir) {
        $VerifyDir = Assert-SafeDirectChildPath $resolvedRelease $VerifyDir $VerifyLeaf "Verification directory before final recursive deletion"
        Assert-NotReparsePoint $VerifyDir "verification directory"
        Remove-Item -LiteralPath $VerifyDir -Recurse -Force
    }
}

$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$HashPath = "$ZipPath.sha256"
"$Hash  $([System.IO.Path]::GetFileName($ZipPath))" | Set-Content -LiteralPath $HashPath -Encoding ascii

$FolderMB = Get-DirectorySizeMB $OutputDir
$ZipMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 1)
Write-Host "$Edition desktop package completed."
Write-Host "Folder: $OutputDir ($FolderMB MB)"
Write-Host "ZIP:    $ZipPath ($ZipMB MB)"
Write-Host "SHA256: $HashPath"
