"""Strategy engine for cooling recommendations."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
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
    ) -> CoolingStrategy:
        """Determine the best cooling strategy.
        
        Priority order:
        1. Natural cooling (windows) if conditions allow
        2. Fan cooling if temp advantage exists
        3. AC if other methods insufficient
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
        hours_to_bedtime = self._hours_from_conditions(current_conditions)
        
        # Check if we need cooling at all
        if cooling_deficit <= self.comfort_tolerance:
            return CoolingStrategy(
                method=CoolingMethod.NO_ACTION,
                timing="",
                predicted_temp=prediction.predicted_bedtime_temp,
                target_temp=target_temp,
                reasoning="Temperature within comfort range",
                confidence=0.9,
            )
        
        # Check environmental conditions for natural/fan cooling
        temp_advantage = indoor_temp - outdoor_temp
        has_temp_advantage = temp_advantage >= self.min_temp_advantage
        aqi_ok = aqi <= self.aqi_threshold
        
        strategies = []
        
        # Evaluate natural cooling (window only)
        if has_temp_advantage and aqi_ok:
            natural_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_bedtime,
                cooling_strategy=None,  # Natural means no active cooling modeled
            )
            strategies.append({
                "method": CoolingMethod.OPEN_WINDOW,
                "prediction": natural_prediction,
                "achieves_target": natural_prediction.cooling_deficit <= self.comfort_tolerance,
            })
        
        # Evaluate fan cooling
        if has_temp_advantage and aqi_ok:
            fan_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_bedtime,
                cooling_strategy="fan",
            )
            strategies.append({
                "method": CoolingMethod.START_FAN,
                "prediction": fan_prediction,
                "achieves_target": fan_prediction.cooling_deficit <= self.comfort_tolerance,
            })
        
        # Evaluate AC cooling
        ac_prediction = self.thermal_model.predict_temperature(
            current_conditions=current_conditions,
            hours_ahead=hours_to_bedtime,
            cooling_strategy="ac",
        )
        strategies.append({
            "method": CoolingMethod.START_AC,
            "prediction": ac_prediction,
            "achieves_target": ac_prediction.cooling_deficit <= self.comfort_tolerance,
        })
        
        # Select best strategy (prefer energy efficiency)
        # Priority: natural > fan > AC
        best_strategy = None
        for strategy in strategies:
            if strategy["achieves_target"]:
                best_strategy = strategy
                break
        
        # If nothing achieves target, use AC as fallback
        if best_strategy is None:
            best_strategy = strategies[-1]  # AC is always last
        
        # Adjust method based on current device states
        method = best_strategy["method"]
        if method == CoolingMethod.START_FAN and fan_running:
            method = CoolingMethod.CONTINUE_FAN
        elif method == CoolingMethod.START_AC and ac_running:
            method = CoolingMethod.CONTINUE_AC
        elif method == CoolingMethod.OPEN_WINDOW and window_open:
            method = CoolingMethod.KEEP_WINDOW_OPEN
        
        return CoolingStrategy(
            method=method,
            timing="NOW!" if best_strategy["achieves_target"] else self._calculate_timing(current_conditions),
            predicted_temp=best_strategy["prediction"].predicted_bedtime_temp,
            target_temp=target_temp,
            reasoning=self._generate_reasoning(method, current_conditions, best_strategy),
            confidence=0.7 if best_strategy["achieves_target"] else 0.5,
            alternatives=[
                {
                    "method": s["method"].value,
                    "predicted_temp": s["prediction"].predicted_bedtime_temp,
                    "achieves_target": s["achieves_target"],
                }
                for s in strategies
                if s != best_strategy
            ],
        )

    def _hours_from_conditions(self, conditions: dict[str, Any]) -> float:
        """Extract hours to bedtime from conditions."""
        # This should match coordinator's calculation
        bedtime_str = conditions.get("bedtime", "22:30:00")
        current_time = conditions.get("current_time", datetime.now())
        
        try:
            bedtime = datetime.strptime(bedtime_str, "%H:%M:%S").time()
            bedtime_today = current_time.replace(
                hour=bedtime.hour, minute=bedtime.minute, second=0, microsecond=0
            )
            if bedtime_today < current_time:
                from datetime import timedelta
                bedtime_today += timedelta(days=1)
            return (bedtime_today - current_time).total_seconds() / 3600
        except ValueError:
            return 8.0

    def _calculate_timing(self, conditions: dict[str, Any]) -> str:
        """Calculate when cooling should start."""
        # For now, always recommend NOW if cooling is needed
        # More sophisticated timing can be added later
        return "NOW!"

    def _generate_reasoning(
        self,
        method: CoolingMethod,
        conditions: dict[str, Any],
        strategy: dict[str, Any],
    ) -> str:
        """Generate human-readable reasoning for the recommendation."""
        reasons = {
            CoolingMethod.NO_ACTION: "Temperature comfortable",
            CoolingMethod.OPEN_WINDOW: "Outside cooler than inside, natural cooling sufficient",
            CoolingMethod.START_FAN: "Fan cooling available and efficient",
            CoolingMethod.CONTINUE_FAN: "Fan already running effectively",
            CoolingMethod.START_AC: "AC required to reach target temperature",
            CoolingMethod.CONTINUE_AC: "AC already running, continue for target",
            CoolingMethod.KEEP_WINDOW_OPEN: "Window open, natural cooling working",
        }
        
        base_reason = reasons.get(method, "Cooling recommended")
        
        if not strategy["achieves_target"]:
            base_reason += f" (may not fully reach target)"
        
        return base_reason
