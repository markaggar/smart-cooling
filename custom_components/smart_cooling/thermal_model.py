"""Thermal model for temperature prediction."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .const import DEFAULT_PHYSICS_PARAMS

_LOGGER = logging.getLogger(__name__)


@dataclass
class TemperaturePrediction:
    """Prediction result from thermal model."""
    
    predicted_bedtime_temp: float
    cooling_deficit: float  # How many degrees above target
    hourly_predictions: list[dict[str, Any]] = field(default_factory=list)
    uncooled_bedtime_temp: float = 0.0  # What temp would be without intervention
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for HA state attributes."""
        return {
            "predicted_bedtime_temp": round(self.predicted_bedtime_temp, 1),
            "cooling_deficit": round(self.cooling_deficit, 1),
            "uncooled_bedtime_temp": round(self.uncooled_bedtime_temp, 1),
            "hourly_predictions": self.hourly_predictions,
        }


class ThermalModel:
    """Physics-based thermal model for indoor temperature prediction."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize with configuration."""
        self.config = config
        # Start with default parameters, can be updated via learning
        self.params = dict(DEFAULT_PHYSICS_PARAMS)

    def update_params(self, new_params: dict[str, float]) -> None:
        """Update physics parameters (from learning or manual override)."""
        for key, value in new_params.items():
            if key in self.params:
                _LOGGER.debug("Updating param %s: %s -> %s", key, self.params[key], value)
                self.params[key] = value

    def calculate_heat_gain(
        self,
        hour: int,
        outdoor_temp: float,
        indoor_temp: float,
        cloud_coverage: float = 50.0,
        uv_index: float = 0.0,
    ) -> float:
        """Calculate heat gain rate in °F/hr for a given hour.
        
        Args:
            hour: Hour of day (0-23)
            outdoor_temp: Outside temperature in °F
            indoor_temp: Inside temperature in °F
            cloud_coverage: Cloud coverage percentage (0-100)
            uv_index: UV index (0-11+)
            
        Returns:
            Heat gain rate in °F/hr (positive = warming, negative = cooling)
        """
        base_gain = self.params["base_heat_gain_rate"]
        
        # Solar gain component (only during daylight, peak at solar noon)
        solar_multiplier = 0.0
        if 12 <= hour <= 18:
            uv_factor = uv_index / 10.0
            cloud_factor = (100 - cloud_coverage) / 100.0
            solar_multiplier = uv_factor * cloud_factor * self.params["solar_gain_factor"]
        
        # Ambient heat transfer (outdoor-indoor differential)
        temp_diff = outdoor_temp - indoor_temp
        ambient_gain = temp_diff * self.params["thermal_transfer_coefficient"]
        
        total_gain = base_gain * (1 + solar_multiplier) + ambient_gain
        
        # Heat gain can't be negative if outdoor is hotter
        if outdoor_temp > indoor_temp:
            return max(total_gain, 0.0)
        return total_gain

    def calculate_fan_cooling_rate(
        self, outdoor_temp: float, indoor_temp: float, wind_speed: float = 0.0
    ) -> float:
        """Calculate cooling rate when using window fan.
        
        Returns cooling rate in °F/hr (positive = degrees of cooling per hour).
        """
        temp_differential = indoor_temp - outdoor_temp
        
        if temp_differential <= 0:
            # Can't cool if outdoor is warmer
            return 0.0
        
        base_effectiveness = self.params["fan_cooling_effectiveness"]
        
        # Effectiveness scales with temperature differential
        if temp_differential > 10:
            effectiveness_multiplier = 1.0
        elif temp_differential > 5:
            effectiveness_multiplier = 0.8
        elif temp_differential > 2:
            effectiveness_multiplier = 0.5
        elif temp_differential > 0:
            effectiveness_multiplier = 0.2
        else:
            effectiveness_multiplier = 0.0
        
        # Wind boost
        effective_wind = max(wind_speed, self.params["fan_equivalent_wind_speed"])
        wind_factor = effective_wind / 10.0  # Normalize to ~1 at 10 mph
        
        cooling_rate = (
            temp_differential 
            * base_effectiveness 
            * effectiveness_multiplier 
            * wind_factor
        )
        
        return max(cooling_rate, 0.0)

    def calculate_ac_cooling_rate(self, outdoor_temp: float) -> float:
        """Calculate AC cooling rate based on outdoor temperature.
        
        AC is less effective when it's very hot outside.
        """
        if outdoor_temp >= 82:
            return self.params["ac_cooling_rate_hot"]
        return self.params["ac_cooling_rate_mild"]

    def predict_temperature(
        self,
        current_conditions: dict[str, Any],
        hours_ahead: float,
        cooling_strategy: str | None = None,
    ) -> TemperaturePrediction:
        """Predict temperature evolution over time.
        
        Args:
            current_conditions: Dict with indoor_temp, outdoor_temp, forecast, etc.
            hours_ahead: How many hours to predict
            cooling_strategy: Optional strategy to model ("fan", "ac", "natural", None)
            
        Returns:
            TemperaturePrediction with results
        """
        indoor_temp = current_conditions.get("indoor_temp", 72.0)
        outdoor_temp = current_conditions.get("outdoor_temp", 70.0)
        target_temp = current_conditions.get("target_temp", 72.0)
        forecast = current_conditions.get("forecast", [])
        current_time: datetime = current_conditions.get("current_time", datetime.now())
        
        # Simulate temperature evolution hour by hour
        hourly_predictions = []
        simulated_temp = indoor_temp
        
        hours_to_simulate = int(hours_ahead) + 1
        
        for i in range(hours_to_simulate):
            future_time = current_time + timedelta(hours=i)
            hour = future_time.hour
            
            # Get forecast data for this hour if available
            forecast_data = self._get_forecast_for_hour(forecast, future_time)
            hour_outdoor_temp = forecast_data.get("temperature", outdoor_temp)
            cloud_coverage = forecast_data.get("cloud_coverage", 50.0)
            uv_index = forecast_data.get("uv_index", 0.0)
            
            # Calculate heat gain for this hour
            heat_gain = self.calculate_heat_gain(
                hour=hour,
                outdoor_temp=hour_outdoor_temp,
                indoor_temp=simulated_temp,
                cloud_coverage=cloud_coverage,
                uv_index=uv_index,
            )
            
            # Apply cooling if strategy specified
            cooling = 0.0
            if cooling_strategy == "fan":
                cooling = self.calculate_fan_cooling_rate(
                    hour_outdoor_temp, simulated_temp
                )
            elif cooling_strategy == "ac":
                cooling = self.calculate_ac_cooling_rate(hour_outdoor_temp)
            elif cooling_strategy == "natural":
                # Natural ventilation (open window, no fan) — driven by
                # temperature differential and wind, but less effective than a fan
                temp_diff = simulated_temp - hour_outdoor_temp
                if temp_diff > 0:
                    wind_data = self._get_forecast_for_hour(forecast, future_time)
                    hour_wind = wind_data.get("wind_speed", current_conditions.get("wind_speed", 3.0))
                    wind_factor = max(float(hour_wind), 1.0) / 10.0
                    cooling = (
                        temp_diff
                        * self.params["natural_cooling_effectiveness"]
                        * wind_factor
                    )
            
            # Net temperature change
            net_change = heat_gain - cooling
            simulated_temp += net_change
            
            hourly_predictions.append({
                "hour": hour,
                "time": future_time.isoformat(),
                "predicted_temp": round(simulated_temp, 1),
                "outdoor_temp": hour_outdoor_temp,
                "heat_gain": round(heat_gain, 2),
                "cooling": round(cooling, 2),
                "net_change": round(net_change, 2),
            })
        
        # Final prediction is the last hour
        predicted_bedtime_temp = simulated_temp
        cooling_deficit = predicted_bedtime_temp - target_temp
        
        # Also calculate uncooled scenario
        uncooled_prediction = self.predict_temperature(
            current_conditions=current_conditions,
            hours_ahead=hours_ahead,
            cooling_strategy=None,
        ) if cooling_strategy else None
        
        return TemperaturePrediction(
            predicted_bedtime_temp=predicted_bedtime_temp,
            cooling_deficit=cooling_deficit,
            hourly_predictions=hourly_predictions,
            uncooled_bedtime_temp=(
                uncooled_prediction.predicted_bedtime_temp 
                if uncooled_prediction 
                else predicted_bedtime_temp
            ),
        )

    def find_hours_to_cool_to_target(
        self,
        current_conditions: dict[str, Any],
        cooling_strategy: str,
        max_hours: float = 24.0,
    ) -> float | None:
        """Find how many hours until indoor temp reaches target with given strategy.

        Simulates forward in 15-minute steps until crossing target or max_hours.
        Returns fractional hours, or None if target not reachable within max_hours.
        """
        indoor_temp = current_conditions.get("indoor_temp", 72.0)
        outdoor_temp = current_conditions.get("outdoor_temp", 70.0)
        target_temp = current_conditions.get("target_temp", 72.0)
        forecast = current_conditions.get("forecast", [])
        current_time: datetime = current_conditions.get("current_time", datetime.now())

        # Already at or below target
        if indoor_temp <= target_temp:
            return 0.0

        step_hours = 0.25  # 15-minute resolution
        steps = int(max_hours / step_hours)
        simulated_temp = indoor_temp

        for i in range(steps):
            elapsed_hours = i * step_hours
            future_time = current_time + timedelta(hours=elapsed_hours)
            hour = future_time.hour

            forecast_data = self._get_forecast_for_hour(forecast, future_time)
            hour_outdoor_temp = forecast_data.get("temperature", outdoor_temp)
            cloud_coverage = forecast_data.get("cloud_coverage", 50.0)
            uv_index = forecast_data.get("uv_index", 0.0)

            heat_gain = self.calculate_heat_gain(
                hour=hour,
                outdoor_temp=hour_outdoor_temp,
                indoor_temp=simulated_temp,
                cloud_coverage=cloud_coverage,
                uv_index=uv_index,
            )

            cooling = 0.0
            if cooling_strategy == "fan":
                cooling = self.calculate_fan_cooling_rate(hour_outdoor_temp, simulated_temp)
            elif cooling_strategy == "ac":
                cooling = self.calculate_ac_cooling_rate(hour_outdoor_temp)
            elif cooling_strategy == "natural":
                temp_diff = simulated_temp - hour_outdoor_temp
                if temp_diff > 0:
                    wind_data = self._get_forecast_for_hour(forecast, future_time)
                    hour_wind = wind_data.get("wind_speed", current_conditions.get("wind_speed", 3.0))
                    wind_factor = max(float(hour_wind), 1.0) / 10.0
                    cooling = (
                        temp_diff
                        * self.params["natural_cooling_effectiveness"]
                        * wind_factor
                    )

            if simulated_temp <= target_temp:
                # Interpolate back to precise crossing point
                return round(elapsed_hours, 2)

        return None  # Not reachable within max_hours

    def _get_forecast_for_hour(
        self, forecast: list[dict], target_time: datetime
    ) -> dict[str, Any]:
        """Extract forecast data for the hour closest to target_time."""
        if not forecast:
            return {}

        from datetime import timezone as _tz

        def _to_utc(dt: datetime) -> datetime:
            """Convert datetime to UTC; treat naive as UTC."""
            if dt.tzinfo is None:
                return dt.replace(tzinfo=_tz.utc)
            return dt.astimezone(_tz.utc)

        try:
            target_utc = _to_utc(target_time)
        except (AttributeError, ValueError, OverflowError):
            return {}

        best_entry = None
        best_delta = float("inf")

        for entry in forecast:
            entry_time = entry.get("datetime")
            if entry_time is None:
                continue

            try:
                if isinstance(entry_time, str):
                    if "T" in entry_time:
                        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    else:
                        entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M")
                        entry_dt = entry_dt.replace(tzinfo=_tz.utc)
                elif isinstance(entry_time, datetime):
                    entry_dt = entry_time
                else:
                    continue

                entry_utc = _to_utc(entry_dt)
            except (ValueError, OverflowError, AttributeError):
                continue

            delta = abs((entry_utc - target_utc).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best_entry = entry

        # Only use forecast data if it's within 90 minutes of target (hourly forecast
        # has entries every hour, so the closest should always be ≤30min away unless
        # the forecast window is exhausted)
        if best_entry is not None and best_delta < 5400:
            return {
                "temperature": best_entry.get("temperature", 70.0),
                "humidity": best_entry.get("humidity", 50.0),
                "cloud_coverage": best_entry.get("cloud_coverage", 50.0),
                "wind_speed": best_entry.get("wind_speed", 5.0),
                "uv_index": best_entry.get("uv_index", 0.0),
            }

        return {}
