param(
    [string]$OutputName = "leakage_simulator_desktop_v1.0.0_gpu_cuda",
    [string]$SourcePythonDirectory = "",
    [string]$ReleaseDirectory = ""
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$AiInstructionEntrypoint = "AGENTS.md"
$AiGpuRunbook = "docs/ai-gpu-execution-runbook.md"

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
$OutputDirectory = Assert-SafeDirectChildPath $ReleaseRoot (Join-Path $ReleaseRoot $OutputName) $OutputName "Output directory"
$ExpectedZipPath = Assert-SafeDirectChildPath $ReleaseRoot (Join-Path $ReleaseRoot "$OutputName.zip") "$OutputName.zip" "ZIP path"

try {
    $Git = Get-Command "git.exe" -ErrorAction Stop
    $Commit = (& $Git.Source -C $Root rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $Commit -notmatch "^[0-9a-f]{40}$") {
        throw "Unable to identify the Git commit for this build."
    }
    $Branch = (& $Git.Source -C $Root branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Branch) {
        throw "Build from a named Git branch, not detached HEAD."
    }
    $WorktreeChanges = @(& $Git.Source -C $Root status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to verify Git worktree status."
    }
    if ($WorktreeChanges.Count -gt 0) {
        throw "The worktree contains uncommitted files. Commit them before creating a tester artifact."
    }

    $BuildParameters = @{
        OutputName = $OutputName
        ReleaseDirectory = $ReleaseRoot
    }
    if ($SourcePythonDirectory) {
        $BuildParameters.SourcePythonDirectory = $SourcePythonDirectory
    }
    & (Join-Path $Root "build_gpu_cuda_desktop.ps1") @BuildParameters
    if ($LASTEXITCODE -ne 0) {
        throw "GPU CUDA package build failed with exit code $LASTEXITCODE."
    }

    $OutputDirectory = Assert-SafeDirectChildPath $ReleaseRoot $OutputDirectory $OutputName "Built output directory"
    $ZipPath = Assert-SafeDirectChildPath $ReleaseRoot $ExpectedZipPath "$OutputName.zip" "Built ZIP path"
    $ChecksumPath = "$ZipPath.sha256"
    foreach ($Artifact in @($ZipPath, $ChecksumPath)) {
        if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
            throw "Expected build artifact is missing: $Artifact"
        }
    }

    $Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $SidecarTokens = (Get-Content -LiteralPath $ChecksumPath -Raw).Trim() -split "\s+"
    if ($SidecarTokens.Count -lt 2 -or $SidecarTokens[0].ToLowerInvariant() -ne $Hash) {
        throw "The ZIP checksum does not match its .sha256 sidecar."
    }
    if ($SidecarTokens[-1] -ne [System.IO.Path]::GetFileName($ZipPath)) {
        throw "The .sha256 sidecar names a different ZIP file."
    }

    $ZipItem = Get-Item -LiteralPath $ZipPath
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $ArchiveEntries = @(
            $Archive.Entries | ForEach-Object { $_.FullName.Replace("\", "/") }
        )
        foreach ($AiEntry in @($AiInstructionEntrypoint, $AiGpuRunbook)) {
            $ExpectedArchiveEntry = "$OutputName/$AiEntry"
            if ($ArchiveEntries -notcontains $ExpectedArchiveEntry) {
                throw "The GPU ZIP is missing its AI guidance entry: $ExpectedArchiveEntry"
            }
        }
    }
    finally {
        $Archive.Dispose()
    }

    $Manifest = [ordered]@{
        schema_version = 1
        artifact_kind = "tv_leakage_simulator_gpu_cuda_test_zip"
        delivery_path = "gpu_cuda_zip"
        zip_file = $ZipItem.Name
        zip_bytes = $ZipItem.Length
        sha256 = $Hash
        git_branch = $Branch
        git_commit = $Commit
        git_worktree_clean = $true
        source_checkout_entrypoint = "run_web_gpu.bat"
        packaged_tester_entrypoint = "CHECK_GPU_CUDA.bat"
        ai_instruction_entrypoint = $AiInstructionEntrypoint
        ai_gpu_runbook = $AiGpuRunbook
        ai_requires_package_file_access = $true
        source_pull_does_not_update_extracted_zip = $true
        tester_must_report_compute_row = $true
        tester_must_run_real_cuda_preflight = $true
    }
    $ManifestPath = "$ZipPath.handoff.json"
    $Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

    $RoundTrip = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if (
        $RoundTrip.sha256 -ne $Hash -or
        $RoundTrip.git_commit -ne $Commit -or
        $RoundTrip.ai_instruction_entrypoint -ne $AiInstructionEntrypoint -or
        $RoundTrip.ai_gpu_runbook -ne $AiGpuRunbook
    ) {
        throw "The generated handoff manifest failed round-trip verification."
    }

    Write-Host ""
    Write-Host "[GPU RELEASE VERIFIED] $($ZipItem.FullName)"
    Write-Host "[GPU RELEASE VERIFIED] SHA256: $Hash"
    Write-Host "[GPU RELEASE VERIFIED] Git: $Branch @ $Commit"
    Write-Host "[GPU RELEASE VERIFIED] Handoff: $ManifestPath"
    Write-Host "[ACTION] Send ZIP + .sha256 + .handoff.json together; never describe git pull as an EXE update."
}
catch {
    Write-Host ""
    Write-Host "[GPU RELEASE FAILED] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

exit 0
