param(
    [string]$OutputName = "leakage_simulator_desktop_v1.0.0_gpu_cuda",
    [string]$SourcePythonDirectory = "",
    [string]$ReleaseDirectory = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildParameters = @{
    Edition = "gpu_cuda"
    OutputName = $OutputName
}
if ($SourcePythonDirectory) {
    $BuildParameters.SourcePythonDirectory = $SourcePythonDirectory
}
if ($ReleaseDirectory) {
    $BuildParameters.ReleaseDirectory = $ReleaseDirectory
}

& (Join-Path $Root "build_lightweight_desktop.ps1") @BuildParameters
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
