# Router installer — Windows (PowerShell).
# Usage (PowerShell): irm https://.../install.ps1 | iex
#
# Mirrors install.sh: detects arch, downloads the zip from the release,
# installs under $env:LOCALAPPDATA\router, adds to PATH for the user, and
# launches the setup wizard.

$ErrorActionPreference = "Stop"

$Prefix = if ($env:ROUTER_PREFIX) { $env:ROUTER_PREFIX } else { Join-Path $env:LOCALAPPDATA "router" }
$Share  = Join-Path $Prefix "share"
$Bin    = Join-Path $Prefix "bin"
$Base   = if ($env:ROUTER_BASE_URL) { $env:ROUTER_BASE_URL } else { "https://github.com/sketcher98/CraftedWorkflowsOS/releases/latest/download" }

function Get-Arch {
    switch ($env:PROCESSOR_ARCHITECTURE) {
        "AMD64" { "amd64" }
        "ARM64" { "arm64" }
        default   { "amd64" }
    }
}

$plat = "windows-$((Get-Arch))"
$archive = "router-$plat.zip"
Write-Host "[router] platform: $plat"

New-Item -ItemType Directory -Force -Path $Share, $Bin | Out-Null

$tmp = Join-Path $env:TEMP ("router-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    if ($env:ROUTER_LOCAL_BUNDLE) {
        Copy-Item $env:ROUTER_LOCAL_BUNDLE (Join-Path $tmp $archive)
    } else {
        Write-Host "[router] downloading $archive ..."
        Invoke-WebRequest -UseBasicParsing -Uri "$Base/$archive" -OutFile (Join-Path $tmp $archive)
    }
    # Bundles ship as .tar.gz; Windows 10+ ships bsdtar which extracts them.
    tar -xzf (Join-Path $tmp $archive) -C $tmp
    if ($LASTEXITCODE -ne 0) { throw "tar extraction failed" }

    Copy-Item -Recurse -Force (Join-Path $tmp "*") $Share
    Get-ChildItem (Join-Path $Share "router.exe") | ForEach-Object { Copy-Item $_.FullName (Join-Path $Bin "router.exe") -Force }
    Copy-Item -Force (Join-Path $Share "setup\wizard.py") (Join-Path $Bin "router-setup.py")

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$Bin*") {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Bin", "User")
        Write-Host "[router] added $Bin to your PATH (new shells only)"
    }
    Write-Host "[router] installed. Run 'router-setup.py' to connect your tools."
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
