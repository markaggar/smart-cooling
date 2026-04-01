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
    UPDATE_INTERVAL_SECONDS,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_TARGET_TEMP_ENTITY,
    CONF_BEDTIME_ENTITY,
)
from .thermal_model import ThermalModel
from .strategy_engine import StrategyEngine
from .learning_module import LearningModule

_LOGGER = logging.getLogger(__name__)


class SmartCoolingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Smart Cooling data updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        
        # Initialize components
        self.thermal_model = ThermalModel(self.config)
        self.strategy_engine = StrategyEngine(self.thermal_model)
        self.learning_module = LearningModule(hass, entry.entry_id)
        
        # Apply any learned parameters
        learned_params = self.learning_module.get_learned_params()
        if learned_params:
            self.thermal_model.update_params(learned_params)

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

    def _get_time_value(self, entity_id: str | None, default: str = "22:30:00") -> str:
        """Get time value from an entity."""
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    def _get_forecast(self) -> list[dict[str, Any]]:
        """Get weather forecast from weather entity."""
        weather_entity = self.config.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            return []
        
        state = self.hass.states.get(weather_entity)
        if state is None:
            return []
        
        forecast = state.attributes.get("forecast", [])
        return forecast if isinstance(forecast, list) else []

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from sensors and compute recommendations."""
        try:
            # Gather current conditions
            current_conditions = {
                "indoor_temp": self._get_sensor_value(
                    self.config.get(CONF_INDOOR_TEMP_SENSOR), 72.0
                ),
                "outdoor_temp": self._get_sensor_value(
                    self.config.get(CONF_OUTDOOR_TEMP_SENSOR), 70.0
                ),
                "target_temp": self._get_sensor_value(
                    self.config.get(CONF_TARGET_TEMP_ENTITY), 72.0
                ),
                "bedtime": self._get_time_value(
                    self.config.get(CONF_BEDTIME_ENTITY), "22:30:00"
                ),
                "current_time": datetime.now(),
                "forecast": self._get_forecast(),
            }
            
            # Run thermal model prediction
            prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=self._hours_to_bedtime(current_conditions["bedtime"]),
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
                "current_conditions": current_conditions,
                "prediction": prediction,
                "strategy": strategy,
                "learned_params": self.thermal_model.params,
                "prediction_confidence": self.learning_module.get_confidence(),
            }
            
        except Exception as err:
            raise UpdateFailed(f"Error updating Smart Cooling data: {err}") from err

    def _hours_to_bedtime(self, bedtime_str: str) -> float:
        """Calculate hours until bedtime."""
        try:
            bedtime = datetime.strptime(bedtime_str, "%H:%M:%S").time()
            now = datetime.now()
            bedtime_today = now.replace(
                hour=bedtime.hour, minute=bedtime.minute, second=0, microsecond=0
            )
            if bedtime_today < now:
                bedtime_today += timedelta(days=1)
            return (bedtime_today - now).total_seconds() / 3600
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
