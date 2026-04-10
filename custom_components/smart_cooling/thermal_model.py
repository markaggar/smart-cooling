"""Thermal model for temperature prediction."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .const import DEFAULT_PHYSICS_PARAMS, WINDOW_DIRECTION_DEGREES

_LOGGER = logging.getLogger(__name__)


def wind_alignment_factor(
    wind_bearing: float | None,
    window_facing: list[str],
) -> float:
    """Return how well the wind aligns with the best-facing window.

    Uses cos(angle_diff) so head-on wind → 1.0, parallel → 0.0, tail → 0.0.
    Takes the max across all configured window directions so a room with windows
    on two walls benefits from whichever one the wind favours most.

    Returns 1.0 (no penalty) when wind_bearing is None or no window_facing set.
    """
    if wind_bearing is None or not window_facing:
        return 1.0

    best = 0.0
    for direction in window_facing:
        facing_deg = WINDOW_DIRECTION_DEGREES.get(direction)
        if facing_deg is None:
            continue
        # Smallest angle between wind bearing and window facing (0–180)
        diff = abs(wind_bearing - facing_deg) % 360
        if diff > 180:
            diff = 360 - diff
        alignment = max(0.0, math.cos(math.radians(diff)))
        if alignment > best:
            best = alignment
    return best if best > 0.0 else 0.0


@dataclass
class TemperaturePrediction:
    """Prediction result from thermal model."""
    
    predicted_target_temp: float
    cooling_deficit: float  # How many degrees above target
    hourly_predictions: list[dict[str, Any]] = field(default_factory=list)
    uncooled_target_temp: float = 0.0  # What temp would be without intervention
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for HA state attributes."""
        return {
            "predicted_target_temp": round(self.predicted_target_temp, 1),
            "cooling_deficit": round(self.cooling_deficit, 1),
            "uncooled_target_temp": round(self.uncooled_target_temp, 1),
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
        afternoon_solar_load: float = 0.0,
    ) -> float:
        """Calculate heat gain rate in °F/hr for a given hour.
        
        Args:
            hour: Hour of day (0-23)
            outdoor_temp: Outside temperature in °F
            indoor_temp: Inside temperature in °F
            cloud_coverage: Cloud coverage percentage (0-100)
            uv_index: UV index (0-11+)
            afternoon_solar_load: Peak solar fraction (0-1) from today's forecast
                (used for thermal lag calculation). Defaults to 0 (no lag).
            
        Returns:
            Heat gain rate in °F/hr (positive = warming, negative = cooling)
        """
        base_gain = self.params["base_heat_gain_rate"]

        # cloud_factor is used by both the direct solar window and the thermal lag.
        cloud_factor = (100.0 - cloud_coverage) / 100.0

        # Solar gain component: active 8 AM to 7 PM with a linear ramp peaking at 1 PM.
        # This captures morning wall heating (east/south exposure) that the previous
        # noon-only window missed.
        solar_multiplier = 0.0
        if 8 <= hour <= 19:
            if hour <= 13:
                solar_hour_factor = (hour - 8) / 5.0   # 0 at 8 AM → 1.0 at 1 PM
            else:
                solar_hour_factor = (19 - hour) / 6.0  # 1.0 at 1 PM → 0 at 7 PM
            uv_factor = uv_index / 10.0
            solar_multiplier = uv_factor * cloud_factor * solar_hour_factor * self.params["solar_gain_factor"]

        # Ambient heat transfer (outdoor-indoor differential)
        temp_diff = outdoor_temp - indoor_temp
        ambient_gain = temp_diff * self.params["thermal_transfer_coefficient"]

        # Thermal lag: walls and attic absorb solar energy during the afternoon and
        # re-radiate it into the room for several hours after sunset.
        # Model: exponential decay from thermal peak at ~2 PM, scaled by how sunny
        # the afternoon was (afternoon_solar_load = 0 when unavailable → no lag).
        thermal_lag_gain = 0.0
        if afternoon_solar_load > 0.0:
            lag_hours = (hour - 14) % 24  # hours since thermal peak; handles midnight wrap
            if lag_hours < 12:            # only apply up to ~2 AM
                decay = math.exp(-lag_hours / 4.0)  # 4-hour time constant
                thermal_lag_gain = (
                    afternoon_solar_load
                    * self.params["thermal_lag_factor"]
                    * base_gain
                    * decay
                )

        total_gain = base_gain * (1 + solar_multiplier) + ambient_gain + thermal_lag_gain

        # Heat gain can't be negative if outdoor is hotter
        if outdoor_temp > indoor_temp:
            return max(total_gain, 0.0)
        return total_gain

    def calculate_fan_cooling_rate(
        self,
        outdoor_temp: float,
        indoor_temp: float,
        wind_speed: float = 0.0,
        outdoor_humidity: float = 50.0,
    ) -> float:
        """Calculate cooling rate when using window fan.

        outdoor_humidity (0-100 %RH) reduces effectiveness — humid air transfers
        less heat via convection and offers no evaporative benefit.
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

        # Humidity penalty: high outdoor humidity reduces convective cooling.
        # At 40% RH → factor=1.0; at 90% RH → factor=0.75; clamped at 0.5.
        humidity_factor = max(0.5, 1.0 - max(outdoor_humidity - 40.0, 0.0) * 0.005)
        
        cooling_rate = (
            temp_differential
            * base_effectiveness
            * effectiveness_multiplier
            * wind_factor
            * humidity_factor
        )

        return max(cooling_rate, 0.0)

    def calculate_ac_cooling_rate(self, outdoor_temp: float) -> float:
        """Calculate AC cooling rate based on outdoor temperature.
        
        AC is less effective when it's very hot outside.
        """
        if outdoor_temp >= 82:
            return self.params["ac_cooling_rate_hot"]
        return self.params["ac_cooling_rate_mild"]

    def _compute_cooling_for_hour(
        self,
        cooling_strategy: str | None,
        simulated_temp: float,
        hour_outdoor_temp: float,
        hour_humidity: float,
        hour_wind: float,
        hour_bearing: float | None,
        window_facing: list[str],
    ) -> float:
        """Return the cooling rate (°F/hr) for one simulation step."""
        if cooling_strategy == "fan":
            alignment = wind_alignment_factor(hour_bearing, window_facing)
            return self.calculate_fan_cooling_rate(
                hour_outdoor_temp, simulated_temp,
                wind_speed=hour_wind * alignment,
                outdoor_humidity=hour_humidity,
            )
        if cooling_strategy == "ac":
            return self.calculate_ac_cooling_rate(hour_outdoor_temp)
        if cooling_strategy == "natural":
            temp_diff = simulated_temp - hour_outdoor_temp
            if temp_diff > 0:
                alignment = wind_alignment_factor(hour_bearing, window_facing)
                # Floor of 0.3 so truly still air gives near-zero ventilation
                wind_factor = max(hour_wind * alignment, 0.3) / 10.0
                humidity_factor = max(0.5, 1.0 - max(hour_humidity - 40.0, 0.0) * 0.005)
                return (
                    temp_diff
                    * self.params["natural_cooling_effectiveness"]
                    * wind_factor
                    * humidity_factor
                )
        return 0.0

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

        # Compute once: peak afternoon solar load for thermal lag.
        # Take the max of (a) what the forecast says and (b) the day's running
        # maximum tracked by the coordinator.  The forecast loses afternoon
        # entries once those hours pass, so (b) is critical in the evening.
        _forecast_solar = self._get_peak_afternoon_solar(forecast)
        _tracked_solar = current_conditions.get("peak_afternoon_solar", 0.0)
        peak_afternoon_solar = max(_forecast_solar, _tracked_solar)

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
                afternoon_solar_load=peak_afternoon_solar,
            )
            hour_humidity = float(forecast_data.get("humidity", current_conditions.get("outdoor_humidity", 50.0)))
            _wind_fallback = current_conditions.get("wind_speed", 3.0 if cooling_strategy == "natural" else 0.0)
            hour_wind = float(forecast_data.get("wind_speed", _wind_fallback))
            hour_bearing = forecast_data.get("wind_bearing", current_conditions.get("wind_bearing"))
            window_facing = current_conditions.get("window_facing", [])
            cooling = self._compute_cooling_for_hour(
                cooling_strategy, simulated_temp, hour_outdoor_temp,
                hour_humidity, hour_wind, hour_bearing, window_facing,
            )
            
            # Net temperature change
            net_change = heat_gain - cooling
            simulated_temp += net_change

            if cooling_strategy == "ac":
                # AC won't cool below its new setpoint (the target temp).
                simulated_temp = max(simulated_temp, float(target_temp))
            else:
                # Background AC protection: the current setpoint is a ceiling for
                # non-AC strategies. The thermostat prevents the room rising above it
                # when we simulate fan/natural/no-action scenarios.
                ac_setpoint = current_conditions.get("ac_setpoint")
                if ac_setpoint is not None:
                    simulated_temp = min(simulated_temp, float(ac_setpoint))

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
        predicted_target_temp = simulated_temp
        cooling_deficit = predicted_target_temp - target_temp
        
        # Also calculate uncooled scenario
        uncooled_prediction = self.predict_temperature(
            current_conditions=current_conditions,
            hours_ahead=hours_ahead,
            cooling_strategy=None,
        ) if cooling_strategy else None
        
        return TemperaturePrediction(
            predicted_target_temp=predicted_target_temp,
            cooling_deficit=cooling_deficit,
            hourly_predictions=hourly_predictions,
            uncooled_target_temp=(
                uncooled_prediction.predicted_target_temp
                if uncooled_prediction
                else predicted_target_temp
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

        # Compute once: peak afternoon solar load for thermal lag.
        # Take max of forecast-based and coordinator-tracked peaks.
        _forecast_solar = self._get_peak_afternoon_solar(forecast)
        _tracked_solar = current_conditions.get("peak_afternoon_solar", 0.0)
        peak_afternoon_solar = max(_forecast_solar, _tracked_solar)

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
                afternoon_solar_load=peak_afternoon_solar,
            )

            hour_humidity = float(forecast_data.get("humidity", current_conditions.get("outdoor_humidity", 50.0)))
            _wind_fallback = current_conditions.get("wind_speed", 3.0 if cooling_strategy == "natural" else 0.0)
            hour_wind = float(forecast_data.get("wind_speed", _wind_fallback))
            hour_bearing = forecast_data.get("wind_bearing", current_conditions.get("wind_bearing"))
            window_facing = current_conditions.get("window_facing", [])
            cooling = self._compute_cooling_for_hour(
                cooling_strategy, simulated_temp, hour_outdoor_temp,
                hour_humidity, hour_wind, hour_bearing, window_facing,
            )

            # Apply fractional step (rates are per-hour; step is 0.25h)
            net_change = heat_gain - cooling
            simulated_temp += net_change * step_hours

            # Background AC protection: ceiling for non-AC strategies.
            ac_setpoint = current_conditions.get("ac_setpoint")
            if cooling_strategy != "ac" and ac_setpoint is not None:
                simulated_temp = min(simulated_temp, float(ac_setpoint))

            if simulated_temp <= target_temp:
                return round(elapsed_hours + step_hours, 2)

        return None  # Not reachable within max_hours

    def simulate_comfort_window(
        self,
        current_conditions: dict[str, Any],
        start_temp: float,
        start_time: datetime,
        window_hours: float,
        cooling_strategy: str | None = None,
    ) -> dict[str, Any]:
        """Simulate temperature over the comfort window (e.g. midnight → wake time).

        Args:
            current_conditions: Standard conditions dict (for forecast, sensors, etc.)
            start_temp: Indoor temperature at the *start* of the comfort window.
            start_time: Datetime when the comfort window begins.
            window_hours: Duration of the window in hours.
            cooling_strategy: Active cooling strategy during the window
                (None = passive, "ac", "fan", "natural").

        Returns a dict with:
          peak_temp    – highest predicted temp during the window (°F)
          peak_at      – ISO datetime string when peak occurs
          end_temp     – predicted temp at the end of the window
          hourly_predictions – list of hourly step dicts
        """
        forecast = current_conditions.get("forecast", [])
        ac_setpoint = current_conditions.get("ac_setpoint")
        outdoor_temp_default = current_conditions.get("outdoor_temp", 70.0)
        outdoor_humidity_default = current_conditions.get("outdoor_humidity", 50.0)
        wind_speed_default = current_conditions.get("wind_speed", 0.0)
        wind_bearing_default = current_conditions.get("wind_bearing")
        window_facing = current_conditions.get("window_facing", [])

        simulated_temp = start_temp
        hourly_predictions: list[dict[str, Any]] = []
        peak_temp = start_temp
        peak_at = start_time.isoformat()

        # Compute once: peak afternoon solar load for thermal lag.
        # Take max of forecast-based and coordinator-tracked peaks.
        _forecast_solar = self._get_peak_afternoon_solar(forecast)
        _tracked_solar = current_conditions.get("peak_afternoon_solar", 0.0)
        peak_afternoon_solar = max(_forecast_solar, _tracked_solar)

        hours_to_simulate = int(window_hours) + 1

        for i in range(hours_to_simulate):
            future_time = start_time + timedelta(hours=i)
            hour = future_time.hour

            forecast_data = self._get_forecast_for_hour(forecast, future_time)
            hour_outdoor_temp = forecast_data.get("temperature", outdoor_temp_default)
            cloud_coverage = forecast_data.get("cloud_coverage", 50.0)
            uv_index = forecast_data.get("uv_index", 0.0)

            heat_gain = self.calculate_heat_gain(
                hour=hour,
                outdoor_temp=hour_outdoor_temp,
                indoor_temp=simulated_temp,
                cloud_coverage=cloud_coverage,
                uv_index=uv_index,
                afternoon_solar_load=peak_afternoon_solar,
            )

            hour_wind = float(forecast_data.get("wind_speed", wind_speed_default))
            hour_bearing = forecast_data.get("wind_bearing", wind_bearing_default)
            hour_humidity = float(forecast_data.get("humidity", outdoor_humidity_default))
            cooling = self._compute_cooling_for_hour(
                cooling_strategy, simulated_temp, hour_outdoor_temp,
                hour_humidity, hour_wind, hour_bearing, window_facing,
            )

            net_change = heat_gain - cooling
            simulated_temp += net_change

            if cooling_strategy == "ac":
                # AC won't cool below its new setpoint (the target temp).
                target_temp_val = current_conditions.get("target_temp")
                if target_temp_val is not None:
                    simulated_temp = max(simulated_temp, float(target_temp_val))
            elif ac_setpoint is not None:
                # Background AC protection: ceiling for non-AC strategies.
                simulated_temp = min(simulated_temp, float(ac_setpoint))

            if simulated_temp > peak_temp:
                peak_temp = simulated_temp
                peak_at = future_time.isoformat()

            hourly_predictions.append({
                "hour": hour,
                "time": future_time.isoformat(),
                "predicted_temp": round(simulated_temp, 1),
                "outdoor_temp": hour_outdoor_temp,
                "heat_gain": round(heat_gain, 2),
                "cooling": round(cooling, 2),
                "net_change": round(net_change, 2),
            })

        return {
            "peak_temp": round(peak_temp, 1),
            "peak_at": peak_at,
            "end_temp": round(simulated_temp, 1),
            "hourly_predictions": hourly_predictions,
        }

    def _get_peak_afternoon_solar(self, forecast: list[dict]) -> float:
        """Return the peak afternoon solar intensity (0-1) from the forecast.

        Scans all forecast entries for hours 9-17 and returns the highest
        cloud-adjusted UV fraction.  Used to estimate wall thermal lag for
        evening simulations — returns 0.0 when no afternoon data is available,
        which safely disables the thermal-lag term.
        """
        if not forecast:
            return 0.0

        peak_solar = 0.0
        for entry in forecast:
            entry_time = entry.get("datetime")
            if entry_time is None:
                continue
            try:
                if isinstance(entry_time, str):
                    entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                elif isinstance(entry_time, datetime):
                    entry_dt = entry_time
                else:
                    continue
                # Normalise to naive local for hour comparison
                if entry_dt.tzinfo is not None:
                    entry_dt = entry_dt.astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, AttributeError, OverflowError):
                continue

            if not (9 <= entry_dt.hour <= 17):
                continue

            uv = float(entry.get("uv_index", 0.0))
            cloud = float(entry.get("cloud_coverage", 50.0))
            cloud_factor = (100.0 - cloud) / 100.0
            solar = (uv / 10.0) * cloud_factor
            if solar > peak_solar:
                peak_solar = solar

        return min(peak_solar, 1.0)

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
