"""Data update coordinator for Smart Cooling."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    GLOBAL_CONFIG_KEY,
    UPDATE_INTERVAL_SECONDS,
    # Global config keys
    CONF_WEATHER_ENTITY,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_AQI_SENSOR,
    # Room config keys
    CONF_ROOM_NAME,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_INDOOR_HUMIDITY_SENSOR,
    CONF_WINDOW_SENSOR,
    CONF_FAN_SENSOR,
    CONF_AC_SENSOR,
    CONF_TARGET_TEMP_ENTITY,
    CONF_TARGET_TIME_ENTITY,
    CONF_BEDTIME_ENTITY,  # Legacy support
    CONF_TOLERANCE_MINUTES,
    DEFAULT_TOLERANCE_MINUTES,
)
from .thermal_model import ThermalModel
from .strategy_engine import StrategyEngine
from .learning_module import LearningModule

_LOGGER = logging.getLogger(__name__)


class SmartCoolingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Smart Cooling data updates.
    
    Each room has its own coordinator instance with its own physics simulation.
    Global config (weather, outdoor temp, AQI) is shared from hass.data.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.room_name = entry.data.get(CONF_ROOM_NAME, "Room")
        
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.room_name}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        
        # Build config from entry data, options, and global config
        self.config = self._build_config()
        
        # Initialize components (each room has its own physics simulation)
        self.thermal_model = ThermalModel(self.config)
        self.strategy_engine = StrategyEngine(self.thermal_model)
        self.learning_module = LearningModule(hass, entry.entry_id)
        
        # Apply any learned parameters for this room
        learned_params = self.learning_module.get_learned_params()
        if learned_params:
            self.thermal_model.update_params(learned_params)

    def _build_config(self) -> dict[str, Any]:
        """Build configuration merging entry data, options, and global config."""
        # Start with entry data and options
        config = {**self.entry.data, **self.entry.options}
        
        # Get global config from hass.data if available (shared sensors)
        if DOMAIN in self.hass.data and GLOBAL_CONFIG_KEY in self.hass.data[DOMAIN]:
            global_config = self.hass.data[DOMAIN][GLOBAL_CONFIG_KEY]
            # Global config takes precedence for shared sensors
            for key in [CONF_WEATHER_ENTITY, CONF_OUTDOOR_TEMP_SENSOR, CONF_AQI_SENSOR]:
                if global_config.get(key):
                    config[key] = global_config[key]
        
        return config

    def _get_sensor_value(self, entity_id: str | None, default: float = 0.0) -> float:
        """Get numeric value from a sensor entity."""
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _get_binary_state(self, entity_id: str | None) -> bool | None:
        """Get boolean value from a binary_sensor entity."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state.state == "on"

    def _get_time_value(self, entity_id: str | None, default: str = "22:30:00") -> str:
        """Get time value from an entity."""
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    async def _get_hourly_forecast(self) -> list[dict[str, Any]]:
        """Get hourly weather forecast from weather entity.
        
        Uses weather.get_forecasts service which returns hourly forecast array.
        Each forecast item includes:
          - datetime: forecast time
          - temperature: predicted outdoor temp
          - wind_speed: wind speed in mph/km/h (used for fan/window cooling)
          - humidity: outdoor humidity
          - condition: weather condition string
        """
        weather_entity = self.config.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            return []
        
        try:
            # Use the weather.get_forecasts service for hourly forecast
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            
            if response and weather_entity in response:
                forecast_data = response[weather_entity]
                if isinstance(forecast_data, dict):
                    return forecast_data.get("forecast", [])
                return forecast_data if isinstance(forecast_data, list) else []
        except Exception as err:
            _LOGGER.debug("Error fetching hourly forecast: %s", err)
            
            # Fallback to state attributes (legacy method)
            state = self.hass.states.get(weather_entity)
            if state is not None:
                forecast = state.attributes.get("forecast", [])
                return forecast if isinstance(forecast, list) else []
        
        return []

    def _get_current_wind_speed(self, forecast: list[dict[str, Any]]) -> float:
        """Extract current wind speed from the first hourly forecast item."""
        if not forecast:
            return 0.0
        first_forecast = forecast[0] if forecast else {}
        try:
            return float(first_forecast.get("wind_speed", 0))
        except (ValueError, TypeError):
            return 0.0

    def _get_current_outdoor_humidity(self, forecast: list[dict[str, Any]]) -> float:
        """Extract current outdoor humidity from the first hourly forecast item."""
        if not forecast:
            return 50.0
        first_forecast = forecast[0] if forecast else {}
        try:
            return float(first_forecast.get("humidity", 50.0))
        except (ValueError, TypeError):
            return 50.0

    def _sensor_ready(self, entity_id: str | None) -> bool:
        """Return True if the entity exists and has a real (non-startup) value."""
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state not in ("unknown", "unavailable")

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from sensors and compute recommendations."""
        try:
            # Guard against startup race: if critical sensors aren't ready yet,
            # return the last good dataset (sensors keep their previous state) or
            # raise UpdateFailed on the very first run so sensors show "unavailable"
            # rather than computing garbage from hardcoded defaults.
            indoor_sensor = self.config.get(CONF_INDOOR_TEMP_SENSOR)
            outdoor_sensor = self.config.get(CONF_OUTDOOR_TEMP_SENSOR)
            target_sensor = (
                self.config.get(CONF_TARGET_TEMP_ENTITY)
                or self.config.get(CONF_BEDTIME_ENTITY)
            )
            critical_ready = (
                self._sensor_ready(indoor_sensor)
                and self._sensor_ready(outdoor_sensor)
                and self._sensor_ready(target_sensor)
            )
            if not critical_ready:
                if self.data:
                    _LOGGER.debug(
                        "%s: sensors not yet ready, keeping last good data",
                        self.room_name,
                    )
                    return self.data
                raise UpdateFailed(
                    f"{self.room_name}: waiting for sensors to become available "
                    f"({indoor_sensor}, {outdoor_sensor}, {target_sensor})"
                )

            # Get hourly forecast (includes wind_speed per forecast item)
            forecast = await self._get_hourly_forecast()
            current_wind_speed = self._get_current_wind_speed(forecast)
            current_outdoor_humidity = self._get_current_outdoor_humidity(forecast)

            # Gather current conditions for this room
            current_conditions = {
                "room_name": self.room_name,
                "indoor_temp": self._get_sensor_value(
                    self.config.get(CONF_INDOOR_TEMP_SENSOR), 72.0
                ),
                "indoor_humidity": self._get_sensor_value(
                    self.config.get(CONF_INDOOR_HUMIDITY_SENSOR)
                ),
                "outdoor_temp": self._get_sensor_value(
                    self.config.get(CONF_OUTDOOR_TEMP_SENSOR), 70.0
                ),
                "outdoor_humidity": current_outdoor_humidity,
                "aqi": self._get_sensor_value(
                    self.config.get(CONF_AQI_SENSOR), 50.0
                ),
                "wind_speed": current_wind_speed,
                "target_temp": self._get_sensor_value(
                    self.config.get(CONF_TARGET_TEMP_ENTITY), 72.0
                ),
                # Support both new target_time and legacy bedtime
                "target_time": self._get_time_value(
                    self.config.get(CONF_TARGET_TIME_ENTITY) or 
                    self.config.get(CONF_BEDTIME_ENTITY),
                    "22:30:00"
                ),
                "window_open": self._get_binary_state(
                    self.config.get(CONF_WINDOW_SENSOR)
                ),
                "fan_running": self._get_binary_state(
                    self.config.get(CONF_FAN_SENSOR)
                ),
                "ac_running": self._get_binary_state(
                    self.config.get(CONF_AC_SENSOR)
                ),
                "current_time": dt_util.now(),
                "forecast": forecast,
            }
            
            # Calculate hours until target time
            hours_to_target = self._hours_to_target_time(current_conditions["target_time"])
            
            # Run thermal model prediction
            prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
            )
            
            # Get strategy recommendation (tolerance-aware)
            tolerance_minutes = int(
                self.config.get(CONF_TOLERANCE_MINUTES, DEFAULT_TOLERANCE_MINUTES)
            )
            strategy = self.strategy_engine.recommend(
                current_conditions=current_conditions,
                prediction=prediction,
                tolerance_minutes=tolerance_minutes,
)

            # Estimate time to reach target with the recommended cooling method
            cooling_method = strategy.method.value  # e.g. "start_fan", "start_ac", etc.
            if "fan" in cooling_method:
                active_strategy = "fan"
            elif "ac" in cooling_method:
                active_strategy = "ac"
            else:
                active_strategy = "natural"

            hours_until_cool = self.thermal_model.find_hours_to_cool_to_target(
                current_conditions=current_conditions,
                cooling_strategy=active_strategy,
            )

            now = dt_util.now()

            # Datetime when room will reach target.
            # hours_until_cool == 0.0 means already at/below target — treat as None
            # so the sensor stays stable instead of ticking to "now" every minute.
            if hours_until_cool is not None and hours_until_cool > 0.0:
                will_reach_target_at = now + timedelta(hours=hours_until_cool)
            else:
                will_reach_target_at = None

            # Latest time to START cooling so target is reached by target_time + tolerance.
            # None when no action is needed (room already at/below target and not
            # predicted to rise above it).
            tolerance_hours = tolerance_minutes / 60.0
            target_datetime = now + timedelta(hours=hours_to_target)
            no_action = strategy.method.value == "no_action"
            if no_action or (hours_until_cool is not None and hours_until_cool <= 0.0):
                action_needed_by = None
            elif hours_until_cool is not None:
                # You can delay starting by (budget - time_it_takes)
                delay_budget = hours_to_target + tolerance_hours - hours_until_cool
                action_needed_by = now + timedelta(hours=max(0.0, delay_budget))
            else:
                # Can't reach target — action needed immediately
                action_needed_by = now
            
            # Check if any earlier predictions' target time has now passed and
            # record the actual indoor temp so the learning module can score them.
            await self.learning_module.try_complete_predictions(
                current_time=now,
                current_indoor_temp=current_conditions["indoor_temp"],
            )

            # Record this prediction for future comparison (deduplicated per target_datetime)
            self.learning_module.record_prediction(
                timestamp=now,
                conditions=current_conditions,
                prediction=prediction,
                target_datetime=target_datetime,
            )
            
            return {
                "room_name": self.room_name,
                "current_conditions": current_conditions,
                "prediction": prediction,
                "strategy": strategy,
                "learned_params": self.thermal_model.params,
                "prediction_confidence": self.learning_module.get_confidence(),
                "hours_to_target": hours_to_target,
                "hours_until_cool": hours_until_cool,
                "will_reach_target_at": will_reach_target_at,
                "action_needed_by": action_needed_by,
                # Forecast diagnostics — visible in sensor attributes
                "forecast_entries": len(forecast),
                "forecast_sample": [
                    {"datetime": str(f.get("datetime", "")), "temperature": f.get("temperature")}
                    for f in forecast[:4]
                ],
            }
            
        except Exception as err:
            raise UpdateFailed(f"Error updating Smart Cooling data for {self.room_name}: {err}") from err

    def _hours_to_target_time(self, target_time_str: str) -> float:
        """Calculate hours until target time."""
        try:
            target_time = datetime.strptime(target_time_str, "%H:%M:%S").time()
            now = dt_util.now()
            target_today = now.replace(
                hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
            )
            if target_today < now:
                target_today += timedelta(days=1)
            return (target_today - now).total_seconds() / 3600
        except ValueError:
            return 8.0  # Default 8 hours

    async def async_calibrate_from_history(self, days: int = 30) -> dict[str, Any]:
        """Pull history from HA recorder and tune physics parameters.

        Uses the configured indoor/outdoor temp sensors and AC/fan state sensors
        to replay real observed temperature changes against the thermal model,
        then adjusts heat-gain and cooling-rate parameters to minimise error.

        Returns a summary dict with before/after params and error stats.
        """
        from datetime import timezone
        from homeassistant.components import recorder
        from homeassistant.components.recorder.history import get_significant_states

        indoor_entity = self.config.get(CONF_INDOOR_TEMP_SENSOR)
        outdoor_entity = self.config.get(CONF_OUTDOOR_TEMP_SENSOR)
        ac_entity = self.config.get(CONF_AC_SENSOR)
        fan_entity = self.config.get(CONF_FAN_SENSOR)

        if not indoor_entity or not outdoor_entity:
            return {"error": "indoor_temp_sensor and outdoor_temp_sensor must be configured"}

        end_time = dt_util.now()
        start_time = end_time - timedelta(days=days)

        entity_ids = [e for e in [indoor_entity, outdoor_entity, ac_entity, fan_entity] if e]

        # Fetch history from recorder (runs in executor thread)
        rec_instance = recorder.get_instance(self.hass)
        history = await rec_instance.async_add_executor_job(
            get_significant_states,
            self.hass,
            start_time,
            end_time,
            entity_ids,
        )

        # Build timeline of merged samples (one per ~15 min bucket)
        samples: dict[datetime, dict[str, Any]] = {}

        for entity_id, states in history.items():
            for state in states:
                if state.state in ("unknown", "unavailable"):
                    continue
                # Bucket to nearest 15 min
                ts = state.last_changed.replace(second=0, microsecond=0)
                bucket = ts.replace(minute=(ts.minute // 15) * 15, tzinfo=None)
                if bucket not in samples:
                    samples[bucket] = {}
                try:
                    if entity_id == indoor_entity:
                        samples[bucket]["indoor_temp"] = float(state.state)
                    elif entity_id == outdoor_entity:
                        samples[bucket]["outdoor_temp"] = float(state.state)
                    elif entity_id == ac_entity:
                        samples[bucket]["ac_running"] = state.state == "on"
                    elif entity_id == fan_entity:
                        samples[bucket]["fan_running"] = state.state == "on"
                except (ValueError, TypeError):
                    continue

        # Sort and forward-fill missing sensors
        sorted_times = sorted(samples)
        last = {"indoor_temp": None, "outdoor_temp": None, "ac_running": False, "fan_running": False}
        timeline = []
        for ts in sorted_times:
            last.update(samples[ts])
            if last["indoor_temp"] is not None and last["outdoor_temp"] is not None:
                timeline.append({"ts": ts, **last})

        if len(timeline) < 10:
            return {"error": f"Only {len(timeline)} usable samples — need at least 10"}

        fan_cool_obs: list[float] = []
        ac_cool_obs: list[float] = []

        # Passive samples: outdoor < indoor, no AC, no fan
        # observed_rate = base_heat_gain + (outdoor - indoor) * thermal_transfer_coeff
        # This is a linear regression: y = a + b*x
        #   y = observed rate
        #   x = (outdoor - indoor)   [negative values when outdoor is cooler]
        #   a = base_heat_gain_rate
        #   b = thermal_transfer_coefficient
        passive_obs: list[tuple[float, float]] = []  # (x, y)

        for i in range(1, len(timeline)):
            prev = timeline[i - 1]
            curr = timeline[i]
            dt_h = (curr["ts"] - prev["ts"]).total_seconds() / 3600
            if dt_h <= 0 or dt_h > 1:
                continue
            delta_temp = curr["indoor_temp"] - prev["indoor_temp"]
            rate = delta_temp / dt_h  # °F/hr

            if prev["ac_running"]:
                predicted_gain = self.thermal_model.calculate_heat_gain(
                    hour=prev["ts"].hour,
                    outdoor_temp=prev["outdoor_temp"],
                    indoor_temp=prev["indoor_temp"],
                )
                ac_cool_obs.append(max(0.0, predicted_gain - rate))
            elif prev["fan_running"]:
                predicted_gain = self.thermal_model.calculate_heat_gain(
                    hour=prev["ts"].hour,
                    outdoor_temp=prev["outdoor_temp"],
                    indoor_temp=prev["indoor_temp"],
                )
                fan_cool_obs.append(max(0.0, predicted_gain - rate))
            else:
                # All passive samples (outdoor may be hotter OR cooler)
                x = prev["outdoor_temp"] - prev["indoor_temp"]
                passive_obs.append((x, rate))

        updated_params: dict[str, float] = {}
        old_params = dict(self.thermal_model.params)

        # Least-squares regression on passive samples to estimate both
        # base_heat_gain_rate (intercept) and thermal_transfer_coefficient (slope).
        # Requires variance in (outdoor - indoor) to separate the two terms.
        if len(passive_obs) >= 10:
            n = len(passive_obs)
            sum_x = sum(p[0] for p in passive_obs)
            sum_y = sum(p[1] for p in passive_obs)
            sum_xx = sum(p[0] ** 2 for p in passive_obs)
            sum_xy = sum(p[0] * p[1] for p in passive_obs)
            denom = n * sum_xx - sum_x ** 2

            if abs(denom) > 1e-6:  # enough variance in x to solve
                slope = (n * sum_xy - sum_x * sum_y) / denom   # thermal_transfer_coeff
                intercept = (sum_y - slope * sum_x) / n         # base_heat_gain_rate

                # Sanity-clamp before accepting
                slope = max(0.01, min(0.5, slope))
                intercept = max(0.1, min(6.0, intercept))

                # Smooth 50/50 toward observed values
                updated_params["thermal_transfer_coefficient"] = round(
                    0.5 * self.thermal_model.params["thermal_transfer_coefficient"]
                    + 0.5 * slope, 4
                )
                updated_params["base_heat_gain_rate"] = round(
                    0.5 * self.thermal_model.params["base_heat_gain_rate"]
                    + 0.5 * intercept, 3
                )
                _LOGGER.info(
                    "Regression on %d passive samples: base_gain=%.3f, "
                    "thermal_transfer=%.4f",
                    n, intercept, slope,
                )

        if len(ac_cool_obs) >= 5:
            observed_ac = sum(ac_cool_obs) / len(ac_cool_obs)
            updated_params["ac_cooling_rate_mild"] = round(
                0.5 * self.thermal_model.params["ac_cooling_rate_mild"] + 0.5 * observed_ac, 3
            )

        if len(fan_cool_obs) >= 5:
            observed_fan_rate = sum(fan_cool_obs) / len(fan_cool_obs)
            # fan_cooling_effectiveness is per unit of temp_diff * wind_factor
            # Store as direct rate adjustment
            updated_params["fan_cooling_effectiveness"] = round(
                0.5 * self.thermal_model.params["fan_cooling_effectiveness"]
                + 0.5 * (observed_fan_rate / 10.0),  # Normalise by typical temp diff
                4,
            )

        if updated_params:
            self.thermal_model.update_params(updated_params)
            await self.learning_module.save_params(updated_params)
            _LOGGER.info(
                "Calibrated %s from %d days of history. Updates: %s",
                self.room_name, days, updated_params,
            )

        return {
            "samples_used": len(timeline),
            "passive_samples": len(passive_obs),
            "fan_cool_samples": len(fan_cool_obs),
            "ac_cool_samples": len(ac_cool_obs),
            "params_before": old_params,
            "params_after": dict(self.thermal_model.params),
            "updated": updated_params,
        }

    async def async_record_actual_outcome(
        self, timestamp: datetime, actual_temp: float
    ) -> None:
        """Record actual temperature for learning comparison."""
        await self.learning_module.record_actual(timestamp, actual_temp)
        
        # Trigger parameter update if enough data
        updated_params = await self.learning_module.compute_parameter_updates()
        if updated_params:
            self.thermal_model.update_params(updated_params)
            _LOGGER.info("Updated thermal model parameters from learning: %s", updated_params)

    async def async_calibrate_from_history(self, days: int = 30) -> dict[str, Any]:
        """Load history from recorder and tune the thermal model.

        Builds entity_roles from the room's configured sensors, queries the
        recorder for the last `days` days, runs the replay engine, and applies
        any suggested parameter adjustments.

        Returns a summary dict with metrics and any applied changes.
        """
        from .historical_replay import async_load_from_recorder, HistoricalReplayEngine

        entity_roles: dict[str, str] = {}

        indoor = self.config.get(CONF_INDOOR_TEMP_SENSOR)
        outdoor = self.config.get(CONF_OUTDOOR_TEMP_SENSOR)
        if not indoor or not outdoor:
            return {"error": "indoor_temp and outdoor_temp sensors are required"}

        entity_roles["indoor_temp"] = indoor
        entity_roles["outdoor_temp"] = outdoor

        for role, conf_key in (
            ("fan_running", CONF_FAN_SENSOR),
            ("ac_running", CONF_AC_SENSOR),
            ("window_open", CONF_WINDOW_SENSOR),
        ):
            entity_id = self.config.get(conf_key)
            if entity_id:
                entity_roles[role] = entity_id

        _LOGGER.info(
            "Calibrating %s from %d days of history (entities: %s)",
            self.room_name, days, list(entity_roles.values()),
        )

        try:
            data_points = await async_load_from_recorder(
                self.hass, entity_roles, days=days
            )
        except Exception as err:
            return {"error": f"Failed to load recorder history: {err}"}

        if len(data_points) < 10:
            return {
                "error": f"Too few data points ({len(data_points)}) — need at least 10",
                "points_loaded": len(data_points),
            }

        replay = HistoricalReplayEngine(self.thermal_model, self.strategy_engine)
        results = replay.replay_data(data_points)

        if not results:
            return {
                "error": "No replay results (not enough overlapping data)",
                "points_loaded": len(data_points),
            }

        metrics = replay.calculate_metrics(results)
        suggestions = replay.suggest_parameter_adjustments(results)

        if suggestions:
            self.thermal_model.update_params(suggestions)
            await self.learning_module.save_params(suggestions)
            _LOGGER.info("Calibration applied parameter updates for %s: %s", self.room_name, suggestions)

        return {
            "room": self.room_name,
            "points_loaded": len(data_points),
            "replay_results": len(results),
            "metrics": metrics,
            "parameter_adjustments": suggestions,
        }
