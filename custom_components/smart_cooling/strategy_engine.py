"""Strategy engine for cooling recommendations."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .thermal_model import ThermalModel, TemperaturePrediction

_LOGGER = logging.getLogger(__name__)


class CoolingMethod(Enum):
    """Available cooling methods."""
    
    NO_ACTION = "no_action"
    OPEN_WINDOW = "open_window"
    START_FAN = "start_fan"
    CONTINUE_FAN = "continue_fan"
    START_AC = "start_ac"
    CONTINUE_AC = "continue_ac"
    KEEP_WINDOW_OPEN = "keep_window_open"


@dataclass
class CoolingStrategy:
    """Recommended cooling strategy."""
    
    method: CoolingMethod
    timing: str  # "NOW!", "at 6:30pm", etc.
    predicted_temp: float
    target_temp: float
    reasoning: str
    confidence: float  # 0-1 confidence in this recommendation
    
    # Comparison data for alternative strategies
    alternatives: list[dict[str, Any]] | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for HA state attributes."""
        return {
            "method": self.method.value,
            "timing": self.timing,
            "predicted_temp": round(self.predicted_temp, 1),
            "target_temp": round(self.target_temp, 1),
            "reasoning": self.reasoning,
            "confidence": round(self.confidence, 2),
            "alternatives": self.alternatives,
        }

    @property
    def display_text(self) -> str:
        """Human-readable recommendation text."""
        method_display = {
            CoolingMethod.NO_ACTION: "No action needed",
            CoolingMethod.OPEN_WINDOW: "Open window",
            CoolingMethod.START_FAN: "Start fan",
            CoolingMethod.CONTINUE_FAN: "Continue fan",
            CoolingMethod.START_AC: "Start AC",
            CoolingMethod.CONTINUE_AC: "Continue AC",
            CoolingMethod.KEEP_WINDOW_OPEN: "Keep window open",
        }
        
        text = method_display.get(self.method, self.method.value)
        if self.timing:
            text += f" {self.timing}"
        return text


