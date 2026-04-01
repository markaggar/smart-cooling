# Copilot Instructions: Smart Cooling Integration

Purpose: Enable AI agents to quickly and safely enhance the Home Assistant custom integration `smart_cooling`.

## Architecture Overview
- Domain: `custom_components/smart_cooling/` implements a physics-based thermal model with strategy recommendations and self-learning capabilities.
- Core modules:
  - `thermal_model.py`: Physics calculations (heat gain, cooling rates, temperature prediction).
  - `strategy_engine.py`: Recommends optimal cooling method (window, fan, AC) based on predictions.
  - `learning_module.py`: Records predictions vs actuals, computes gradient-based parameter adjustments.
  - `coordinator.py`: HA DataUpdateCoordinator that orchestrates model updates and sensor refresh.
  - `historical_replay.py`: Testing infrastructure for validating model against historical data.
- Platforms:
  - `sensor.py`: Exposes prediction sensors (recommended strategy, predicted temp, confidence, time to comfort).
- Config & Options flows: `config_flow.py` drives multi-step forms for sensor selection, target temps, bedtime, and optional learning.
- Constants: `const.py` centralizes physics defaults, config keys, and domain constants.

## Key Patterns & Conventions
- **Unique IDs**: Prefixed with `entry.entry_id` plus semantic suffix (e.g., `_predicted_temp`, `_cooling_strategy`). Follow this pattern for multi-instance safety.
- **Coordinator pattern**: All sensor entities subscribe to `SmartCoolingCoordinator`. Updates flow through `async_update_data()` which refreshes the thermal model and strategy.
- **Physics parameters**: Stored in `const.py` defaults but overridable via learning module. Key params:
  - `base_heat_gain_rate`, `solar_gain_factor`: Heat accumulation
  - `fan_cooling_coefficient`, `ac_cooling_rate_mild`, `ac_cooling_rate_hot`: Cooling effectiveness
- **Async rules**: All I/O and HA interactions are `async`. Avoid blocking calls—use `hass.async_add_executor_job()` for pandas operations in historical replay.
- **Dataclasses**: Use `TemperaturePrediction` and `CoolingStrategy` dataclasses for structured return values with `to_dict()` methods.
- **Learning persistence**: Learning data stored to `.storage/smart_cooling/` via HA's storage helper. Never write directly to files.

## Data Flow
1. Coordinator polls weather entities (outdoor temp) and indoor sensors at configured interval.
2. `ThermalModel.predict_temperature()` calculates expected bedtime temp under various strategies.
3. `StrategyEngine.recommend()` selects optimal strategy based on predictions, AQI, and constraints.
4. Sensor entities update from coordinator data via `_handle_coordinator_update()`.
5. (Optional) `LearningModule` records predictions for later comparison with actual temps.

## Adding Features Safely
1. Identify if new behavior belongs in physics model, strategy engine, or as a new sensor entity.
2. Add constants in `const.py` (config keys, physics defaults) before using elsewhere.
3. Extend `config_flow.py` for any new user-configurable options (both ConfigFlow and OptionsFlow).
4. Physics changes should be testable—add unit tests in `tests/test_thermal_model.py`.
5. Strategy changes need validation in `tests/test_strategy_engine.py`.
6. Maintain backward compatibility: don't change physics parameter names without migration.

## Testing & Dev Workflow
- **Unit tests**: Run `pytest tests/ -v` from repo root with venv active.
- **Historical replay**: Place Excel/CSV in `data/`, run `python scripts/test_with_historical_data.py data/file.xlsx`.
- **CI/CD**: GitHub Actions runs lint + tests on push. Deploy to HA Dev via rsync (requires secrets: `HA_DEV_HOST`, `HA_DEV_TOKEN`, `HA_DEV_SSH_KEY`).
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
