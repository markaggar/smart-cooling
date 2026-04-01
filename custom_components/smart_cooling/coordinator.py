"""Data update coordinator for Smart Cooling."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
        """Extract current wind speed from hourly forecast.
        
        Wind speed is included as an attribute in each forecast array item.
        Returns the wind_speed from the first (current hour) forecast item.
        """
        if not forecast:
            return 0.0
        
        # Get wind_speed from the first forecast item (current/next hour)
        first_forecast = forecast[0] if forecast else {}
        wind_speed = first_forecast.get("wind_speed", 0)
        
        try:
            return float(wind_speed)
        except (ValueError, TypeError):
            return 0.0

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from sensors and compute recommendations."""
        try:
            # Get hourly forecast (includes wind_speed per forecast item)
            forecast = await self._get_hourly_forecast()
            current_wind_speed = self._get_current_wind_speed(forecast)
            
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
                "current_time": datetime.now(),
                "forecast": forecast,
            }
            
            # Calculate hours until target time
            hours_to_target = self._hours_to_target_time(current_conditions["target_time"])
            
            # Run thermal model prediction
            prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
            )
            
            # Get strategy recommendation
            strategy = self.strategy_engine.recommend(
                current_conditions=current_conditions,
                prediction=prediction,
            )
            
            # Record for learning (actual outcome will be recorded later)
            self.learning_module.record_prediction(
                timestamp=datetime.now(),
                conditions=current_conditions,
                prediction=prediction,
            )
            
            return {
                "room_name": self.room_name,
                "current_conditions": current_conditions,
                "prediction": prediction,
                "strategy": strategy,
                "learned_params": self.thermal_model.params,
                "prediction_confidence": self.learning_module.get_confidence(),
                "hours_to_target": hours_to_target,
            }
            
        except Exception as err:
            raise UpdateFailed(f"Error updating Smart Cooling data for {self.room_name}: {err}") from err

    def _hours_to_target_time(self, target_time_str: str) -> float:
        """Calculate hours until target time."""
        try:
            target_time = datetime.strptime(target_time_str, "%H:%M:%S").time()
            now = datetime.now()
            target_today = now.replace(
                hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
            )
            if target_today < now:
                target_today += timedelta(days=1)
            return (target_today - now).total_seconds() / 3600
        except ValueError:
            return 8.0  # Default 8 hours

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
