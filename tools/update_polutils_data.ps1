param(
    [string]$FfxiRoot = 'C:\Program Files (x86)\Steam\steamapps\common\FFXINA\SquareEnix\FINAL FANTASY XI',
    [string]$Configuration = 'Release'
)

$ErrorActionPreference = 'Stop'
$toolRoot = (Resolve-Path (Join-Path $PSScriptRoot 'polutils_gear_extract')).Path
$simRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$repoRoot = (Resolve-Path (Join-Path $simRoot '..\..')).Path
$buildRoot = Join-Path $simRoot '.cache\polutils-build'
$rawPath = Join-Path $simRoot '.cache\polutils-raw.json'
$catalogPath = Join-Path $simRoot 'data\polutils_models.json'

$msbuild = Get-Command msbuild.exe -ErrorAction SilentlyContinue
if ($null -eq $msbuild) {
    $candidate = 'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\MSBuild.exe'
    if (Test-Path $candidate) { $msbuild = Get-Item $candidate }
}
if ($null -eq $msbuild) { throw 'MSBuild.exe was not found. Install Visual Studio Build Tools with .NET Framework build tools.' }
$msbuildPath = if ($msbuild -is [System.IO.FileInfo]) { $msbuild.FullName } else { $msbuild.Source }
if (-not (Test-Path $FfxiRoot)) { throw "FFXI root does not exist: $FfxiRoot" }

& $msbuildPath (Join-Path $toolRoot 'PolUtilsGearExtractor.csproj') /t:Build /p:Configuration=$Configuration /p:OutputPath="$buildRoot\" /p:BaseOutputPath="$buildRoot\" /p:TargetFrameworkVersion=v4.0 /p:FrameworkPathOverride='C:\Windows\Microsoft.NET\Framework\v4.0.30319' /nologo /v:minimal
if ($LASTEXITCODE -ne 0) { throw "POLUtils extractor build failed: $LASTEXITCODE" }

$exe = Join-Path $buildRoot 'PolUtilsGearExtractor.exe'
if (-not (Test-Path $exe)) { throw "Extractor executable was not produced: $exe" }
New-Item -ItemType Directory -Force (Split-Path $rawPath) | Out-Null
& $exe '--ffxi-root' $FfxiRoot '--output' $rawPath
if ($LASTEXITCODE -ne 0) { throw "POLUtils extraction failed: $LASTEXITCODE" }

python (Join-Path $PSScriptRoot 'build_polutils_catalog.py') --input $rawPath --output $catalogPath
if ($LASTEXITCODE -ne 0) { throw "POLUtils catalog conversion failed: $LASTEXITCODE" }
Write-Output "Updated $catalogPath"
