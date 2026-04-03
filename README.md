# Smart Cooling

A Home Assistant custom integration that predicts whether your room will reach a target temperature (e.g., bedtime comfort) using hourly weather forecast data and a self-learning physics model. It recommends the least-energy method — natural ventilation, fan, or AC — and tells you exactly when to act.

---

## Features

- **Hourly forecast-aware predictions** — simulates indoor temperature hour-by-hour using your weather entity's forecast, not just current outdoor temp
- **Physics-based thermal model** — accounts for wall insulation, solar gain, passive ventilation, fan, and AC cooling rates
- **Energy-efficient strategy selection** — prefers open window → fan → AC, escalating only when needed
- **Adaptive learning** — tracks prediction accuracy over time and builds confidence score
- **Tolerance-aware scheduling** — gives lower-energy methods extra time before escalating to AC
- **8 sensors per room** — recommendation, predicted temp, deficit, confidence, time-to-target, will-reach-target-at, action-needed-by, reasoning

---

## Requirements

- Home Assistant 2024.1 or later
- A weather entity that supports `weather.get_forecasts` with hourly data (e.g., `weather.home` from the Met.no or Open-Meteo integrations)
- An indoor temperature sensor for each room

---

## Installation

1. Copy the `custom_components/smart_cooling` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** and search for **Smart Cooling**
4. Complete the setup wizard (see [Configuration](#configuration))

---

## Configuration

Setup is a 4-step wizard. The global settings (Step 1) only appear once for your first room; additional rooms reuse them.

### Step 1 — Global Settings *(first room only)*

| Field | Required | Description |
|---|---|---|
| Weather entity | ✓ | Hourly forecast source. Must support `weather.get_forecasts` with `type: hourly`. Provides temperature, wind speed, humidity, and condition per hour. |
| Outdoor temperature sensor | ✓ | Current outdoor temperature sensor |
| AQI sensor | — | Air Quality Index sensor. When AQI > 150, window/fan options are suppressed. |

### Step 2 — Room Identity & Sensors

| Field | Required | Description |
|---|---|---|
| Room name | ✓ | Unique display name used in entity IDs (e.g., `master_bedroom`) |
| Indoor temperature sensor | ✓ | Primary temperature sensor for this room |
| Indoor humidity sensor | — | Optional; used for future comfort calculations |

### Step 3 — Device State Sensors

These tell the integration what is currently running so recommendations say "keep open" instead of "open":

| Field | Description |
|---|---|
| Window sensor | Binary sensor — is the window open? |
| Fan sensor | Binary sensor — is the fan running? |
| AC sensor | Binary sensor — is the AC running? |

### Step 4 — Targets & Behavior

| Field | Default | Range | Description |
|---|---|---|---|
| Target temperature entity | — | — | `input_number` helper holding the desired temperature to reach |
| Target time entity | 22:30 | — | `input_datetime` helper for the deadline (e.g., bedtime) |
| Tolerance minutes | 30 | 0–120 min | Extra grace window for lower-energy methods; see [Tolerance](#tolerance) |
| Enable learning | On | — | Whether the model adapts from actual outcomes |

All options except room name are editable after setup via **Settings → Devices & Services → Smart Cooling → Configure**.

---

## Sensors

Each room creates 8 sensors with IDs following the pattern `sensor.smart_cooling_{room_name}_{key}`:

| Sensor | What it shows |
|---|---|
| `recommendation` | Human-readable action, e.g. *"Open window now"* or *"Start AC — target may not be reached"* |
| `predicted_target_temp` | Predicted °F the room will be at the target time under the current strategy. Attributes include `hourly_predictions`, `forecast_entries`, `forecast_sample`, and `physics_params`. |
| `cooling_deficit` | Degrees above target the room is predicted to be at the deadline. Negative = room will overshoot (good). |
| `prediction_confidence` | 0–100% accuracy confidence based on past predictions. Below 10 validated predictions, shows 50%. See [Confidence](#prediction-confidence). |
| `time_to_target` | Hours remaining until the target time |
| `will_reach_target_at` | Datetime when the room is predicted to reach the target temperature. `Unknown` if already there. |
| `action_needed_by` | Latest datetime to start the recommended action and still meet the deadline. `Unknown` if no action is needed. Attributes include `overdue` and `minutes_remaining`. |
| `reasoning` | Plain-English explanation of why this strategy was chosen. Full text in `full_reasoning` attribute. |

### Predicted Temperature Attributes

The `predicted_target_temp` sensor attributes are useful for debugging:

```yaml
hourly_predictions:
  - hour: 17
    time: '2026-04-02T17:49:59-07:00'
    predicted_temp: 70.6
    outdoor_temp: 53
    heat_gain: -1.4
    cooling: 0
    net_change: -1.4
  # ... one entry per simulated hour
forecast_entries: 168        # total hourly forecast points loaded
forecast_sample:             # first 4 forecast points (UTC)
  - datetime: '2026-04-03T00:00:00+00:00'
    temperature: 53
physics_params:              # current model parameters for this room
  base_heat_gain_rate: 0.5
  thermal_transfer_coefficient: 0.1
  # ...
```

---

## How It Works

### Thermal Model

Every update cycle, the model simulates the room temperature hour-by-hour from now until the target time. For each hour it:

1. Looks up the outdoor temperature from the **hourly weather forecast** (matched within 90 minutes)
2. Computes heat exchange through walls: `thermal_transfer_coefficient × (outdoor − indoor)`
3. Adds `base_heat_gain_rate` for internal heat sources
4. Adds solar gain if it is peak hours (noon–6 PM)
5. Subtracts cooling from the active strategy (natural ventilation, fan, or AC)

The simulation produces a predicted indoor temperature at the target time and an `hours_to_cool` estimate for each strategy evaluated.

### Strategy Selection

The engine evaluates three methods in energy-efficiency order and picks the first one that can reach the target within `target_time + tolerance`:

1. **Natural ventilation** (open window) — evaluated if AQI ≤ 150
2. **Fan** — evaluated if AQI ≤ 150
3. **AC** — always evaluated as fallback

If no method can reach the target, AC is selected but the recommendation is labeled *"LATE — target may not be reached"*.

The recommendation text reflects what is already running:

| State | Recommendation |
|---|---|
| Window recommended, window closed | *Open window* |
| Window recommended, window open | *Keep window open* |
| Fan recommended, fan off | *Start fan* |
| Fan recommended, fan running | *Continue fan* |
| AC recommended, AC off | *Start AC* |
| AC recommended, AC running | *Continue AC* |
| Already at/near target | *No action needed* |

### Tolerance

`tolerance_minutes` (default **30**) gives lower-energy methods a grace window before escalating. For example, if AC would reach 69°F by 10:00 PM but a fan would reach it by 10:25 PM, and tolerance is 30 minutes, the fan is chosen.

| Tolerance | Effect |
|---|---|
| 0 min | Must hit target exactly on time; fan chosen only if as fast as AC |
| 30 min | Fan gets a 30-minute grace window |
| 120 min | Window/fan given 2 hours; AC used only if they truly cannot get there |

### Prediction Confidence

Confidence is built from validated predictions — cases where a prediction was made and the actual temperature at the target time was later recorded.

- **< 10 validated predictions** → **50%** (baseline; no track record yet)
- **10+ predictions** → `max(30%, 100% − (MAE ÷ 7°F) × 100%)`

| Mean Absolute Error | Confidence |
|---|---|
| 0°F | 100% |
| 1.4°F | ~80% |
| 3.5°F | ~50% |
| ≥ 4.9°F | 30% (floor) |

Confidence accumulates automatically — every night after the target time passes, the actual reading is recorded and the model is scored. After a week or two of normal use it has a meaningful value.

---

## Physics Parameters

These control the thermal model and are stored per-room in `.storage/smart_cooling/`. Defaults work for most bedrooms; calibrate or tune manually if predictions are consistently off.

| Parameter | Default | Unit | When to adjust |
|---|---|---|---|
| `base_heat_gain_rate` | 0.5 | °F/hr | Raise if room heats faster than predicted when outdoor ≈ indoor (many appliances, occupied room) |
| `thermal_transfer_coefficient` | 0.1 | °F/hr per °F differential | Raise for poorly insulated rooms; lower for well-insulated. 0.05 = very good, 0.3 = poor |
| `solar_gain_factor` | 0.6 | multiplier | Raise for south/west-facing rooms with many windows |
| `ac_cooling_rate_mild` | 4.5 | °F/hr | Adjust if AC reaches target faster/slower on mild days (outdoor < 82°F) |
| `ac_cooling_rate_hot` | 2.5 | °F/hr | Adjust for hot days (outdoor ≥ 82°F) |
| `natural_cooling_effectiveness` | 0.05 | coefficient | The passive airflow bonus from opening windows, on top of wall conduction. Range 0.2–0.5 for a breezy room. |
| `fan_cooling_effectiveness` | 0.15 | coefficient | Raise if fan is more effective than predicted |
| `fan_equivalent_wind_speed` | 8.0 | mph | Effective wind speed assumed for fan on calm nights |

> **Note:** `base_heat_gain_rate` defaults only apply to new rooms. If a room was set up before this was changed, update it via `smart_cooling.set_params` or `smart_cooling.calibrate`.

---

## Services

### `smart_cooling.set_params`

Manually override physics parameters for a room. Changes persist across restarts and take effect immediately.

```yaml
service: smart_cooling.set_params
data:
  entry_id: "abc123"                    # required
  base_heat_gain_rate: 0.5              # optional — omit to leave unchanged
  thermal_transfer_coefficient: 0.1
  solar_gain_factor: 0.6
  ac_cooling_rate_mild: 4.5
  ac_cooling_rate_hot: 2.5
  natural_cooling_effectiveness: 0.35
  fan_cooling_effectiveness: 0.15
```

Find `entry_id` in the URL when you click the integration in **Settings → Devices & Services → Smart Cooling**.

### `smart_cooling.calibrate`

Reads historical sensor data from the HA recorder and auto-tunes parameters using regression. No CSV export needed.

```yaml
service: smart_cooling.calibrate
data:
  entry_id: "abc123"   # required
  days: 30             # optional, default 30, range 1–365
```

Results and parameter changes are logged at INFO level. Requires at least a few days of history with varying indoor/outdoor temperature conditions.

---

## Tuning Guide

### Predictions run too warm (room cools faster than predicted)
Lower `base_heat_gain_rate` or raise `thermal_transfer_coefficient`.

### Predictions run too cool (room stays warmer than predicted)
Raise `base_heat_gain_rate` or lower `thermal_transfer_coefficient`.

### Fan/window never chosen
Increase `tolerance_minutes` (try 60 or 90) to give lower-energy methods more time.

### AC takes too long or too short
Adjust `ac_cooling_rate_mild` and `ac_cooling_rate_hot` to match observed performance.

### Natural ventilation seems underrated
Raise `natural_cooling_effectiveness` (try 0.3–0.5). The default is conservative — it represents only the passive airflow bonus on top of wall conduction, which already handles most cooling when outdoor air is much cooler than indoor.

### Confidence stuck at 50%
Normal for the first week or two. The model needs at least 10 nights where the target time passes and the actual temperature is recorded.

---

## Troubleshooting

### `forecast_entries` is 0
The weather entity is not returning hourly forecast data. Verify it supports `weather.get_forecasts` with `type: hourly`.

### All hourly predictions show the same `outdoor_temp`
The forecast lookup is failing. Check that your weather entity's forecast datetimes are in UTC (ISO 8601 with `+00:00`).

### `action_needed_by` shows a time when no action is needed
Update to the latest version — fixed so `action_needed_by` returns `Unknown` when strategy is `no_action`.

### `will_reach_target_at` ticks to "now" every minute
Update to the latest version — fixed so it returns `Unknown` when the room is already at or below target.

---

## Architecture

```
coordinator.py          — DataUpdateCoordinator; orchestrates each update cycle
│
├── thermal_model.py    — Hour-by-hour temperature simulation + forecast lookup
├── strategy_engine.py  — Cooling method selection with tolerance awareness
├── learning_module.py  — Prediction recording, scoring, and confidence calculation
├── calibration.py      — OLS regression to estimate params from historical data
│
sensor.py               — 8 HA sensor entities per room
config_flow.py          — 4-step setup wizard + options flow
__init__.py             — Integration setup, service registration
const.py                — Default physics params, domain constants
```

---

## License

MIT
