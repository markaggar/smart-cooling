"""Sensor entities for Smart Cooling."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SmartCoolingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Cooling sensors from a config entry."""
    coordinator: SmartCoolingCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        SmartCoolingRecommendationSensor(coordinator, entry),
        SmartCoolingPredictedTempSensor(coordinator, entry),
        SmartCoolingPredictedWithActionSensor(coordinator, entry),
        SmartCoolingDeficitSensor(coordinator, entry),
        SmartCoolingConfidenceSensor(coordinator, entry),
        SmartCoolingTimeToTargetSensor(coordinator, entry),
        SmartCoolingWillReachTargetAtSensor(coordinator, entry),
        SmartCoolingActionNeededBySensor(coordinator, entry),
        SmartCoolingReasoningSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class SmartCoolingBaseSensor(CoordinatorEntity[SmartCoolingCoordinator], SensorEntity):
    """Base class for Smart Cooling sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartCoolingCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self.coordinator.room_name,
            manufacturer=MANUFACTURER,
            model="Smart Cooling Controller",
            sw_version="0.1.0",
        )


class SmartCoolingRecommendationSensor(SmartCoolingBaseSensor):
    """Sensor showing the current cooling recommendation."""

    _attr_icon = "mdi:air-conditioner"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the recommendation sensor."""
        super().__init__(coordinator, entry, "recommendation", "Recommendation")

    @property
    def native_value(self) -> str | None:
        """Return the recommendation text."""
        if not self.coordinator.data:
            return None
        strategy = self.coordinator.data.get("strategy")
        if strategy:
            return strategy.display_text
        return "Unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
        
        strategy = self.coordinator.data.get("strategy")
        if not strategy:
            return {}
        
        attrs = strategy.to_dict()
        
        # Add current conditions for debugging
        conditions = self.coordinator.data.get("current_conditions", {})
        attrs["indoor_temp"] = conditions.get("indoor_temp")
        attrs["outdoor_temp"] = conditions.get("outdoor_temp")
        attrs["target_temp"] = conditions.get("target_temp")
        
        return attrs


class SmartCoolingPredictedTempSensor(SmartCoolingBaseSensor):
    """Sensor showing predicted temperature at target time with NO action taken (baseline)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the predicted temp sensor."""
        super().__init__(
            coordinator, entry, "predicted_target_temp", "Predicted Temp (No Action)"
        )

    @property
    def native_value(self) -> float | None:
        """Return the predicted temperature if nothing is done."""
        if not self.coordinator.data:
            return None
        prediction = self.coordinator.data.get("prediction")
        if prediction:
            return round(prediction.predicted_bedtime_temp, 1)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return prediction details including hourly breakdown and forecast diagnostics."""
        if not self.coordinator.data:
            return {}
        prediction = self.coordinator.data.get("prediction")
        if not prediction:
            return {}
        attrs = prediction.to_dict()
        attrs["forecast_entries"] = self.coordinator.data.get("forecast_entries", 0)
        attrs["forecast_sample"] = self.coordinator.data.get("forecast_sample", [])
        attrs["physics_params"] = self.coordinator.data.get("learned_params", {})
        return attrs


class SmartCoolingPredictedWithActionSensor(SmartCoolingBaseSensor):
    """Sensor showing predicted temperature at target time if the recommendation is followed."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer-check"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the with-action predicted temp sensor."""
        super().__init__(
            coordinator, entry, "predicted_temp_with_action", "Predicted Temp (With Recommendation)"
        )

    @property
    def native_value(self) -> float | None:
        """Return the predicted temperature if recommendation is acted on now."""
        if not self.coordinator.data:
            return None
        with_action = self.coordinator.data.get("with_action_prediction")
        if with_action:
            return round(with_action.predicted_bedtime_temp, 1)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return hourly breakdown for the with-action scenario."""
        if not self.coordinator.data:
            return {}
        with_action = self.coordinator.data.get("with_action_prediction")
        strategy = self.coordinator.data.get("strategy")
        if not with_action:
            return {}
        attrs = with_action.to_dict()
        if strategy:
            attrs["recommendation"] = strategy.method.value
        return attrs


