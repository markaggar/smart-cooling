"""Binary sensor entities for Smart Cooling."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, MODEL, SW_VERSION
from .coordinator import SmartCoolingCoordinator
from .strategy_engine import CoolingMethod

_LOGGER = logging.getLogger(__name__)

# Methods that mean "AC is needed now or is actively running"
_AC_METHODS = {CoolingMethod.START_AC, CoolingMethod.CONTINUE_AC}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Cooling binary sensors from a config entry."""
    coordinator: SmartCoolingCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            SmartCoolingACNeededBinarySensor(coordinator, entry),
            SmartCoolingActionNeededNowBinarySensor(coordinator, entry),
        ]
    )


class SmartCoolingBaseBinarySensor(
    CoordinatorEntity[SmartCoolingCoordinator], BinarySensorEntity
):
    """Base class for Smart Cooling binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartCoolingCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize the binary sensor."""
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
            model=MODEL,
            sw_version=SW_VERSION,
        )


class SmartCoolingACNeededBinarySensor(SmartCoolingBaseBinarySensor):
    """Binary sensor that is ON when the recommended strategy requires AC.

    Use this sensor to trigger automations that turn on the AC unit.
    It turns ON for both START_AC (start now) and CONTINUE_AC (already running,
    keep going) so the automation can simply keep AC on while this is ON.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:air-conditioner"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the AC needed sensor."""
        super().__init__(coordinator, entry, "ac_needed", "AC Needed")

    @property
    def is_on(self) -> bool | None:
        """Return True when the recommendation is to run AC."""
        if not self.coordinator.data:
            return None
        strategy = self.coordinator.data.get("strategy")
        if strategy is None:
            return None
        return strategy.method in _AC_METHODS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the cooling method and reasoning for diagnostics."""
        if not self.coordinator.data:
            return {}
        strategy = self.coordinator.data.get("strategy")
        if strategy is None:
            return {}
        return {
            "method": strategy.method.value,
            "reasoning": strategy.reasoning,
            "confidence": strategy.confidence,
            "ac_deferred_peak": strategy.ac_deferred_peak,
            "precool_setpoint": strategy.precool_setpoint,
        }


class SmartCoolingActionNeededNowBinarySensor(SmartCoolingBaseBinarySensor):
    """Binary sensor that is ON when the action deadline is imminent (≤ 5 min).

    Turns ON when action_needed_by is not None and is within 5 minutes of now,
    or has already passed.  Use this to trigger urgent automations or
    notifications — it becomes ON regardless of which cooling method is needed.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(
        self, coordinator: SmartCoolingCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the action-needed-now sensor."""
        super().__init__(
            coordinator, entry, "action_needed_now", "Action Needed Now"
        )

    @property
    def is_on(self) -> bool | None:
        """Return True when the action deadline is within 5 minutes."""
        if not self.coordinator.data:
            return None
        action_needed_by = self.coordinator.data.get("action_needed_by")
        if action_needed_by is None:
            return False
        now = dt_util.now()
        return action_needed_by <= now + timedelta(minutes=5)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return deadline details for diagnostics."""
        if not self.coordinator.data:
            return {}
        action_needed_by = self.coordinator.data.get("action_needed_by")
        strategy = self.coordinator.data.get("strategy")
        attrs: dict[str, Any] = {}
        if action_needed_by is not None:
            now = dt_util.now()
            minutes_remaining = (action_needed_by - now).total_seconds() / 60
            attrs["action_needed_by"] = action_needed_by.isoformat()
            attrs["minutes_remaining"] = round(minutes_remaining, 1)
            attrs["overdue"] = minutes_remaining < 0
        if strategy is not None:
            attrs["method"] = strategy.method.value
        return attrs
