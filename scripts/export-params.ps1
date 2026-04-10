<#
.SYNOPSIS
  Export learned physics parameters from HA .storage to local data/ folder.

.DESCRIPTION
  The learning module stores per-room calibrated physics params to:
    \\<ha-host>\config\.storage\smart_cooling\params_<entry_id>.json

  This script copies those files to the local data\ folder and writes a
  human-readable index mapping entry IDs to room names (read from HA API).

  Output: data\params_<room_slug>.json
          data\params_index.json  (entry_id → room name mapping)

  After running this script you can use the learned params in simulations:
    python scripts/simulate_scenario.py --params data/params_master_bedroom.json

.PARAMETER HAHost
  Home Assistant hostname or IP (default: 10.0.0.62)

.PARAMETER HAPort
  Home Assistant port (default: 8123)

.PARAMETER StoragePath
  UNC path to the HA config share (default: \\10.0.0.62\config)

.PARAMETER OutDir
  Local folder to write params files into (default: .\data)

.EXAMPLE
  .\scripts\export-params.ps1
  .\scripts\export-params.ps1 -HAHost 192.168.1.10 -StoragePath \\192.168.1.10\config
#>
[CmdletBinding()]
param(
    [string]$HAHost     = "10.0.0.62",
    [int]   $HAPort     = 8123,
    [string]$StoragePath = "\\10.0.0.62\config",
    [string]$OutDir     = ".\data"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Step  { param([string]$msg) Write-Host $msg -ForegroundColor Cyan }
function Write-OK    { param([string]$msg) Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "  WARN  $msg" -ForegroundColor Yellow }
function Write-Fail  { param([string]$msg) Write-Host "  FAIL  $msg" -ForegroundColor Red; exit 1 }

function Slug([string]$name) {
    # Convert room name to filesystem-safe slug
    $name.ToLower() -replace '[^a-z0-9]+', '_' -replace '^_|_$', ''
}

# ---------------------------------------------------------------------------
# 1. Locate params files on HA share
# ---------------------------------------------------------------------------
$storageDir = Join-Path $StoragePath ".storage\smart_cooling"

Write-Step "Looking for params files in: $storageDir"

if (-not (Test-Path $storageDir)) {
    Write-Fail "Could not reach '$storageDir'. Is the share mounted? Is HA running?"
}

$paramsFiles = Get-ChildItem $storageDir -Filter "params_*.json" -ErrorAction SilentlyContinue
if (-not $paramsFiles) {
    Write-Warn "No params_*.json files found in '$storageDir'."
    Write-Warn "The learning module writes params after at least a few prediction cycles complete."
    exit 0
}

Write-OK "$($paramsFiles.Count) params file(s) found."

# ---------------------------------------------------------------------------
# 2. Resolve room names from HA API
# ---------------------------------------------------------------------------
$token = $env:HA_TOKEN
$headers = @{}
if ($token) {
    $headers["Authorization"] = "Bearer $token"
}
$apiBase = "http://${HAHost}:${HAPort}/api"

$entryNames = @{}   # entry_id → room name

try {
    Write-Step "Fetching integration config entries from HA API..."
    $resp = Invoke-RestMethod -Uri "$apiBase/config/config_entries/entry" `
        -Headers $headers -ErrorAction Stop
    foreach ($entry in $resp) {
        if ($entry.domain -eq "smart_cooling") {
            $entryNames[$entry.entry_id] = $entry.title ?? $entry.entry_id
        }
    }
    Write-OK "$($entryNames.Count) smart_cooling entry(ies) found."
} catch {
    Write-Warn "Could not reach HA API ($($_.Exception.Message)). Room names will use entry IDs."
}

# ---------------------------------------------------------------------------
# 3. Copy and annotate params files
# ---------------------------------------------------------------------------
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
    Write-OK "Created output directory: $OutDir"
}

$index = @{}

foreach ($file in $paramsFiles) {
    # Extract entry_id from filename: params_<entry_id>.json
    $entryId = $file.BaseName -replace '^params_', ''
    $roomName = $entryNames[$entryId] ?? $entryId
    $slug = Slug $roomName

    $destName = "params_${slug}.json"
    $destPath = Join-Path $OutDir $destName

    # Read, annotate with metadata, write locally
    $content = Get-Content $file.FullName -Raw | ConvertFrom-Json -AsHashtable
    $annotated = [ordered]@{
        _meta = [ordered]@{
            entry_id    = $entryId
            room_name   = $roomName
            exported_at = (Get-Date -Format "o")
            source      = $file.FullName
        }
    }
    foreach ($k in $content.Keys) {
        $annotated[$k] = $content[$k]
    }

    $annotated | ConvertTo-Json -Depth 5 | Set-Content $destPath -Encoding UTF8
    Write-OK "Exported: $destPath  (room: $roomName)"

    $index[$entryId] = @{
        room_name  = $roomName
        slug       = $slug
        file       = $destName
    }
}

# Write index
$indexPath = Join-Path $OutDir "params_index.json"
$index | ConvertTo-Json -Depth 4 | Set-Content $indexPath -Encoding UTF8
Write-OK "Index written: $indexPath"

# ---------------------------------------------------------------------------
# 4. Show usage hint
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Usage in simulation:" -ForegroundColor Cyan
foreach ($entry in $index.GetEnumerator()) {
    $f = $entry.Value.file
    Write-Host "  python scripts/simulate_scenario.py --params data/$f scenarios/*.yaml"
}
