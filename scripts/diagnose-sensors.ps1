<#
.SYNOPSIS
  Query Smart Cooling sensor states from Home Assistant and show full diagnostic output.

.DESCRIPTION
  Reads all smart_cooling entities + configured input entities + hourly forecast.
  Uses HA_TOKEN env var and infers the HA URL from HA_BASE_URL or the deploy-script default.
  Run after each deploy to evaluate recommendation quality without manual HA interaction.

.EXAMPLE
  .\scripts\diagnose-sensors.ps1
  .\scripts\diagnose-sensors.ps1 -Verbose
#>

param(
    [string]$BaseUrl,
    [string]$Token,
    [switch]$ShowForecast,
    [switch]$ShowHourlyBreakdown
)

if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = $env:HA_BASE_URL }
if ([string]::IsNullOrWhiteSpace($Token))   { $Token   = $env:HA_TOKEN }
if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = "http://10.0.0.62:8123" }

if ([string]::IsNullOrWhiteSpace($Token)) { Write-Error "Set HA_TOKEN env var"; exit 1 }

$h = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }

function Get-HA($path) {
    try { return Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/$path" -Headers $h -TimeoutSec 15 }
    catch { return $null }
}

function fmt($v) { if ($null -eq $v) { return "(null)" }; return "$v" }

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  SMART COOLING DIAGNOSTIC" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# --- INPUT ENTITIES ---
Write-Host "INPUT ENTITIES" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────" -ForegroundColor DarkGray
$inputs = @(
    "sensor.ca_outside_temperature_coldest_sensor",
    "sensor.ca_master_bed_air_quality_temperature",
    "sensor.ca_master_bed_air_quality_humidity",
    "sensor.ca_airnow_aqi",
    "binary_sensor.ca_master_bedroom_windows_open",
    "binary_sensor.ca_master_bed_window_fan_state",
    "binary_sensor.ca_tstat_upstairs_ac_on",
    "input_number.ca_upstairs_bedrooms_cooling_setpoint",
    "input_number.master_bedroom_target_temp",
    "input_datetime.master_bedroom_target_temp_time"
)
foreach ($eid in $inputs) {
    $s = Get-HA "states/$eid"
    $col = if ($null -eq $s -or $s.state -in "unavailable","unknown") { "Red" } else { "Green" }
    $val = if ($null -eq $s) { "NOT FOUND" } else { $s.state }
    Write-Host ("  {0,-55} {1}" -f $eid, $val) -ForegroundColor $col
}

# --- WEATHER FORECAST SAMPLE ---
Write-Host "`nWEATHER FORECAST (next 8 hours)" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────" -ForegroundColor DarkGray
$wx = Get-HA "states/weather.chezaggar"
if ($wx) {
    Write-Host ("  Condition: {0}" -f $wx.state) -ForegroundColor Green
    $fc = $wx.attributes.forecast
    if ($fc) {
        $n = [Math]::Min(8, $fc.Count)
        for ($i = 0; $i -lt $n; $i++) {
            $f = $fc[$i]
            $dt = try { ([datetime]$f.datetime).ToString("HH:mm") } catch { $f.datetime }
            Write-Host ("  [{0}] temp={1,5}°F  wind={2,4}mph  humidity={3,3}%  cond={4}" -f `
                $dt, (fmt $f.temperature), (fmt $f.wind_speed), (fmt $f.humidity), (fmt $f.condition))
        }
    } else { Write-Host "  No forecast data in attributes" -ForegroundColor Red }
} else { Write-Host "  weather.chezaggar not found" -ForegroundColor Red }

# --- SMART COOLING SENSORS ---
Write-Host "`nSMART COOLING SENSORS" -ForegroundColor Yellow
Write-Host "─────────────────────────────────────────" -ForegroundColor DarkGray
$all = Get-HA "states"
$sc = $all | Where-Object { $_.entity_id -like "*smart_cooling*" } | Sort-Object entity_id

if (-not $sc -or $sc.Count -eq 0) {
    Write-Host "  No smart_cooling entities found!" -ForegroundColor Red
} else {
    Write-Host "  Found $($sc.Count) entities" -ForegroundColor DarkGray
}

foreach ($e in $sc) {
    $col = if ($e.state -in "unavailable","unknown","") { "Red" } else { "White" }
    Write-Host ("`n  ◆ {0}" -f $e.entity_id) -ForegroundColor Yellow
    Write-Host ("    state: {0}" -f $e.state) -ForegroundColor $col

    # Selectively show the most useful attributes
    $attrs = $e.attributes
    if ($attrs) {
        $show = @("method","timing","indoor_temp","outdoor_temp","target_temp",
                  "predicted_temp","cooling_deficit","confidence",
                  "hours_to_cool","cooling_method","readable","overdue","minutes_remaining",
                  "comfort_phase","required_start_temp","window_peak_temp",
                  "peak_temp_open","peak_temp_closed","forecast_entries",
                  "recommendation")
        foreach ($k in $show) {
            $v = $attrs.$k
            if ($null -ne $v) {
                Write-Host ("    {0}: {1}" -f $k, $v) -ForegroundColor DarkGray
            }
        }

        # Full reasoning
        $fr = $attrs.full_reasoning
        if (-not $fr) { $fr = $attrs.reasoning }
        if ($fr) {
            Write-Host "    reasoning:" -ForegroundColor DarkGray
            # Wrap at 90 chars
            $words = $fr -split ' '
            $line = "      "
            foreach ($w in $words) {
                if (($line + $w).Length -gt 90) { Write-Host $line; $line = "      $w " }
                else { $line += "$w " }
            }
            if ($line.Trim()) { Write-Host $line }
        }

        # Alternatives (hours_to_cool per strategy — key for debugging fan vs AC)
        $alts = $attrs.alternatives
        if ($alts) {
            Write-Host "    alternatives:" -ForegroundColor DarkGray
            foreach ($alt in $alts) {
                $achieves = if ($alt.achieves_target) { "✓" } else { "✗" }
                $htc = if ($null -ne $alt.hours_to_cool) { "$($alt.hours_to_cool)h" } else { "unreachable" }
                Write-Host ("      {0} {1,-15} hours_to_cool={2,10}  pred_temp={3}" -f `
                    $achieves, $alt.method, $htc, (fmt $alt.predicted_temp)) -ForegroundColor DarkCyan
            }
        }

        # Physics params (from predicted temp sensor)
        $pp = $attrs.physics_params
        if ($pp) {
            Write-Host "    physics_params (learned):" -ForegroundColor DarkGray
            $pp.PSObject.Properties | ForEach-Object {
                Write-Host ("      {0}: {1}" -f $_.Name, $_.Value) -ForegroundColor DarkGray
            }
        }

        # Hourly breakdown (if requested)
        if ($ShowHourlyBreakdown) {
            $hp = $attrs.hourly_predictions
            if ($hp -and $hp.Count -gt 0) {
                Write-Host "    hourly_predictions:" -ForegroundColor DarkGray
                foreach ($row in $hp) {
                    $t = try { ([datetime]$row.time).ToString("HH:mm") } catch { $row.time }
                    Write-Host ("      [{0}] temp={1,5}°F  gain={2,5}  cool={3,5}  net={4,6}" -f `
                        $t, $row.predicted_temp, $row.heat_gain, $row.cooling, $row.net_change) -ForegroundColor DarkGray
                }
            }
        }

        # Forecast sample (from predicted temp sensor)
        if ($ShowForecast) {
            $fs = $attrs.forecast_sample
            if ($fs -and $fs.Count -gt 0) {
                Write-Host "    forecast_sample (bias-corrected):" -ForegroundColor DarkGray
                foreach ($f in $fs) {
                    $dt = try { ([datetime]$f.datetime).ToString("HH:mm") } catch { $f.datetime }
                    Write-Host ("      [{0}] temp={1,5}°F  wind={2,4}  humidity={3}%" -f `
                        $dt, (fmt $f.temperature), (fmt $f.wind_speed), (fmt $f.humidity)) -ForegroundColor DarkGray
                }
            }
        }
    }
}

Write-Host "`n========================================`n" -ForegroundColor Cyan