class SmartCoolingDeficitSensor(SmartCoolingBaseSensor):
    """Sensor showing cooling deficit (degrees above target)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer-alert"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the deficit sensor."""
        super().__init__(coordinator, entry, "cooling_deficit", "Cooling Deficit")

    @property
    def native_value(self) -> float | None:
        """Return the cooling deficit."""
        if not self.coordinator.data:
            return None
        prediction = self.coordinator.data.get("prediction")
        if prediction:
            return round(prediction.cooling_deficit, 1)
        return None


class SmartCoolingTimeToTargetSensor(SmartCoolingBaseSensor):
    """Sensor showing estimated hours until indoor temp reaches target."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the time-to-target sensor."""
        super().__init__(coordinator, entry, "time_to_target", "Time to Reach Target Temp")

    @property
    def native_value(self) -> float | None:
        """Return hours until target temperature is reached."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("hours_until_cool")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional context."""
        if not self.coordinator.data:
            return {}
        hours = self.coordinator.data.get("hours_until_cool")
        strategy = self.coordinator.data.get("strategy")
        attrs: dict[str, Any] = {}
        if hours is not None:
            h = int(hours)
            m = int((hours - h) * 60)
            attrs["readable"] = f"{h}h {m}m" if h > 0 else f"{m} min"
        else:
            attrs["readable"] = "Not reachable in 24h"
        if strategy:
            attrs["cooling_method"] = strategy.method.value
        return attrs


class SmartCoolingWillReachTargetAtSensor(SmartCoolingBaseSensor):
    """Sensor showing the predicted datetime when target temperature will be reached."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:thermometer-check"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "will_reach_target_at", "Will Reach Target At")

    @property
    def native_value(self):
        """Return the datetime when target will be reached."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("will_reach_target_at")


class SmartCoolingActionNeededBySensor(SmartCoolingBaseSensor):
    """Sensor showing latest datetime by which cooling must be started to hit the target."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "action_needed_by", "Action Needed By")

    @property
    def native_value(self):
        """Return the deadline to start cooling."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("action_needed_by")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Indicate if action is already overdue."""
        if not self.coordinator.data:
            return {}
        action_by = self.coordinator.data.get("action_needed_by")
        if action_by is None:
            return {}
        from homeassistant.util import dt as dt_util
        now = dt_util.now()
        overdue = action_by <= now
        # Round to nearest 5 minutes to avoid per-minute attribute churn
        minutes_remaining = round((action_by - now).total_seconds() / 300) * 5
        return {
            "overdue": overdue,
            "minutes_remaining": minutes_remaining,
        }


class SmartCoolingReasoningSensor(SmartCoolingBaseSensor):
    """Sensor explaining why the current recommendation was made."""

    _attr_icon = "mdi:chat-question-outline"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "reasoning", "Recommendation Reasoning")

    @property
    def native_value(self) -> str | None:
        """Return the reasoning text (truncated for state, full in attributes)."""
        if not self.coordinator.data:
            return None
        strategy = self.coordinator.data.get("strategy")
        if not strategy:
            return None
        # HA states truncate at 255 chars; put full text in attributes
        text = strategy.reasoning
        if len(text) <= 255:
            return text
        # Trim at the last sentence boundary that fits within 255 chars
        trimmed = text[:254]
        last_period = trimmed.rfind(". ")
        if last_period > 60:
            return text[: last_period + 1]
        return trimmed + "…"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return full reasoning and alternatives."""
        if not self.coordinator.data:
            return {}
        strategy = self.coordinator.data.get("strategy")
        if not strategy:
            return {}
        return {
            "full_reasoning": strategy.reasoning,
            "alternatives": strategy.alternatives,
        }


class SmartCoolingConfidenceSensor(SmartCoolingBaseSensor):
    """Sensor showing prediction confidence based on learning."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:chart-line"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the confidence sensor."""
        super().__init__(coordinator, entry, "prediction_confidence", "Prediction Confidence")

    @property
    def native_value(self) -> float | None:
        """Return the confidence percentage."""
        if not self.coordinator.data:
            return None
        confidence = self.coordinator.data.get("prediction_confidence", 0.5)
        return round(confidence * 100, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return learned parameters."""
        if not self.coordinator.data:
            return {}
        
        return {
            "learned_params": self.coordinator.data.get("learned_params", {}),
        }
