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
    CLOSE_WINDOW = "close_window"


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
            CoolingMethod.CLOSE_WINDOW: "Close window",
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

        # Device availability — per-room config flags
        fan_available = current_conditions.get("fan_available", True)
        ac_available = current_conditions.get("ac_available", True)
        
        cooling_deficit = prediction.cooling_deficit
        hours_to_target = self._hours_from_conditions(current_conditions)
        tolerance_hours = tolerance_minutes / 60.0
        
        # --- Close-window check ---
        # If window is open but outdoor conditions make it counterproductive, say so
        # before evaluating cooling strategies.
        if window_open:
            close_reason = self._close_window_reason(
                indoor_temp, outdoor_temp, target_temp, aqi, cooling_deficit
            )
            if close_reason:
                return CoolingStrategy(
                    method=CoolingMethod.CLOSE_WINDOW,
                    timing="",
                    predicted_temp=prediction.predicted_bedtime_temp,
                    target_temp=target_temp,
                    reasoning=close_reason,
                    confidence=0.9,
                )

        # Check if we need cooling at all
        if cooling_deficit <= self.comfort_tolerance:
            return CoolingStrategy(
                method=CoolingMethod.NO_ACTION,
                timing="",
                predicted_temp=prediction.predicted_bedtime_temp,
                target_temp=target_temp,
                reasoning=self._no_action_reasoning(
                    indoor_temp, target_temp, outdoor_temp, ac_running, fan_running,
                    prediction.predicted_bedtime_temp, hours_to_target,
                ),
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
        if aqi_ok and fan_available:
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

        # Evaluate AC cooling (always evaluated unless AC is not available)
        if ac_available:
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

        # Nothing achieves target within tolerance
        if best_strategy is None:
            if strategies:
                # Fall back to best available option (last evaluated = AC if available,
                # else fan, else natural)
                best_strategy = strategies[-1]
            else:
                # No strategies at all (AQI too high, nothing available) — synthesise a
                # no-action placeholder so the rest of the code can run normally.
                no_prediction = self.thermal_model.predict_temperature(
                    current_conditions=current_conditions,
                    hours_ahead=hours_to_target,
                    cooling_strategy="natural",
                )
                best_strategy = {
                    "method": CoolingMethod.NO_ACTION,
                    "prediction": no_prediction,
                    "hours_to_cool": None,
                    "achieves_target": False,
                }

        # Adjust method based on current device states
        method = best_strategy["method"]
        if method == CoolingMethod.START_FAN and fan_running:
            method = CoolingMethod.CONTINUE_FAN
        elif method == CoolingMethod.START_AC and ac_running:
            method = CoolingMethod.CONTINUE_AC
        elif method == CoolingMethod.OPEN_WINDOW and window_open:
            method = CoolingMethod.KEEP_WINDOW_OPEN

        # --- Lazy-start timing ---
        # If we have buffer before we must act, tell the user when to start rather
        # than shouting "NOW!" for the entire afternoon.
        tolerance_hours = tolerance_minutes / 60.0
        hours_until_cool = best_strategy.get("hours_to_cool") or 0.0
        delay_budget = hours_to_target + tolerance_hours - hours_until_cool
        deferred_minutes = 15  # act if ≤15 min of budget left
        current_time: datetime = current_conditions.get("current_time", datetime.now())

        if best_strategy["achieves_target"] and delay_budget > (deferred_minutes / 60.0):
            start_by = current_time + timedelta(hours=delay_budget)
            # strftime %-I is Linux-only; strip leading zero manually for cross-platform
            hour_str = start_by.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
            timing = f"by {hour_str}"
        elif best_strategy["achieves_target"]:
            timing = "NOW!"
        elif not ac_available:
            timing = "— target temperature may not be reachable"
        else:
            timing = "LATE — target may not be reached"

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
            fan_available=fan_available,
            ac_available=ac_available,
        )

        return CoolingStrategy(
            method=method,
            timing=timing,
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

    def _close_window_reason(
        self,
        indoor_temp: float,
        outdoor_temp: float,
        target_temp: float,
        aqi: float,
        cooling_deficit: float,
    ) -> str | None:
        """Return a reason string if the open window should be closed, else None."""
        # Bad air quality — always close
        if aqi > self.aqi_threshold:
            return (
                f"Close window: AQI is {aqi:.0f} (above {self.aqi_threshold} threshold). "
                f"Switch to fan or AC for cooling."
            )
        # Outside warmer than inside — window is bringing heat in
        if outdoor_temp >= indoor_temp:
            return (
                f"Close window: outside ({outdoor_temp:.1f}°F) is at or warmer than inside "
                f"({indoor_temp:.1f}°F) — the window is adding heat, not removing it."
            )
        # Room is already at or well below target and outside is much colder — over-cooling risk
        if cooling_deficit <= self.comfort_tolerance and outdoor_temp < target_temp - 5.0:
            excess = indoor_temp - outdoor_temp
            return (
                f"Close window: room is already at target ({indoor_temp:.1f}°F) and outside "
                f"is {outdoor_temp:.1f}°F — {excess:.1f}°F colder than inside. "
                f"The room will over-cool without intervention."
            )
        return None

    def _no_action_reasoning(
        self,
        indoor_temp: float,
        target_temp: float,
        outdoor_temp: float,
        ac_running: bool,
        fan_running: bool,
        predicted_bedtime_temp: float | None = None,
        hours_to_target: float = 0.0,
    ) -> str:
        """Explain why no action is needed."""
        parts = []
        if indoor_temp <= target_temp:
            below_by = target_temp - indoor_temp
            parts.append(
                f"Room is already at {indoor_temp:.1f}°F, "
                f"{below_by:.1f}°F below target of {target_temp:.1f}°F"
            )
        else:
            parts.append(f"Room is {indoor_temp:.1f}°F, within comfort range of {target_temp:.1f}°F target")

        if ac_running:
            parts.append("AC is already running and keeping up")
        elif fan_running:
            parts.append("Fan is running effectively")
        elif outdoor_temp < indoor_temp:
            parts.append(f"Outside air ({outdoor_temp:.1f}°F) is cooler and providing passive cooling")

        # Warn only if the room is predicted to drop into genuinely cold territory
        cold_threshold = 64.0
        if (
            predicted_bedtime_temp is not None
            and hours_to_target > 0
            and predicted_bedtime_temp < cold_threshold
        ):
            h = int(hours_to_target)
            m = int((hours_to_target - h) * 60)
            time_str = f"{h}h {m}m" if h > 0 else f"{m} min"
            parts.append(
                f"Room is predicted to drop to ~{predicted_bedtime_temp:.0f}°F "
                f"in {time_str} — consider adding heat if that's too cold"
            )

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
        fan_available: bool = True,
        ac_available: bool = True,
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
        deficit = indoor_temp - target_temp
        h = int(hours_to_target)
        m = int((hours_to_target - h) * 60)
        time_str = f"{h}h {m}m" if h > 0 else f"{m} min"

        parts: list[str] = []

        # --- Extreme conditions note (>15°F gap is a large task) ---
        if deficit > 15:
            parts.append(
                f"Room is very hot ({indoor_temp:.0f}°F) — "
                f"cooling {deficit:.0f}°F in {time_str} is a large task"
            )
        else:
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
            elif not fan_available:
                parts.append("No fan available in this room — AC required for active cooling")
            else:
                if fan_strategy:
                    fan_h = fan_strategy.get("hours_to_cool")
                    if fan_h is None:
                        # After the step-hours fix, None means genuinely can't reach in 24h
                        if forecast_temps and min(forecast_temps) >= target_temp:
                            parts.append(
                                f"Outdoor air ({min(forecast_temps):.0f}°F min forecast) "
                                f"won't drop below target — fan/window cannot cool the room"
                            )
                        else:
                            parts.append(
                                f"Fan cannot cool the room to {target_temp:.0f}°F "
                                f"within 24 hours given current conditions"
                            )
                    elif not fan_strategy.get("achieves_target"):
                        parts.append(
                            f"Fan would take {_cool_time_str(fan_h)}, which exceeds the "
                            f"{tolerance_minutes}-minute tolerance — AC required"
                        )
            if hours_to_cool is not None:
                parts.append(f"AC will reach target in {_cool_time_str(hours_to_cool)}")
            elif not achieves:
                if forecast_temps and min(forecast_temps) >= target_temp:
                    parts.append(
                        f"Forecast low is {min(forecast_temps):.0f}°F — outdoor air stays "
                        f"above target, even AC may struggle"
                    )
                else:
                    parts.append(
                        f"AC cannot cool {indoor_temp:.0f}°F → {target_temp:.0f}°F "
                        f"({indoor_temp - target_temp:.0f}°F) within the available time"
                    )

        # --- Late warning / heat warning ---
        if not achieves:
            if not ac_available:
                # AC is not available — warn about predicted temperature instead
                predicted_temp = strategy["prediction"].predicted_bedtime_temp
                overshoot = predicted_temp - target_temp
                h = int(hours_to_target)
                m = int((hours_to_target - h) * 60)
                time_str = f"{h}h {m}m" if h > 0 else f"{m} min"
                parts.append(
                    f"Without AC, the room is predicted to reach "
                    f"{predicted_temp:.0f}°F by target time "
                    f"({overshoot:.0f}°F above the {target_temp:.0f}°F target)"
                )
                if predicted_temp >= 85:
                    parts.append(
                        "This may be unsafe — consider moving to a cooler area of the home "
                        "or taking other measures to manage the heat"
                    )
                elif predicted_temp >= 78:
                    parts.append(
                        "Consider moving to a cooler part of the home if available"
                    )
            else:
                # When outdoor is much colder than indoor and AQI is OK, opening windows
                # alongside AC gives free supplemental cooling
                outdoor_advantage = indoor_temp - outdoor_temp
                if aqi_ok and outdoor_advantage >= 8 and not window_open:
                    parts.append(
                        f"Opening windows (outside is {outdoor_advantage:.0f}°F cooler) "
                        f"alongside AC will significantly speed up cooling"
                    )
                parts.append(
                    "Target may not be reached by the deadline — "
                    "start AC earlier next time or lower the target temperature"
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

