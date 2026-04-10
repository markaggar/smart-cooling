# Smart Cooling

A Home Assistant custom integration that predicts whether your room will reach a target temperature (e.g., bedtime comfort) using hourly weather forecast data and a self-learning physics model. It recommends the least-energy method — natural ventilation, fan, or AC — and tells you exactly when to act.

---

## Features

- **Hourly forecast-aware predictions** — simulates indoor temperature hour-by-hour using your weather entity's forecast, not just current outdoor temp
- **Physics-based thermal model** — accounts for wall insulation, solar gain, passive ventilation, fan, and AC cooling rates; fan/window effectiveness reduced by high outdoor humidity
- **Energy-efficient strategy selection** — prefers open window → fan → AC, escalating only when needed
- **Lazy-start timing** — never shouts "NOW!" when there is buffer time; tells you the latest you need to act (e.g., *"Start fan by 9:45 PM"*) so devices run for the minimum time required
- **Close-window detection** — if a window is open but outdoor conditions have turned counterproductive (outside warmer than inside, AQI spike, or over-cooling risk), the recommendation immediately switches to *"Close window"* with a reason
- **Adaptive learning** — segmented by what was running (passive, window, fan, AC); each mode independently tunes its own parameters from nightly outcomes
- **Tolerance-aware scheduling** — gives lower-energy methods extra time before escalating to AC
- **Overnight comfort window** — optionally configure a wake time so the model predicts whether the room will stay within a comfort tolerance *through the entire night*, not just at bedtime. It calculates the required pre-cool target temperature and recommends the appropriate strategy to maintain it until morning
- **10 sensors per room** — recommendation, two predicted-temp sensors (no-action baseline and with-recommendation), deficit, confidence, time-to-target, will-reach-target-at, action-needed-by, reasoning, and a configured-sensors diagnostic sensor
- **AC setpoint awareness** — optional thermostat setpoint entity prevents the model from predicting AC cooling past the temperature the AC will actually stop at
- **Close-window wind context** — when recommending to close a window, the reasoning now explains whether calm walls, light breeze, or active wind is driving the predicted cool-down
- **Forecast bias correction** — actual outdoor sensor reading is used to anchor the near-term forecast. Any gap between the sensor and the forecast's current-hour temperature is applied to the first several forecast hours with exponential decay, fading to zero by ~6 hours out

---

## Requirements

