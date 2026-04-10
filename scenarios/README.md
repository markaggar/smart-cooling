# Smart Cooling Scenario Files

YAML files in this folder are run by `scripts/simulate_scenario.py`.

## Usage

```powershell
# Run all built-in scenarios
python scripts/simulate_scenario.py

# Run a single YAML scenario file
python scripts/simulate_scenario.py scenarios/master_bedroom_cool_eve.yaml

# Run with learned params from HA
python scripts/simulate_scenario.py --params data/params_master_bedroom.json scenarios/master_bedroom_cool_eve.yaml
```

## YAML Schema

```yaml
name: "Human-readable scenario name"
description: "Optional longer description"

# expected_method must match the CoolingMethod value string:
#   no_action | open_window | start_fan | continue_fan | start_ac | continue_ac | close_window
expected_method: start_fan

# Optional: assert the timing string contains this substring (e.g. "by" for deferred)
expected_timing_contains: "by"

conditions:
  indoor_temp: 68.0         # °F
  outdoor_temp: 65.0        # °F
  outdoor_humidity: 55.0    # %
  aqi: 42.0
  wind_speed: 5.0           # mph
  target_temp: 63.0         # °F
  target_time: "22:00:00"   # HH:MM:SS
  current_time: "2026-04-09T16:00:00"  # ISO datetime — date is ignored, only time matters

  window_open: false
  fan_running: false
  ac_running: false
  fan_available: true
  ac_available: true
  fan_sensor_configured: true
  ac_sensor_configured: true
  window_sensor_configured: true
  ac_setpoint: 83.0   # °F — background thermostat ceiling. null if not applicable.

  forecast:
    - datetime: "2026-04-09T16:00:00"
      temperature: 65.0
      wind_speed: 5.0
      precipitation: 0.0
      humidity: 55.0
    - datetime: "2026-04-09T17:00:00"
      temperature: 63.0
      wind_speed: 5.0
      precipitation: 0.0
      humidity: 57.0
    # ... add one entry per hour through target_time
```

## Tips

- Set `current_time` to the scenario start time; the date part is used but only time matters for strategy logic.
- `forecast` should cover at least `hours_to_target` entries starting at `current_time`.
- If `ac_setpoint` is null/absent, background thermostat ceiling is disabled.
- Omitting `expected_method` / `expected_timing_contains` still runs the scenario and prints output — useful for exploratory runs.
