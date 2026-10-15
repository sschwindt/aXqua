# Windows entry point. All builds and GUI applications run inside WSL2/WSLg.
[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$Prefix = '',
    [ValidateRange(0, 256)][int]$Jobs = 0,
    [switch]$InstallSystemPackages,
    [string]$ReuseOpenfoam = '',
    [string]$SourceCache = '',
    [ValidateSet('', 'debian12', 'ubuntu22', 'ubuntu24')][string]$VisitPlatform = '',
    [switch]$SkipVisualization,
    [switch]$Examples,
    [switch]$SmokeTest,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

if ($DryRun) {
    Write-Output "No actions executed. WSL2 distribution: $Distro"
    Write-Output 'Compile OpenCFD v2406 + both Olsen solvers + sediDriftFoam2Rating + BAW.'
    Write-Output 'Install signed distro ParaView and pinned OS-matched VisIt 3.5.0 (unless skipped).'
    Write-Output 'Use the WSL Linux filesystem; do not compile under /mnt/c.'
    if ($SourceCache) { Write-Output "Read-only source archive cache (Linux path): $SourceCache" }
    exit 0
}
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL2 is required. In administrator PowerShell: wsl --install -d Ubuntu-24.04; then restart and create a Linux user.'
}

# WSL2 is checked inside the selected distribution, not inferred from wsl.exe existing.
& wsl.exe --distribution $Distro --exec /bin/sh -c 'uname -r | grep -qi "microsoft.*WSL2"'
if ($LASTEXITCODE -ne 0) {
    throw "Initialize WSL2 first: wsl --install -d $Distro; restart/create a Linux user. If needed: wsl --set-version $Distro 2"
}
& wsl.exe --distribution $Distro --exec /bin/sh -c 'test "$(id -u)" != 0'
if ($LASTEXITCODE -ne 0) { throw 'Configure a normal default Linux user, not root, for this distribution.' }

# Minimal WSL images may not yet include Python. Bootstrap only with explicit opt-in.
& wsl.exe --distribution $Distro --exec /bin/sh -c 'command -v python3 >/dev/null'
if ($LASTEXITCODE -ne 0) {
    if (-not $InstallSystemPackages) { throw 'Python3 is missing in WSL. Rerun with -InstallSystemPackages.' }
    & wsl.exe --distribution $Distro --exec /bin/sh -c 'sudo apt-get update && sudo apt-get install -y python3'
    if ($LASTEXITCODE -ne 0) { throw 'WSL Python3 bootstrap failed.' }
}

$scriptPath = Join-Path $PSScriptRoot 'install.py'
$converted = & wsl.exe --distribution $Distro --exec wslpath -a -u $scriptPath
if ($LASTEXITCODE -ne 0) { throw 'Cannot translate the repository path into WSL.' }
$linuxScript = (($converted -join "`n") -replace "`0", '').Trim()
$arguments = @('--distribution', $Distro, '--exec', 'python3', $linuxScript)
if ($Prefix) { $arguments += @('--prefix', $Prefix) }
if ($Jobs -gt 0) { $arguments += @('--jobs', "$Jobs") }
if ($InstallSystemPackages) { $arguments += '--install-system-packages' }
if ($ReuseOpenfoam) { $arguments += @('--reuse-openfoam', $ReuseOpenfoam) }
if ($SourceCache) { $arguments += @('--source-cache', $SourceCache) }
if ($VisitPlatform) { $arguments += @('--visit-platform', $VisitPlatform) }
if ($SkipVisualization) { $arguments += '--skip-visualization' }
if ($Examples) { $arguments += '--examples' }
if ($SmokeTest) { $arguments += '--smoke-test' }
& wsl.exe @arguments
exit $LASTEXITCODE
