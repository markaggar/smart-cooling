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
            has_temp_advantage=has_temp_advantage,
            aqi_ok=aqi_ok,
            temp_advantage=temp_advantage,
            wind_speed=wind_speed,
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
        has_temp_advantage: bool,
        aqi_ok: bool,
        temp_advantage: float,
        wind_speed: float,
        aqi: float,
        hours_to_target: float,
    ) -> str:
        """Generate detailed, contextual reasoning for the recommendation."""
        indoor_temp = conditions.get("indoor_temp", 72.0)
        outdoor_temp = conditions.get("outdoor_temp", 70.0)
        target_temp = conditions.get("target_temp", 72.0)
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

        # --- Outdoor conditions ---
        if has_temp_advantage:
            wind_str = f", with {wind_speed:.0f} mph wind" if wind_speed >= 3 else ""
            parts.append(
                f"Outside is {outdoor_temp:.1f}°F ({temp_advantage:.1f}°F cooler than inside{wind_str})"
            )
        else:
            parts.append(
                f"Outside ({outdoor_temp:.1f}°F) is not cool enough for natural cooling "
                f"(need {self.min_temp_advantage:.0f}°F+ advantage)"
            )

        # --- AQI note if relevant ---
        if not aqi_ok:
            parts.append(f"AQI is {aqi:.0f} (above {self.aqi_threshold} threshold) — windows/fans not recommended")

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
        fan_strategy = next((s for s in strategies if s["method"] == CoolingMethod.START_FAN), None)
        ac_strategy = next((s for s in strategies if s["method"] == CoolingMethod.START_AC), None)

        if method in (CoolingMethod.START_FAN, CoolingMethod.CONTINUE_FAN):
            if hours_to_cool is not None:
                cool_h = int(hours_to_cool)
                cool_m = int((hours_to_cool - cool_h) * 60)
                cool_str = f"{cool_h}h {cool_m}m" if cool_h > 0 else f"{cool_m} min"
                parts.append(f"Fan will reach target in {cool_str}")
            if tolerance_minutes > 0 and achieves:
                parts.append(f"This is within the {tolerance_minutes}-minute tolerance — fan preferred over AC to save energy")
            if ac_strategy and ac_strategy.get("hours_to_cool") is not None:
                ac_h = ac_strategy["hours_to_cool"]
                parts.append(f"AC would be faster ({ac_h:.1f}h) but not needed given tolerance")

        elif method in (CoolingMethod.START_AC, CoolingMethod.CONTINUE_AC):
            if fan_strategy:
                fan_h = fan_strategy.get("hours_to_cool")
                if fan_h is None:
                    parts.append("Fan cannot cool the room enough — AC required")
                elif not fan_strategy.get("achieves_target"):
                    parts.append(
                        f"Fan would take {fan_h:.1f}h, which exceeds the {tolerance_minutes}-minute tolerance — AC required"
                    )
            if hours_to_cool is not None:
                cool_h = int(hours_to_cool)
                cool_m = int((hours_to_cool - cool_h) * 60)
                cool_str = f"{cool_h}h {cool_m}m" if cool_h > 0 else f"{cool_m} min"
                parts.append(f"AC will reach target in {cool_str}")

        elif method in (CoolingMethod.OPEN_WINDOW, CoolingMethod.KEEP_WINDOW_OPEN):
            if hours_to_cool is not None:
                parts.append(f"Natural ventilation will reach target in {hours_to_cool:.1f}h")

        elif method == CoolingMethod.NO_ACTION:
            pass  # Handled by _no_action_reasoning

        # --- Late warning ---
        if not achieves:
            parts.append(
                f"Target may not be reached by the deadline — consider starting earlier or using AC"
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

