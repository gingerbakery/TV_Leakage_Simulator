param(
    [string]$OptixRoot = 'C:\ProgramData\NVIDIA Corporation\OptiX SDK 9.1.0',
    [string]$CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1',
    [string]$Architecture = 'compute_86',
    [string]$OutputDirectory = 'outputs/perf5e-native',
    [switch]$EnableTestFaults
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'tools/perf5e_native'
$output = [IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$vcvars = Join-Path $visualStudio 'VC/Auxiliary/Build/vcvars64.bat'
$nvcc = Join-Path $CudaRoot 'bin/nvcc.exe'
foreach ($required in @($vcvars, $nvcc, (Join-Path $OptixRoot 'include/optix.h'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing prerequisite: $required. No installation was attempted." }
}
if ($Architecture -notmatch '^compute_[0-9]+$') { throw 'Invalid CUDA architecture' }
New-Item -ItemType Directory -Path $output -Force | Out-Null
$compileDevice = '"{0}" --ptx --std=c++17 -arch={1} --fmad=false --prec-div=true --ftz=false -I"{2}\include" -I"{3}" "{3}\optix_probe.cu" -o "{4}\optix_probe.ptx"' -f $nvcc, $Architecture, $OptixRoot, $source, $output
$compileHost = 'cl.exe /nologo /std:c++17 /utf-8 /O2 /EHsc /MT /LD /fp:strict /DNOMINMAX /I"{0}\include" /I"{1}\include" /I"{2}" "{2}\optix_probe.cpp" /Fo"{3}\optix_probe.obj" /link /OUT:"{3}\optix_probe.dll" /IMPLIB:"{3}\optix_probe.lib" /LIBPATH:"{1}\lib\x64" cuda.lib advapi32.lib' -f $OptixRoot, $CudaRoot, $source, $output
if ($EnableTestFaults) { $compileHost = $compileHost.Replace(' /DNOMINMAX ', ' /DNOMINMAX /DPERF5E_TEST_FAULTS ') }
& $env:ComSpec /d /c "call `"$vcvars`" >nul && $compileDevice && $compileHost"
if ($LASTEXITCODE -ne 0) { throw "Native probe build failed: $LASTEXITCODE" }
$artifacts = @('optix_probe.dll', 'optix_probe.ptx') | ForEach-Object {
    $path = Join-Path $output $_
    @{ path = $path; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
}
$manifest = @{
    experimental = $true
    production_enabled = $false
    test_faults_enabled = [bool]$EnableTestFaults
    optix_root = $OptixRoot
    cuda_root = $CudaRoot
    architecture = $Architecture
    compiler_installation = $visualStudio
    device_command = $compileDevice
    host_command = $compileHost
    sources = @(Get-ChildItem -LiteralPath $source -File | ForEach-Object { @{ path = $_.FullName; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } })
    artifacts = $artifacts
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'build-manifest.json') -Encoding UTF8
Write-Output "Experimental OptiX probe built: $output"
