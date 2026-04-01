param(
    [string]$DestPath = "\\10.0.0.62\config\custom_components\smart_cooling",
    [string]$VerifyEntity = "sensor.smart_cooling_recommended_strategy",
    [switch]$ForceCopy,
    [switch]$SkipRestart,
    [switch]$AlwaysRestart
)

# Resolve repo root relative to this script
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$src = Join-Path $repoRoot "custom_components\smart_cooling"

$componentCopied = $false
$restartAttempted = $false
$sawDowntime = $false

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
    '/XD','__pycache__','tests'
)
if ($ForceCopy) {
    $robocopyArgs += @('/IS','/IT')
}

& robocopy $robocopyArgs | Out-Null
$code = $LASTEXITCODE
if ($code -lt 0) { $code = 16 }

if ($code -le 7) {
    Write-Host "Robocopy OK (code $code)" -ForegroundColor DarkGray
    if (($code -band 1) -ne 0) { $componentCopied = $true }
} else {
    Write-Warning "Robocopy reported error (code $code); attempting fallback copy..."
    try {
        $files = Get-ChildItem -Path $src -Recurse -File -Force -ErrorAction Stop |
            Where-Object { $_.Name -notmatch '\.pyc$|\.pyo$' -and $_.FullName -notmatch '\\__pycache__\\|\\tests\\' }
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

# If no changes and not forcing restart, exit early
if (-not $componentCopied) {
    if ($AlwaysRestart) {
        Write-Host "No changes detected, but -AlwaysRestart specified." -ForegroundColor Yellow
    } else {
        Write-Host "No changes detected. Skipping HA restart." -ForegroundColor DarkGray
        exit 0
    }
} else {
    Write-Host "Files copied successfully" -ForegroundColor Green
}

# Skip restart if requested
if ($SkipRestart) {
    Write-Host "Skipping restart (-SkipRestart specified)." -ForegroundColor Yellow
    exit 0
}

# Infer HA base URL from share path if not set
$baseUrl = $env:HA_BASE_URL
$token = $env:HA_TOKEN
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    if ($DestPath -match '^\\\\([^\\]+)\\') {
        $haHost = $Matches[1]
        $baseUrl = "http://$haHost`:8123"
        Write-Host "HA_BASE_URL not set; inferring $baseUrl from share host" -ForegroundColor DarkGray
    }
}

if ([string]::IsNullOrWhiteSpace($baseUrl) -or [string]::IsNullOrWhiteSpace($token)) {
    Write-Warning "Set HA_BASE_URL and HA_TOKEN env vars to enable restart"
    Write-Host "`nDeployment complete! Please restart HA manually." -ForegroundColor Yellow
    exit 0
}

$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }
$basicHeaders = @{ Authorization = "Bearer $token" }

# Quick API check
try {
    Write-Host "Checking HA API: $baseUrl/api/config" -ForegroundColor DarkGray
    $cfgResp = Invoke-WebRequest -Method Get -Uri "$baseUrl/api/config" -Headers $headers -TimeoutSec 15 -ErrorAction Stop
    Write-Host "HA API reachable (HTTP $($cfgResp.StatusCode))." -ForegroundColor DarkGray
} catch {
    Write-Warning "HA API check failed: $($_.Exception.Message)"
}

# Config check before restart
$configValid = $true
try {
    $checkUri = "$baseUrl/api/config/core/check_config"
    Write-Host "Running HA config check: $checkUri" -ForegroundColor DarkGray
    $checkResp = Invoke-RestMethod -Method Post -Uri $checkUri -Headers $headers -TimeoutSec 60 -ErrorAction Stop
    $result = $checkResp.result
    if ($result -and $result.ToString().ToLower() -ne 'valid') {
        $configValid = $false
        Write-Warning "Config check reported INVALID configuration:"
        if ($checkResp.errors) { Write-Host $checkResp.errors -ForegroundColor Yellow }
    } else {
        Write-Host "Config check PASSED." -ForegroundColor DarkGray
    }
} catch {
    $status = $null
    if ($_.Exception.Response) { try { $status = $_.Exception.Response.StatusCode.value__ } catch {} }
    if ($status -eq 404) {
        Write-Host "Config check not available (404); skipping." -ForegroundColor DarkGray
    } else {
        Write-Warning "Config check failed: $($_.Exception.Message). Proceeding to restart."
    }
}

if (-not $configValid) {
    Write-Host "Skipping restart due to invalid configuration." -ForegroundColor Yellow
    exit 2
}

# Request restart
$uri = "$baseUrl/api/services/homeassistant/restart"
try {
    Write-Host "Requesting HA Core restart: $uri" -ForegroundColor Cyan
    $resp = Invoke-WebRequest -Method Post -Uri $uri -Headers $headers -Body '{}' -TimeoutSec 30 -ErrorAction Stop
    Write-Host "HA restart requested (HTTP $($resp.StatusCode))." -ForegroundColor Green
    $restartAttempted = $true
} catch {
    $msg = $_.Exception.Message
    $status = $null
    if ($_.Exception.Response) { try { $status = $_.Exception.Response.StatusCode.value__ } catch {} }
    if ($status -in 502,504 -or $msg -match 'actively refused') {
        Write-Host "Restart likely in progress; HA may be temporarily unavailable." -ForegroundColor Yellow
        $restartAttempted = $true
    } else {
        Write-Warning "HA restart failed: $msg"
        $restartAttempted = $true
    }
}

# Poll for HA to come back online
$maxWait = 60
$interval = 2
$elapsed = 0
$back = $false
Write-Host "Waiting up to ${maxWait}s for HA to come back online..." -ForegroundColor DarkGray
while ($elapsed -lt $maxWait) {
    try {
        $ping = Invoke-WebRequest -Method Get -Uri "$baseUrl/api/config" -Headers $headers -TimeoutSec 10 -ErrorAction Stop
        if ($ping.StatusCode -eq 200) {
            Write-Host "HA back online after ${elapsed}s." -ForegroundColor Green
            $back = $true
            break
        }
    } catch {
        $sawDowntime = $true
    }
    Start-Sleep -Seconds $interval
    $elapsed += $interval
}

if (-not $back) {
    Write-Warning "HA did not respond within ${maxWait}s; it may still be restarting."
}

# Wait for integration to load
if ($back -and -not [string]::IsNullOrWhiteSpace($VerifyEntity)) {
    $verifyMaxWait = 30
    $verifyInterval = 2
    Write-Host "Waiting for entity: $VerifyEntity (timeout ${verifyMaxWait}s)" -ForegroundColor DarkGray
    $ok = $false
    $elapsed = 0
    while ($elapsed -lt $verifyMaxWait) {
        try {
            $stateResp = Invoke-WebRequest -Method Get -Uri "$baseUrl/api/states/$VerifyEntity" -Headers $headers -TimeoutSec 10 -ErrorAction Stop
            if ($stateResp.StatusCode -eq 200) {
                $obj = $stateResp.Content | ConvertFrom-Json
                $st = [string]$obj.state
                if ($st -and $st -ne 'unknown' -and $st -ne 'unavailable') {
                    Write-Host "Entity $VerifyEntity is available: $st" -ForegroundColor Green
                    $ok = $true
                    break
                }
            }
        } catch {
            # 404 or other while HA is still initializing
        }
        Start-Sleep -Seconds $verifyInterval
        $elapsed += $verifyInterval
    }
    if (-not $ok) {
        Write-Host "Entity $VerifyEntity not yet available (integration may not be configured)." -ForegroundColor Yellow
    }
}

Write-Host "`nDeployment complete!" -ForegroundColor Cyan