class StrategyEngine:
    """Engine for determining optimal cooling strategy."""

    def __init__(self, thermal_model: ThermalModel) -> None:
        """Initialize with thermal model."""
        self.thermal_model = thermal_model
        
        # Thresholds (can be made configurable)
        self.comfort_tolerance = 2.0  # °F tolerance from target
        self.aqi_threshold = 150  # Max AQI for window opening
        self.min_temp_advantage = 3.0  # Min °F cooler outside vs inside

    def recommend(
        self,
        current_conditions: dict[str, Any],
        prediction: TemperaturePrediction,
        tolerance_minutes: int = 30,
    ) -> CoolingStrategy:
        """Determine the best cooling strategy.
        
        Tolerance-aware priority: if a lower-energy method (fan/window) can reach
        target within target_time + tolerance_minutes, prefer it over AC.
        Priority: natural > fan > AC
        """
        indoor_temp = current_conditions.get("indoor_temp", 72.0)
        outdoor_temp = current_conditions.get("outdoor_temp", 70.0)
        target_temp = current_conditions.get("target_temp", 72.0)
        aqi = current_conditions.get("aqi", 50)
        wind_speed = current_conditions.get("wind_speed", 5.0)
        
        # Device states (if available)
        window_open = current_conditions.get("window_open", False)
        fan_running = current_conditions.get("fan_running", False)
        ac_running = current_conditions.get("ac_running", False)
        
        cooling_deficit = prediction.cooling_deficit
        hours_to_target = self._hours_from_conditions(current_conditions)
        tolerance_hours = tolerance_minutes / 60.0
        
        # Check if we need cooling at all
        if cooling_deficit <= self.comfort_tolerance:
            return CoolingStrategy(
                method=CoolingMethod.NO_ACTION,
                timing="",
                predicted_temp=prediction.predicted_bedtime_temp,
                target_temp=target_temp,
                reasoning=self._no_action_reasoning(indoor_temp, target_temp, outdoor_temp, ac_running, fan_running),
                confidence=0.9,
            )
        
        # Check environmental conditions for natural/fan cooling
        temp_advantage = indoor_temp - outdoor_temp
        has_temp_advantage = temp_advantage >= self.min_temp_advantage
        aqi_ok = aqi <= self.aqi_threshold

        strategies = []

        # Evaluate natural cooling (open window, no fan).
        # Don't gate on current outdoor temp — forecast may show outdoor dropping
        # well below target later; find_hours_to_cool_to_target uses hourly forecast
        # and returns None only if target is unreachable within 24h.
        if aqi_ok:
            natural_h = self.thermal_model.find_hours_to_cool_to_target(
                current_conditions, "natural",
            )
            natural_within_tolerance = (
                natural_h is not None and natural_h <= (hours_to_target + tolerance_hours)
            )
            natural_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
                cooling_strategy="natural",
            )
            strategies.append({
                "method": CoolingMethod.OPEN_WINDOW,
                "prediction": natural_prediction,
                "hours_to_cool": natural_h,
                "achieves_target": natural_within_tolerance,
            })

        # Evaluate fan cooling
        if aqi_ok:
            fan_h = self.thermal_model.find_hours_to_cool_to_target(
                current_conditions, "fan",
            )
            fan_within_tolerance = (
                fan_h is not None and fan_h <= (hours_to_target + tolerance_hours)
            )
            fan_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
                cooling_strategy="fan",
            )
            strategies.append({
                "method": CoolingMethod.START_FAN,
                "prediction": fan_prediction,
                "hours_to_cool": fan_h,
                "achieves_target": fan_within_tolerance,
            })

        # Evaluate AC cooling (always available as fallback)
        ac_h = self.thermal_model.find_hours_to_cool_to_target(
            current_conditions, "ac",
        )
        ac_within_tolerance = (
            ac_h is not None and ac_h <= (hours_to_target + tolerance_hours)
        )
        ac_prediction = self.thermal_model.predict_temperature(
            current_conditions=current_conditions,
            hours_ahead=hours_to_target,
            cooling_strategy="ac",
        )
        strategies.append({
            "method": CoolingMethod.START_AC,
            "prediction": ac_prediction,
            "hours_to_cool": ac_h,
            "achieves_target": ac_within_tolerance,
        })

        # Select best strategy (prefer energy efficiency — first that achieves within tolerance)
        best_strategy = None
        for s in strategies:
            if s["achieves_target"]:
                best_strategy = s
                break

        # Nothing achieves target even with tolerance — fall back to AC
        if best_strategy is None:
            best_strategy = strategies[-1]

        # Adjust method based on current device states
        method = best_strategy["method"]
        if method == CoolingMethod.START_FAN and fan_running:
            method = CoolingMethod.CONTINUE_FAN
        elif method == CoolingMethod.START_AC and ac_running:
            method = CoolingMethod.CONTINUE_AC
        elif method == CoolingMethod.OPEN_WINDOW and window_open:
            method = CoolingMethod.KEEP_WINDOW_OPEN

        reasoning = self._generate_reasoning(
            method=method,
            conditions=current_conditions,
            strategy=best_strategy,
            strategies=strategies,
            tolerance_minutes=tolerance_minutes,
            window_open=window_open,
            fan_running=fan_running,
            ac_running=ac_running,
            aqi_ok=aqi_ok,
            aqi=aqi,
            hours_to_target=hours_to_target,
        )

        return CoolingStrategy(
            method=method,
            timing="NOW!" if best_strategy["achieves_target"] else "LATE — target may not be reached",
            predicted_temp=best_strategy["prediction"].predicted_bedtime_temp,
            target_temp=target_temp,
            reasoning=reasoning,
            confidence=0.7 if best_strategy["achieves_target"] else 0.4,
            alternatives=[
                {
                    "method": s["method"].value,
                    "predicted_temp": round(s["prediction"].predicted_bedtime_temp, 1),
                    "hours_to_cool": round(s["hours_to_cool"], 1) if s["hours_to_cool"] is not None else None,
                    "achieves_target": s["achieves_target"],
                }
                for s in strategies
                if s is not best_strategy
            ],
        )

    def _no_action_reasoning(
        self,
        indoor_temp: float,
        target_temp: float,
        outdoor_temp: float,
        ac_running: bool,
        fan_running: bool,
    ) -> str:
        """Explain why no action is needed."""
        parts = []
        if indoor_temp <= target_temp:
            parts.append(f"Room is already at {indoor_temp:.1f}°F, at or below target of {target_temp:.1f}°F")
        else:
            parts.append(f"Room is {indoor_temp:.1f}°F, within comfort range of {target_temp:.1f}°F target")

        if ac_running:
            parts.append("AC is already running and keeping up")
        elif fan_running:
            parts.append("Fan is running effectively")
        elif outdoor_temp < indoor_temp:
            parts.append(f"Outside air ({outdoor_temp:.1f}°F) is cooler and providing passive cooling")

        return ". ".join(parts) + "."

    def _generate_reasoning(
        self,
        method: CoolingMethod,
        conditions: dict[str, Any],
        strategy: dict[str, Any],
        strategies: list[dict[str, Any]],
        tolerance_minutes: int,
        window_open: bool,
        fan_running: bool,
        ac_running: bool,
        aqi_ok: bool,
        aqi: float,
        hours_to_target: float,
    ) -> str:
        """Generate detailed, contextual reasoning using forecast trajectory."""
        indoor_temp = conditions.get("indoor_temp", 72.0)
        outdoor_temp = conditions.get("outdoor_temp", 70.0)
        outdoor_humidity = conditions.get("outdoor_humidity", 50.0)
        target_temp = conditions.get("target_temp", 72.0)
        forecast = conditions.get("forecast", [])
        current_time: datetime = conditions.get("current_time", datetime.now())
        hours_to_cool = strategy.get("hours_to_cool")
        achieves = strategy.get("achieves_target", False)

        parts: list[str] = []

        # --- What the room needs ---
        deficit = indoor_temp - target_temp
        h = int(hours_to_target)
        m = int((hours_to_target - h) * 60)
        time_str = f"{h}h {m}m" if h > 0 else f"{m} min"
        parts.append(
            f"Room is {indoor_temp:.1f}°F, needs to reach {target_temp:.1f}°F "
            f"({deficit:.1f}°F drop) in {time_str}"
        )

        # --- Forecast trajectory ---
        # Pull outdoor temps from forecast over the prediction window
        forecast_temps: list[float] = []
        forecast_humidities: list[float] = []
        if forecast:
            from datetime import timezone as _tz

            def _to_utc(dt: datetime) -> datetime:
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=_tz.utc)
                return dt.astimezone(_tz.utc)

            window_end_utc = _to_utc(current_time + timedelta(hours=hours_to_target))
            now_utc = _to_utc(current_time)

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
                    entry_utc = _to_utc(entry_dt)
                    if now_utc <= entry_utc <= window_end_utc:
                        t = entry.get("temperature")
                        rh = entry.get("humidity")
                        if t is not None:
                            forecast_temps.append(float(t))
                        if rh is not None:
                            forecast_humidities.append(float(rh))
                except (ValueError, AttributeError, OverflowError):
                    continue

        if forecast_temps:
            min_forecast = min(forecast_temps)
            max_forecast = max(forecast_temps)
            if abs(max_forecast - min_forecast) >= 2.0:
                # Temps are changing meaningfully — describe the trajectory
                if forecast_temps[-1] < forecast_temps[0]:
                    parts.append(
                        f"Outdoor temp will drop from {outdoor_temp:.0f}°F now to "
                        f"{min_forecast:.0f}°F by target time"
                    )
                else:
                    parts.append(
                        f"Outdoor temp rises from {outdoor_temp:.0f}°F now to "
                        f"{max_forecast:.0f}°F — conditions warming through the window"
                    )
            else:
                # Relatively stable — just report current
                parts.append(f"Outdoor temp is stable around {outdoor_temp:.0f}°F")
        else:
            # No forecast data — fall back to current reading
            diff = indoor_temp - outdoor_temp
            if diff >= self.min_temp_advantage:
                parts.append(f"Outside is {outdoor_temp:.1f}°F ({diff:.1f}°F cooler than inside)")
            else:
                parts.append(f"Outside is {outdoor_temp:.1f}°F (currently only {diff:.1f}°F cooler than inside)")

        # --- Humidity note if it's materially affecting fan/window cooling ---
        if method in (
            CoolingMethod.START_FAN, CoolingMethod.CONTINUE_FAN,
            CoolingMethod.OPEN_WINDOW, CoolingMethod.KEEP_WINDOW_OPEN,
        ):
            avg_humidity = (
                sum(forecast_humidities) / len(forecast_humidities)
                if forecast_humidities else outdoor_humidity
            )
            if avg_humidity >= 70:
                reduction_pct = int(min(50, (avg_humidity - 40) * 0.5))
                parts.append(
                    f"High outdoor humidity ({avg_humidity:.0f}% RH) is reducing "
                    f"ventilation effectiveness by ~{reduction_pct}%"
                )

        # --- AQI note ---
        if not aqi_ok:
            parts.append(
                f"AQI is {aqi:.0f} (above {self.aqi_threshold} threshold) — "
                f"windows/fans not recommended"
            )

        # --- Current device states ---
        active_devices = []
        if ac_running:
            active_devices.append("AC is on")
        if fan_running:
            active_devices.append("fan is running")
        if window_open:
            active_devices.append("window is open")
        if active_devices:
            parts.append(", ".join(active_devices).capitalize())

        # --- Why this method was chosen ---
        natural_strategy = next((s for s in strategies if s["method"] == CoolingMethod.OPEN_WINDOW), None)
        fan_strategy = next((s for s in strategies if s["method"] == CoolingMethod.START_FAN), None)
        ac_strategy = next((s for s in strategies if s["method"] == CoolingMethod.START_AC), None)

        def _cool_time_str(hrs: float) -> str:
            ch = int(hrs)
            cm = int((hrs - ch) * 60)
            return f"{ch}h {cm}m" if ch > 0 else f"{cm} min"

        if method in (CoolingMethod.OPEN_WINDOW, CoolingMethod.KEEP_WINDOW_OPEN):
            if hours_to_cool is not None:
                parts.append(f"Natural ventilation will reach target in {_cool_time_str(hours_to_cool)}")
                if forecast_temps and min(forecast_temps) < target_temp:
                    parts.append(
                        f"Forecast shows outdoor air dropping to {min(forecast_temps):.0f}°F — "
                        f"passive cooling alone is sufficient"
                    )
            if tolerance_minutes > 0 and achieves:
                parts.append(
                    f"This is within the {tolerance_minutes}-minute tolerance — "
                    f"no fan or AC needed"
                )

        elif method in (CoolingMethod.START_FAN, CoolingMethod.CONTINUE_FAN):
            if natural_strategy:
                nat_h = natural_strategy.get("hours_to_cool")
                if nat_h is None or not natural_strategy.get("achieves_target"):
                    parts.append("Natural ventilation alone is not enough — fan needed")
            if hours_to_cool is not None:
                parts.append(f"Fan will reach target in {_cool_time_str(hours_to_cool)}")
            if tolerance_minutes > 0 and achieves:
                parts.append(
                    f"This is within the {tolerance_minutes}-minute tolerance — "
                    f"fan preferred over AC to save energy"
                )
            if ac_strategy and ac_strategy.get("hours_to_cool") is not None:
                ac_h = ac_strategy["hours_to_cool"]
                parts.append(f"AC would be faster ({ac_h:.1f}h) but not needed given tolerance")

        elif method in (CoolingMethod.START_AC, CoolingMethod.CONTINUE_AC):
            # Explain why lower-energy options were rejected
            if not aqi_ok:
                parts.append("Air quality prevents opening windows or using fan")
            else:
                if fan_strategy:
                    fan_h = fan_strategy.get("hours_to_cool")
                    if fan_h is None:
                        # Check if forecast temps ever get below target
                        if forecast_temps and min(forecast_temps) >= target_temp:
                            parts.append(
                                f"Outdoor air ({min(forecast_temps):.0f}°F min forecast) "
                                f"won't drop below target — fan/window cannot cool the room"
                            )
                        else:
                            parts.append("Fan cannot cool the room to target within 24 hours")
                    elif not fan_strategy.get("achieves_target"):
                        parts.append(
                            f"Fan would take {fan_h:.1f}h, which exceeds the "
                            f"{tolerance_minutes}-minute tolerance — AC required"
                        )
            if hours_to_cool is not None:
                parts.append(f"AC will reach target in {_cool_time_str(hours_to_cool)}")
            elif not achieves:
                if forecast_temps and min(forecast_temps) >= target_temp:
                    parts.append(
                        f"Forecast low is {min(forecast_temps):.0f}°F — outdoor air stays "
                        f"above target all night, even AC may struggle"
                    )

        # --- Late warning ---
        if not achieves:
            parts.append(
                "Target may not be reached by the deadline — "
                "consider starting earlier or using AC"
            )

        return ". ".join(parts) + "."

    def _hours_from_conditions(self, conditions: dict[str, Any]) -> float:
        """Extract hours to target time from conditions."""
        target_time_str = conditions.get("target_time", conditions.get("bedtime", "22:30:00"))
        current_time = conditions.get("current_time", datetime.now())

        try:
            target_time = datetime.strptime(target_time_str, "%H:%M:%S").time()
            target_today = current_time.replace(
                hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
            )
            if target_today < current_time:
                target_today += timedelta(days=1)
            return (target_today - current_time).total_seconds() / 3600
        except ValueError:
            return 8.0