- Developed with Home Assistant 2024.1 or later. Probably works with older versions in the last year or so.
- A weather entity that supports `weather.get_forecasts` with hourly data (e.g., `weather.home` from the Met.no, Open-Meteo or Pirate Weather integrations)
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
| Weather entity | ✓ | Hourly forecast source. Must support `weather.get_forecasts` with `type: hourly`. Provides temperature, wind speed, wind bearing, humidity, and condition per hour. See [Forecast attributes](#forecast-attributes). |
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

| Field | Default | Description |
|---|---|---|
| Window sensor | — | Binary sensor — is the window open? |
| Fan sensor | — | Binary sensor — is the fan running? |
| AC sensor | — | Binary sensor — is the AC running? |
| Window facing directions | — | Multi-select — compass directions your windows face (N, NE, E, SE, S, SW, W, NW). When set, the effective wind contribution to fan and natural ventilation is scaled by how directly the wind blows through those windows. Leave empty to use full wind speed regardless of direction. |
| Fan available | On | Whether this room has a fan at all. When **off**, fan recommendations are skipped and the strategy escalates directly from window → AC. |
| AC available | On | Whether this room has air conditioning. When **off**, AC is never recommended; if cooling cannot be achieved by window alone, the integration instead warns about the predicted temperature at the target time (e.g., *“Room predicted to reach 81°F — consider moving to a cooler area”*). |

### Step 4 — Targets & Behavior

| Field | Default | Range | Description |
|---|---|---|---|
| Target temperature entity | — | — | `input_number` helper holding the desired temperature to reach |
| Target time entity | 22:30 | — | `input_datetime` helper for the deadline (e.g., bedtime) |
| Tolerance minutes | 30 | 0–120 min | Extra grace window for lower-energy methods; see [Tolerance](#tolerance) |
| Enable learning | On | — | Whether the model adapts from actual outcomes |
| Comfort window end entity | — | — | `input_datetime` helper for when comfort must end (e.g., wake time). When set, activates the overnight comfort window; see [Comfort Window](#comfort-window) |
| Comfort tolerance | 2.0 | °F | How far above target is still acceptable during the overnight window |
| Prefer AC during comfort window | On | — | When on, biases toward AC to maintain overnight comfort. Turn off for rooms that prefer quiet at night. |

All options except room name are editable after setup via **Settings → Devices & Services → Smart Cooling → Configure**.

---

## Sensors

Each room creates 10 sensors with IDs following the pattern `sensor.smart_cooling_{room_name}_{key}`.

Two sensors appear in the main section of the device page; the rest are grouped under **Diagnostics**:

| Sensor | Category | What it shows |
|---|---|---|
| `recommendation` | Primary | Human-readable action, e.g. *"Open window now"* or *"Start AC — target may not be reached"* |
| `action_needed_by` | Primary | Latest datetime to start the recommended action and still meet the deadline. `Unknown` if no action is needed. Attributes include `overdue` and `minutes_remaining`. |
| `predicted_target_temp` | Diagnostic | **Predicted Temp (No Action)** — predicted °F at the target time if the current device state continues unchanged (AC stays on if running, window stays open if open, etc.). Attributes include `hourly_predictions`, `forecast_entries`, `forecast_sample`, `physics_params`, and 24-hour peak predictions. |
| `predicted_temp_with_action` | Diagnostic | **Predicted Temp (With Recommendation)** — predicted °F at the target time if the recommended action is followed immediately. Useful for seeing the gap between doing something and doing nothing. |
| `cooling_deficit` | Diagnostic | Degrees above target the room is predicted to be at the deadline. Negative = room will be below target (over-cooling). Computed from the no-action prediction. |
| `time_to_target` | Diagnostic | Hours remaining until the target time |
| `will_reach_target_at` | Diagnostic | Datetime when the room is predicted to reach the target temperature. `Unknown` if already there. |
| `reasoning` | Diagnostic | Plain-English explanation of why this strategy was chosen. Full text in `full_reasoning` attribute. |
| `prediction_confidence` | Diagnostic | 0–100% accuracy confidence based on past predictions. Below 10 validated predictions, shows 50%. See [Confidence](#prediction-confidence). |
| `configured_sensors` | Diagnostic | Count of configured sensor/entity slots for this room (including global sensors). Attributes show the live HA state of each slot. See [Configured Sensors](#configured-sensors-sensor). |

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
peak_temp_closed: 84.2      # 24h peak predicted temp with window closed (walls only)
peak_at_closed: '2026-04-08T15:00:00'   # when that peak occurs
peak_temp_open: 79.6        # 24h peak with window open (natural ventilation)
peak_at_open: '2026-04-08T14:30:00'     # when that peak occurs
```

`peak_temp_closed` and `peak_temp_open` are useful for dashboards that want to show "today's expected high" for the room under each scenario.

### Configured Sensors Sensor

`sensor.smart_cooling_{room_name}_configured_sensors` reports the count of entity slots that have been wired in for this room (including the three shared global sensors). Its **attributes contain the live HA state** of each slot — not the entity ID, but the current reading:

```yaml
# State: 9  (9 out of 11 possible slots are configured)
weather_entity:
  state: sunny
  entity_id: weather.home
outdoor_temp_sensor:
  state: "81.3"
  entity_id: sensor.backyard_temperature
aqi_sensor:
  state: "42"
  entity_id: sensor.air_quality_index
indoor_temp_sensor:
  state: "74.6"
  entity_id: sensor.bedroom_temperature
indoor_humidity_sensor: null        # not configured → null
window_sensor:
  state: "off"
  entity_id: binary_sensor.bedroom_window
fan_sensor:
  state: "off"
  entity_id: binary_sensor.bedroom_fan
ac_sensor:
  state: "on"
  entity_id: binary_sensor.bedroom_ac
ac_setpoint_entity:
  state: "72.0"
  entity_id: climate.bedroom_ac
target_temp_entity:
  state: "70.0"
  entity_id: input_number.bedroom_target_temp
target_time_entity:
  state: "22:30:00"
  entity_id: input_datetime.bedroom_bedtime
```

**Accessing attributes in HA templates:**

```yaml
# Current state of the outdoor temperature sensor
{{ state_attr('sensor.smart_cooling_bedroom_configured_sensors', 'outdoor_temp_sensor')['state'] }}
# → "81.3"

# Entity ID of the outdoor temperature sensor
{{ state_attr('sensor.smart_cooling_bedroom_configured_sensors', 'outdoor_temp_sensor')['entity_id'] }}
# → "sensor.backyard_temperature"

# Is the window open?
{{ state_attr('sensor.smart_cooling_bedroom_configured_sensors', 'window_sensor')['state'] == 'on' }}

# AC setpoint as a float (guard against null for unconfigured slots)
{% set slot = state_attr('sensor.smart_cooling_bedroom_configured_sensors', 'ac_setpoint_entity') %}
{{ slot['state'] | float if slot else none }}
```

Unconfigured slots return `null` (Python `None`) — always guard with `if slot` before accessing sub-keys. Configured but unreachable entities show `"unavailable"` in the `state` sub-key.

### Forecast Bias Correction

Every update cycle, after fetching the hourly weather forecast, the integration compares the forecast's current-hour temperature against the actual reading from your outdoor temperature sensor. If the gap is 0.5°F or more, it applies a correction to the near-term forecast temperatures:

$$T_{\text{corrected},i} = T_{\text{forecast},i} + (T_{\text{actual}} - T_{\text{forecast},0}) \times e^{-\frac{\ln 2}{t_{1/2}} \cdot i}$$

where $i$ is the hour index, and the half-life $t_{1/2}$ is **2 hours**. This means:

| Forecast hour | Correction applied |
|---|---|
| Hour 0 (now) | 100% of the offset |
| Hour 2 | 50% |
| Hour 4 | 25% |
| Hour 6+ | < 13% (effectively zero) |

This corrects for microclimate differences — your backyard sensor may consistently read 3–4°F warmer or cooler than the area forecast — and grounds the simulation in what the sensor is actually measuring right now. The correction is skipped if the outdoor sensor is unavailable.

The corrected forecast is what flows into `predict_temperature()` and is reflected in `forecast_sample` on the `predicted_target_temp` sensor.

---

## Forecast Attributes

The integration reads the following attributes from each hourly forecast entry. All are provided by Met.no, Open-Meteo, and most other HA weather integrations:

| Attribute | Used for |
|---|---|
| `datetime` | Matching the forecast entry to the simulated hour |
| `temperature` | Outdoor temperature in the thermal simulation |
| `wind_speed` | Fan and natural ventilation wind factor |
| `wind_bearing` | Wind alignment factor (degrees, 0 = N, 90 = E, 180 = S, 270 = W) |
| `humidity` | Humidity penalty for fan/window effectiveness |

Example entry (from the weather entity's hourly forecast):

```yaml
- datetime: '2026-04-04T00:00:00+00:00'
  condition: sunny
  wind_bearing: 220
  wind_speed: 2.68
  wind_gust_speed: 5.36
  temperature: 58
  apparent_temperature: 58
  dew_point: 42
  humidity: 55
  precipitation: 0
  precipitation_probability: 0
  cloud_coverage: 30
  uv_index: 2.83
  pressure: 30.36
```

The following fields are consumed. All others are ignored:

| Attribute | Used for |
|---|---|
| `datetime` | Matching the forecast entry to the simulated hour |
| `temperature` | Outdoor temperature in the thermal simulation |
| `wind_speed` | Fan and natural ventilation wind factor |
| `wind_bearing` | Wind alignment factor (degrees, 0 = N, 90 = E, 180 = S, 270 = W) |
| `humidity` | Humidity penalty for fan/window effectiveness |
| `cloud_coverage` | Reduces solar gain (0% cloud = full solar; 100% cloud = no solar) |
| `uv_index` | Scales the solar gain term; also used to estimate the day's peak solar load for thermal lag |

`cloud_coverage` and `uv_index` are used together: `solar_intensity = (uv_index / 10) × (1 − cloud_coverage / 100)`. Both are provided by Met.no, Open-Meteo, and most modern HA weather integrations.

---

## How It Works

### Thermal Model

Every update cycle, the model simulates the room temperature hour-by-hour from now until the target time. For each hour it:

1. Looks up the outdoor temperature, wind speed, wind bearing, humidity, UV index, and cloud coverage from the **hourly weather forecast** (matched within 90 minutes)
2. Computes heat exchange through walls: `thermal_transfer_coefficient × (outdoor − indoor)`
3. Adds `base_heat_gain_rate` for internal heat sources
4. Adds solar gain for daytime hours using a linear ramp:
   - Ramp **up** from 8 AM (0%) to 1 PM (100%)
   - Ramp **down** from 1 PM (100%) to 7 PM (0%)
   - Scaled by `solar_gain_factor × uv_factor × cloud_factor` where `uv_factor = uv_index / 10` and `cloud_factor = (100 − cloud_coverage) / 100`
5. Adds thermal lag heat gain — walls and the attic absorb solar energy during the afternoon and re-radiate it in the evening. The model captures this with an exponential decay from peak (1 PM):

   $$Q_{\text{lag}} = \text{afternoon\_solar\_load} \times \text{thermal\_lag\_factor} \times Q_{\text{base}} \times e^{-t_{\text{lag}}/4}$$

   where $t_{\text{lag}}$ is hours since 2 PM (the thermal peak for dense materials) and the time constant is 4 hours. `afternoon_solar_load` is the day's peak cloud-adjusted UV fraction — tracked by the coordinator as a running maximum in memory from 9 AM to 5 PM, so the lag term remains active in evening predictions even after those hours have dropped off the forecast.
6. Subtracts cooling from the active strategy:
   - **AC**: fixed rate based on outdoor temp (unaffected by humidity)
   - **Fan**: scales with temp differential and per-hour wind speed; reduced by high outdoor humidity
   - **Natural ventilation**: scales with temp differential and wind; same humidity reduction as fan

The humidity penalty for fan/window: $\text{factor} = \max(0.5,\ 1 - (RH - 40) \times 0.005)$, so 60% RH → 10% reduction, 90% RH → 25% reduction.

**Wind alignment factor** (fan and natural ventilation only): when *Window facing directions* are configured, the effective wind speed is multiplied by $\max(0,\ \cos(\theta))$, where $\theta$ is the smallest angular difference between the hourly forecast's `wind_bearing` and any configured window direction. The largest alignment across all configured windows is used:

| Wind vs. window | $\theta$ | Factor |
|---|---|---|
| Head-on | 0° | 1.00 |
| 45° off | 45° | 0.71 |
| Perpendicular | 90° | 0.00 |
| Tailwind | 180° | 0.00 |

With no window facing configured (or when `wind_bearing` is absent from the forecast), the factor defaults to **1.0** — no adjustment.

The simulation produces a predicted indoor temperature at the target time and an `hours_to_cool` estimate for each strategy evaluated.

### Strategy Selection

The engine evaluates three methods in energy-efficiency order and picks the first one that can reach the target within `target_time + tolerance`:

1. **Natural ventilation** (open window) — evaluated if AQI ≤ 150
2. **Fan** — evaluated if AQI ≤ 150 **and** *Fan available* is enabled for this room
3. **AC** — evaluated if *AC available* is enabled for this room; used as fallback otherwise

If no method can reach the target and AC is not available, the recommendation warns about the predicted temperature at the deadline rather than suggesting AC.

If no method can reach the target and AC is available, it is selected but the recommendation is labeled *"LATE — target may not be reached"*.

The recommendation text reflects what is already running and how much time remains:

| State | Example recommendation |
|---|---|
| Window recommended, window closed, time to spare | *Open window by 9:45 PM* |
| Window recommended, window closed, must act now | *Open window NOW!* |
| Window recommended, window open | *Keep window open by 10:00 PM* |
| Fan recommended, fan off, time to spare | *Start fan by 9:30 PM* |
| Fan recommended, fan off, must act now | *Start fan NOW!* |
| Fan recommended, fan running | *Continue fan by 9:50 PM* |
| AC recommended, AC off, time to spare | *Start AC by 10:10 PM* |
| AC recommended, AC running | *Continue AC* |
| Cannot reach target in time | *Start AC LATE — target may not be reached* |
| Window only, target not reachable | *Open window — target temperature may not be reachable* |
| Window only, hot room (78–84°F predicted) | Reasoning includes *“consider moving to a cooler part of the home”* |
| Window only, very hot room (≥85°F predicted) | Reasoning includes *“this may be unsafe — consider moving to a cooler area”* |
| Already at/near target | *No action needed* |
| Window open, outside warmer than inside | *Close window* |
| Window open, AQI too high | *Close window* |
| Window open, room over-cooling | *Close window* |

### Lazy-start Timing

Once a cooling method is chosen, the engine calculates the latest time you need to start it and still reach the target on time (optionally within tolerance). Instead of triggering immediately, the recommendation shows:

- **`by HH:MM AM/PM`** — you have buffer; start no later than this time
- **`NOW!`** — ≤ 15 minutes of buffer remaining, act immediately
- **`LATE — target may not be reached`** — even starting immediately won't hit the target (AC available)
- **`— target temperature may not be reachable`** — window cooling is insufficient and no AC is configured for this room

This means a fan or AC that only needs 90 minutes to cool the room won't be triggered at 4 PM for a 10:30 PM bedtime. The `action_needed_by` sensor always shows the computed deadline as an absolute timestamp.

### Close Window

If a window is open but outdoor conditions have changed to make it counterproductive, `Close window` is recommended immediately with a plain-English reason. Three triggers:

| Trigger | Condition | Example reasoning |
|---|---|---|
| Outside warmer than inside | `outdoor_temp ≥ indoor_temp` | *"Outside (74°F) is at or warmer than inside (72°F) — the window is adding heat, not removing it"* |
| AQI too high | `AQI > 150` | *"AQI is 162 — switch to fan or AC for cooling"* |
| Over-cooling risk | Room at target, outside ≥ 5°F below target | *"Room is already at target (68°F) and outside is 58°F — 6°F colder than inside. Room is predicted to reach 62°F with window open. Wind is calm (0.8 mph) — cool-down is mainly through wall conduction, not the window itself."* |

When closing a window due to over-cooling, the reasoning now includes:
- The predicted temperature at the deadline with the window left open
- A wind context note: calm (< 2 mph), light (2–5 mph), or strong (> 5 mph) — so you can judge whether it is ongoing air exchange or just cold walls driving the prediction

The close-window check runs before strategy selection, so it takes priority over any cooling recommendation.

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

### Learning System

Beyond confidence scoring, the integration tunes its physics parameters from nightly prediction outcomes. Learning is **segmented by what was running at prediction time**, so errors are attributed to the right parameter:

| Conditions at prediction time | Parameter adjusted |
|---|---|
| No AC, fan, or window open | `base_heat_gain_rate` — room warmer than predicted → raise |
| Window open, no fan, no AC | `natural_cooling_effectiveness` — room warmer → lower (window less effective than modeled) |
| Fan running (with or without window) | `fan_cooling_effectiveness` — room warmer → lower |
| AC running, outdoor < 82°F | `ac_cooling_rate_mild` — room warmer → lower |
| AC running, outdoor ≥ 82°F | `ac_cooling_rate_hot` — room warmer → lower |

A segment must accumulate at least 3–5 validated outcomes before any adjustment is made, and only if the mean error exceeds 0.5°F. The learning rate is conservative (0.1) so parameters drift gradually rather than overreacting to a single unusual night.

**Learning vs. Calibration:** Learning adjusts parameters incrementally from each night's outcome. Calibration (`smart_cooling.calibrate`) runs a full OLS regression against weeks of recorder history and is the faster way to get accurate initial values — especially for `thermal_transfer_coefficient`, which requires a wide range of outdoor temperatures to estimate reliably from nightly outcomes alone.

**Installed during hot weather?** Most people install this integration when it first gets hot — which means the AC runs frequently and nearly all nightly predictions are recorded while AC is on. In that scenario, only `ac_cooling_rate_mild` and `ac_cooling_rate_hot` accumulate enough records to tune. The passive, window, and fan segments stay at factory defaults until enough cool-weather nights pass where AC was not running at prediction time.

This matters for spring and autumn: if your first cool snap arrives and the system recommends opening windows, the `natural_cooling_effectiveness` and `fan_cooling_effectiveness` values it uses will be the defaults — not tuned to your room. The recommendation will still be directionally correct (the forecast-based simulation will determine *whether* to open a window), but the predicted temperature at the deadline may be less accurate.

**Recommended approach for new installs:**
1. Run for a few weeks including a mix of AC-on and AC-off evenings
2. Then run `smart_cooling.calibrate` — it processes all recorder history (not just nightly prediction events) and can extract passive and fan cooling rates from any period the sensors show AC was off, regardless of what mode was active at prediction time
3. Re-run calibrate at the start of each season (spring, autumn) when the dominant cooling mode shifts

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
| `natural_cooling_effectiveness` | 0.15 | coefficient | The passive airflow bonus from opening windows, on top of wall conduction. Near-zero at calm wind (< 1 mph); meaningful at 5+ mph. Tunable by the learning system from window-open nights. |
| `fan_cooling_effectiveness` | 0.30 | coefficient | Fan ventilation coefficient. At 10°F indoor-outdoor differential this delivers ~2–3°F/hr of additional cooling. Tunable by the learning system from fan-on nights. |
| `fan_equivalent_wind_speed` | 8.0 | mph | Effective wind speed assumed for fan on calm nights |
| `fan_boost_factor` | 1.4 | multiplier | Extra boost applied when both a fan is running and meaningful outdoor wind is present (fan + wind > fan alone) |
| `thermal_lag_factor` | 0.5 | multiplier | Controls how much afternoon solar heat stored in walls and the attic re-radiates in the evening. 0 = no lag effect; 1 = strong lag. Tunable by the learning system. |

> **Note:** Default values only apply to new rooms. If a room was set up before a default was changed, update parameters via `smart_cooling.set_params` or `smart_cooling.calibrate`.

> **AC setpoint clamping:** When an *AC thermostat setpoint entity* is configured, every step of the AC simulation clamps the predicted temperature at that setpoint — the AC cycles off at its own setpoint in reality, so the simulation matches. Configure this if predictions show the AC cooling the room well past where it actually stops.

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
  fan_cooling_effectiveness: 0.30
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

### Room has no fan or no AC
Turn off **Fan available** or **AC available** in the room's options (Step 3 or **Configure** in the integration UI). When *Fan available* is off, the strategy skips directly from window → AC. When *AC available* is off, the strategy can only recommend opening windows; if that won’t reach the target temperature, the reasoning warns about the predicted indoor temperature at the deadline instead of recommending AC.

These flags are independent — you can have a room with windows and AC but no fan, or windows only with no fan or AC.

### AC takes too long or too short
Adjust `ac_cooling_rate_mild` and `ac_cooling_rate_hot` to match observed performance.

### Comfort window pre-cool target seems wrong
The required pre-cool temperature is `target_temp − min(passive_overshoot, 8.0°F)` where `passive_overshoot` is how far the room would drift above target during the comfort window with no cooling. If evening heat is underestimated, the drift may be too small and the model won't recommend pre-cooling aggressively enough. Check that `thermal_lag_factor` is not zero and that `base_heat_gain_rate` is well-calibrated (run `smart_cooling.calibrate` with recent passive nights).

### Close window triggers too often or not enough
The over-cooling close-window trigger fires when the room is at target and outside is ≥ 5°F below target. This threshold is not currently configurable; if your room cools aggressively on cold nights, ensure your `base_heat_gain_rate` and `thermal_transfer_coefficient` are tuned accurately so the model predicts this correctly.

### Natural ventilation seems underrated or overrated
`natural_cooling_effectiveness` (default 0.15) represents the airflow bonus from an open window on top of passive wall conduction. Its effect scales with wind speed — at calm conditions (< 2 mph) it contributes almost nothing; at 5–10 mph it adds ~4–7°F of extra overnight cooling. Raise it (try 0.3–0.4) if the window feels more effective than predicted on breezy nights; lower it if the room stays warmer than predicted. The learning system will also tune this from window-open nights automatically.

### Fan/window cooling weaker on humid nights
This is expected and automatic — the model reduces fan and natural ventilation effectiveness when outdoor humidity is high (see humidity penalty formula in the Thermal Model section). Your weather entity must provide `humidity` in its hourly forecast for this to work; otherwise it defaults to 50% RH.

### Fan/window cooling lower than expected when wind is high
If the forecast shows good wind speed but cooling is weaker than expected, check whether *Window facing directions* are configured. When set, the wind contribution is scaled by how well each forecast hour's `wind_bearing` aligns with the selected directions (see alignment factor table in the Thermal Model section). A southerly wind against an east-facing window gets close to zero credit. Either add or remove window directions in the room's options, or leave the field empty to disable the alignment penalty entirely.

### Learning adjustments seem too slow
The continuous learning makes small conservative adjustments (learning rate 0.1) once a segment has ≥ 3–5 validated outcomes with mean error > 0.5°F. For faster initial tuning, run `smart_cooling.calibrate` — it processes weeks of recorder history in one pass.

### Confidence stuck at 50%
Normal for the first week or two. The model needs at least 10 nights where the target time passes and the actual temperature is recorded.

---

## Troubleshooting

### `forecast_entries` is 0
The weather entity is not returning hourly forecast data. Verify it supports `weather.get_forecasts` with `type: hourly`.

### All hourly predictions show the same `outdoor_temp`
The forecast lookup is failing. Check that your weather entity's forecast datetimes are in UTC (ISO 8601 with `+00:00`).

### Wind bearing not influencing fan/window cooling
`wind_bearing` must be present in your weather entity's hourly forecast items (most Met.no, Open-Meteo, and Yr-based integrations include it — confirm by checking the `forecast_sample` attribute on `predicted_target_temp`). If the attribute is missing the alignment factor defaults to 1.0 and wind direction has no effect. Also confirm *Window facing directions* are configured in the room's options — the bearing is only applied when at least one direction is selected.

### `action_needed_by` shows a time when no action is needed
Update to the latest version — fixed so `action_needed_by` returns `Unknown` when strategy is `no_action`.

### `will_reach_target_at` ticks to "now" every minute
Update to the latest version — fixed so it returns `Unknown` when the room is already at or below target.

---

## Comfort Window

The comfort window covers the period from bedtime to wake time — the hours when the room must *stay* comfortable, not just reach the target once.

Configure it by setting a **Comfort window end entity** (`input_datetime`, e.g. wake time) in Step 4. Once set:

1. After the target time (bedtime) is reached, the integration enters **comfort-window phase**
2. It simulates how much the room will drift upward from target overnight with no active cooling
3. It computes a **required pre-cool temperature** — typically 1–4°F below target — so that even as the room slowly re-heats, it stays within `comfort_tolerance` (default 2°F) until the comfort end time
4. The strategy recommendation accounts for this lower pre-cool target, and may recommend starting AC or fan earlier than it otherwise would

**Example:** Target temp 68°F, comfort tolerance 2°F (so 70°F is the ceiling), wake time 6:30 AM. If the model predicts the room drifts 3°F overnight without cooling, the required pre-cool target becomes 67°F and the strategy is chosen to reach that cooler temperature by bedtime.

**`prefer_ac_during_comfort`** (default on): When the overhead of maintaining overnight comfort is calculated, AC is preferred over passive if this flag is on. Turn it off for rooms where noise is a priority (a cool-but-quiet preference).

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
sensor.py               — 10 HA sensor entities per room
config_flow.py          — 4-step setup wizard + options flow
__init__.py             — Integration setup, service registration
const.py                — Default physics params, domain constants
```

---

## License

MIT
