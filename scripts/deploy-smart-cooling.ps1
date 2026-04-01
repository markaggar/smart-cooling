param(
    [string]$DestPath = "\\10.0.0.62\config\custom_components\smart_cooling",
    [switch]$ForceCopy,
    [switch]$Restart
)

# Resolve repo root relative to this script
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$src = Join-Path $repoRoot "custom_components\smart_cooling"

Write-Host "Deploying Smart Cooling from: $src" -ForegroundColor Cyan
Write-Host "To: $DestPath" -ForegroundColor Cyan

if (-not (Test-Path $src)) {
    Write-Warning "Source path not found: $src"
    exit 1
}
if (-not (Test-Path $DestPath)) {
    Write-Host "Destination missing; creating: $DestPath"
    New-Item -ItemType Directory -Force -Path $DestPath | Out-Null
}

# Copy files, exclude caches/pyc; /FFT for FAT time granularity on Samba
$robocopyArgs = @(
    $src,
    $DestPath,
    '*.*','/E','/R:2','/W:2','/FFT',
    '/XF','*.pyc','*.pyo',
    '/XD','__pycache__'
)
if ($ForceCopy) {
    $robocopyArgs += @('/IS','/IT')
}

& robocopy $robocopyArgs | Out-Null
$code = $LASTEXITCODE
if ($code -lt 0) { $code = 16 }

if ($code -le 7) {
    Write-Host "Robocopy OK (code $code)" -ForegroundColor Green
    $componentCopied = ($code -band 1) -ne 0
} else {
    Write-Warning "Robocopy reported error (code $code); attempting fallback copy..."
    try {
        $files = Get-ChildItem -Path $src -Recurse -File -Force -ErrorAction Stop |
            Where-Object { $_.Name -notmatch '\.pyc$|\.pyo$' -and $_.FullName -notmatch '\\__pycache__\\' }
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($src.Length).TrimStart([char[]]"/\")
            $destFile = Join-Path $DestPath $rel
            $destDir = Split-Path -Parent $destFile
            if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
            Copy-Item -Path $f.FullName -Destination $destFile -Force
        }
        Write-Host "Fallback copy completed" -ForegroundColor Yellow
        $componentCopied = $true
    } catch {
        Write-Warning "Fallback copy failed: $($_.Exception.Message)"
        exit 1
    }
}

if ($componentCopied) {
    Write-Host "Files copied successfully" -ForegroundColor Green
} else {
    Write-Host "No changes detected" -ForegroundColor DarkGray
}

# Restart HA if requested
if ($Restart -and $componentCopied) {
    $haUrl = $env:HA_BASE_URL
    $haToken = $env:HA_TOKEN
    
    if (-not $haUrl -or -not $haToken) {
        Write-Warning "Set HA_BASE_URL and HA_TOKEN env vars to enable restart"
    } else {
        Write-Host "Restarting Home Assistant..." -ForegroundColor Yellow
        try {
            $headers = @{
                "Authorization" = "Bearer $haToken"
                "Content-Type" = "application/json"
            }
            Invoke-RestMethod -Uri "$haUrl/api/services/homeassistant/restart" -Method POST -Headers $headers | Out-Null
            Write-Host "Restart requested" -ForegroundColor Green
        } catch {
            Write-Warning "Restart failed: $($_.Exception.Message)"
        }
    }
}

Write-Host "`nDeployment complete!" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Restart Home Assistant if needed"
Write-Host "  2. Go to Settings > Devices & Services > Add Integration"
Write-Host "  3. Search for 'Smart Cooling' and configure"
