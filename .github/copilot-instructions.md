# Copilot Instructions: Smart Cooling Integration

Purpose: Enable AI agents to quickly and safely enhance the Home Assistant custom integration `smart_cooling`.

## Architecture Overview
- Domain: `custom_components/smart_cooling/` implements a physics-based thermal model with strategy recommendations and self-learning capabilities.
- **Multi-instance**: Supports multiple rooms, each with its own physics simulation and output sensors.
- **Shared global config**: Weather entity (hourly forecast), outdoor temp, and AQI are shared across all rooms.
- **Per-room config**: Indoor temp, humidity, window/fan/AC sensors, target temp/time helpers.

### Core Modules
- `thermal_model.py`: Physics calculations (heat gain, cooling rates, temperature prediction).
- `strategy_engine.py`: Recommends optimal cooling method (window, fan, AC) based on predictions.
- `learning_module.py`: Records predictions vs actuals, computes gradient-based parameter adjustments per room.
- `coordinator.py`: HA DataUpdateCoordinator that orchestrates model updates and sensor refresh per room.
- `historical_replay.py`: Testing infrastructure for validating model against historical data.

### Platforms
- `sensor.py`: Exposes per-room prediction sensors (recommended strategy, predicted temp, confidence, time to target).

### Configuration
- `config_flow.py`: Multi-step forms. First setup collects global sensors, subsequent setups reuse them.
- `const.py`: Centralizes physics defaults, config keys (global vs per-room), and domain constants.

## Key Patterns & Conventions
- **Multi-instance safety**: Unique IDs prefixed with `entry.entry_id` plus semantic suffix.
- **Global config storage**: `hass.data[DOMAIN][GLOBAL_CONFIG_KEY]` stores shared weather/outdoor/AQI config.
- **Coordinator pattern**: Each room has its own `SmartCoolingCoordinator` instance.
- **Hourly forecast**: Weather entity must support `weather.get_forecasts` service with `type: hourly`. Wind speed is extracted from the forecast array items (`forecast[i].wind_speed`).
- **Target helpers**: Uses `input_number` for target temp and `input_datetime` for target time (when to reach target).
- **Async rules**: All I/O and HA interactions are `async`. Use `hass.async_add_executor_job()` for blocking operations.
- **Dataclasses**: Use `TemperaturePrediction` and `CoolingStrategy` for structured return values.
- **Learning persistence**: Per-room learning data stored to `.storage/smart_cooling/{entry_id}/`.

## Config Keys (const.py)
### Global (shared across rooms)
- `CONF_WEATHER_ENTITY`: Weather entity with hourly forecast (wind_speed in forecast items)
- `CONF_OUTDOOR_TEMP_SENSOR`: Outdoor temperature sensor
- `CONF_AQI_SENSOR`: Air quality index sensor

### Per-Room
- `CONF_ROOM_NAME`: Human-readable room name for instance title
- `CONF_INDOOR_TEMP_SENSOR`: Room temperature sensor
- `CONF_INDOOR_HUMIDITY_SENSOR`: Room humidity sensor (optional)
- `CONF_WINDOW_SENSOR`, `CONF_FAN_SENSOR`, `CONF_AC_SENSOR`: Device state binary_sensors
- `CONF_TARGET_TEMP_ENTITY`: Target temperature helper (input_number)
- `CONF_TARGET_TIME_ENTITY`: Target time helper (input_datetime) - when to reach target

## Data Flow
1. Coordinator fetches hourly forecast via `weather.get_forecasts` service.
2. Wind speed extracted from forecast array: `forecast[0].wind_speed` for current.
3. Global sensors (outdoor temp, AQI) read from shared config.
4. Room sensors (indoor temp, device states) read per-instance.
5. `ThermalModel.predict_temperature()` calculates expected target temp under various strategies.
6. `StrategyEngine.recommend()` selects optimal strategy based on predictions, AQI, and constraints.
7. Sensor entities update from coordinator data.
8. `LearningModule` records predictions for later comparison with actual temps.

## Adding Features Safely
1. Determine if feature is global (affects all rooms) or per-room.
2. Add constants in `const.py` under appropriate section (global vs per-room).
3. Extend `config_flow.py`: global features in `async_step_global`, room features in room steps.
4. Physics changes should be testable—add unit tests in `tests/test_thermal_model.py`.
5. Strategy changes need validation in `tests/test_strategy_engine.py`.
6. Maintain backward compatibility: support legacy `CONF_BEDTIME_ENTITY` alongside new `CONF_TARGET_TIME_ENTITY`.

## Testing & Dev Workflow
- **Unit tests**: Run `pytest tests/ -v` from repo root with venv active.
- **Historical replay**: Place Excel/CSV in `data/`, run `python scripts/test_with_historical_data.py data/file.xlsx`.
- **CI/CD**: GitHub Actions runs lint + tests on push. Deploy to HA Dev via deploy script.
- **Deploy**: Run `.\scripts\deploy-smart-cooling.ps1` to copy files and restart HA.
- **HA mocking**: Tests mock `homeassistant` imports in `conftest.py` to run without HA installation.
- Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.smart_cooling: debug
  ```

## Physics Model Notes
- **Heat gain**: Depends on indoor/outdoor temp differential, time of day (solar), and calibrated base rate.
- **Fan cooling**: Effective only when outdoor < indoor by threshold. Scales with temp advantage and wind speed.
- **AC cooling**: Degrades at high outdoor temps (compressor efficiency). Use `ac_cooling_rate_hot` for temps > 90°F.
- **Predictions are probabilistic**: Model returns confidence based on forecast reliability and time horizon.

## Common Pitfalls
- **Blocking pandas in async**: Use `hass.async_add_executor_job()` for CSV/Excel loading.
- **Physics parameter drift**: Learning module can drift params if actuals are unreliable—add validation bounds.
- **AQI thresholds**: Window/fan strategies gate on AQI; forgetting this check leads to bad recommendations.
- **Time parsing**: Bedtime is a string `"HH:MM:SS"`. Always parse with `datetime.strptime()`, not manual split.
- **Forecast data**: May be empty or None—always provide defaults in `predict_temperature()`.

## Examples
- Predict temperature:
  ```python
  prediction = thermal_model.predict_temperature(
      current_conditions=conditions,
      hours_ahead=4.0,
      cooling_strategy="fan",
  )
  ```
- Get recommended strategy:
  ```python
  strategy = engine.recommend(conditions, prediction)
  # Returns CoolingStrategy with method, predicted_temp, confidence, reasoning
  ```
- Update physics parameters from learning:
  ```python
  adjustments = learning_module.compute_parameter_updates()
  thermal_model.update_params(adjustments)
  ```

## Style & Quality
- Keep functions short and side-effect explicit; physics calculations should be pure functions.
- Use defensive `try/except` in coordinator to avoid breaking update cycles.
- Type hints on all function signatures; `mypy` is run in CI.
- Dataclasses over dicts for structured data returns.

## When Unsure
- Search `const.py` for naming precedents and physics defaults.
- Mirror existing sensor patterns in `sensor.py` for new sensor types.
- Check `test_thermal_model.py` for expected behavior—tests document model assumptions.
- Ask for clarification if physics changes could affect learning module stability.

(End)
