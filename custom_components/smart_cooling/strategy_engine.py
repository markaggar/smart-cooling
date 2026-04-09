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
    # Forward-scan timing: hours from NOW until action must start, and run duration
    start_hours_from_now: float | None = None   # latest deadline to start (hours)
    strategy_hours_to_cool: float | None = None  # cooling duration once started

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for HA state attributes."""
        return {
            "method": self.method.value,
            "timing": self.timing,
            "predicted_temp": round(self.predicted_temp, 1),
            "target_temp": round(self.target_temp, 1),
            "reasoning": self.reasoning,
            "confidence": round(self.confidence, 2),
            "start_hours_from_now": round(self.start_hours_from_now, 2) if self.start_hours_from_now is not None else None,
            "strategy_hours_to_cool": round(self.strategy_hours_to_cool, 1) if self.strategy_hours_to_cool is not None else None,
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
        comfort_data: dict[str, Any] | None = None,
    ) -> CoolingStrategy:
        """Determine the best cooling strategy.

        Tolerance-aware priority: if a lower-energy method (fan/window) can reach
        target within target_time + tolerance_minutes, prefer it over AC.
        Priority: natural > fan > AC

        comfort_data (optional) describes the overnight comfort window:
          phase               – "pre_window" | "during_window" | None
          window_peak_temp    – predicted peak during the comfort window
          target_temp         – target (may be adjusted to required_start_temp pre-window)
          comfort_tolerance   – °F above target still considered comfortable
          prefer_ac           – bias toward AC for overnight maintenance
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
                indoor_temp, outdoor_temp, target_temp, aqi, cooling_deficit,
                wind_speed=wind_speed,
                predicted_open_temp=prediction.predicted_bedtime_temp,
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

        # Check if we need cooling at all.
        # Override NO_ACTION when the room is currently above target by >1°F and free
        # window cooling is available (outdoor below target, AQI ok, window closed).
        # Without this, "predicted to cool to 58°F in 24h through closed walls" would
        # suppress an obvious recommendation to just open the window now.
        _free_cooling_override = (
            indoor_temp > target_temp + 1.0
            and not window_open
            and aqi <= self.aqi_threshold
            and outdoor_temp < target_temp
            and (indoor_temp - outdoor_temp) >= self.min_temp_advantage
        )
        if cooling_deficit <= self.comfort_tolerance and not _free_cooling_override:
            return CoolingStrategy(
                method=CoolingMethod.NO_ACTION,
                timing="",
                predicted_temp=prediction.predicted_bedtime_temp,
                target_temp=target_temp,
                reasoning=self._no_action_reasoning(
                    indoor_temp, target_temp, outdoor_temp, ac_running, fan_running,
                    prediction.predicted_bedtime_temp, hours_to_target,
                    current_conditions,
                ),
                confidence=0.9,
            )
        
        # Check environmental conditions for natural/fan cooling
        temp_advantage = indoor_temp - outdoor_temp
        has_temp_advantage = temp_advantage >= self.min_temp_advantage
        aqi_ok = aqi <= self.aqi_threshold

        strategies = []

        # Passive no-action hourly temps used as the baseline for the forward scan.
        # This is the "no extra devices" trajectory — what happens if nothing changes.
        no_action_hourly = prediction.hourly_predictions

        # Evaluate natural cooling (open window, no fan).
        # Forward scan: find the LATEST hour at which opening the window can still
        # achieve target.  Only considers hours where outdoor is ≥ min_temp_advantage
        # cooler than the predicted indoor temp at that time.
        if aqi_ok:
            natural_result = self._find_latest_viable_start(
                "natural", current_conditions, no_action_hourly,
                hours_to_target, tolerance_hours, check_outdoor_advantage=True,
            )
            natural_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
                cooling_strategy="natural",
            )
            strategies.append({
                "method": CoolingMethod.OPEN_WINDOW,
                "prediction": natural_prediction,
                "hours_to_cool": natural_result["hours_to_cool"] if natural_result else None,
                "start_hours_from_now": natural_result["start_hours_from_now"] if natural_result else None,
                "achieves_target": natural_result is not None,
            })

        # Evaluate fan cooling — same forward scan with outdoor advantage check.
        if aqi_ok and fan_available:
            fan_result = self._find_latest_viable_start(
                "fan", current_conditions, no_action_hourly,
                hours_to_target, tolerance_hours, check_outdoor_advantage=True,
            )
            fan_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
                cooling_strategy="fan",
            )
            strategies.append({
                "method": CoolingMethod.START_FAN,
                "prediction": fan_prediction,
                "hours_to_cool": fan_result["hours_to_cool"] if fan_result else None,
                "start_hours_from_now": fan_result["start_hours_from_now"] if fan_result else None,
                "achieves_target": fan_result is not None,
            })

        # Evaluate AC cooling — no outdoor advantage requirement; AC works any time.
        if ac_available:
            ac_result = self._find_latest_viable_start(
                "ac", current_conditions, no_action_hourly,
                hours_to_target, tolerance_hours, check_outdoor_advantage=False,
            )
            ac_prediction = self.thermal_model.predict_temperature(
                current_conditions=current_conditions,
                hours_ahead=hours_to_target,
                cooling_strategy="ac",
            )
            strategies.append({
                "method": CoolingMethod.START_AC,
                "prediction": ac_prediction,
                "hours_to_cool": ac_result["hours_to_cool"] if ac_result else None,
                "start_hours_from_now": ac_result["start_hours_from_now"] if ac_result else None,
                "achieves_target": ac_result is not None,
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

        # --- Comfort window adjustments ---
        # Override strategy and add context note when overnight comfort data is available.
        comfort_note: str | None = None

        if comfort_data:
            phase = comfort_data.get("phase")
            comfort_tolerance_val = float(comfort_data.get("comfort_tolerance", 2.0))
            window_peak_temp = comfort_data.get("window_peak_temp")
            prefer_ac = comfort_data.get("prefer_ac", True) and ac_available
            required_start_temp = comfort_data.get("required_start_temp")
            comfort_end_label = comfort_data.get("comfort_end_label", "wake time")

            if phase == "pre_window" and required_start_temp is not None and window_peak_temp is not None:
                overshoot = window_peak_temp - (target_temp + comfort_tolerance_val)
                if overshoot > 0:
                    comfort_note = (
                        f"Comfort window: pre-cool to {required_start_temp:.1f}°F before "
                        f"target time to prevent overnight peak of ~{window_peak_temp:.1f}°F"
                    )
                else:
                    comfort_note = (
                        f"Comfort window: predicted overnight peak {window_peak_temp:.1f}°F "
                        f"is within {comfort_tolerance_val:.1f}°F of target — "
                        f"no extra pre-cooling needed"
                    )

            elif phase == "during_window" and window_peak_temp is not None:
                will_exceed = window_peak_temp > target_temp + comfort_tolerance_val
                if not will_exceed:
                    comfort_note = (
                        f"Comfort window: predicted peak {window_peak_temp:.1f}°F stays within "
                        f"{comfort_tolerance_val:.1f}°F of {target_temp:.0f}°F target "
                        f"through {comfort_end_label}"
                    )
                else:
                    overshoot = window_peak_temp - (target_temp + comfort_tolerance_val)
                    if prefer_ac:
                        if method not in (CoolingMethod.START_AC, CoolingMethod.CONTINUE_AC):
                            method = CoolingMethod.CONTINUE_AC if ac_running else CoolingMethod.START_AC
                        comfort_note = (
                            f"Comfort window: predicted peak {window_peak_temp:.1f}°F exceeds "
                            f"target by {overshoot:.1f}°F — running AC to maintain comfort "
                            f"through {comfort_end_label}"
                        )
                    else:
                        # Fan/window preferred overnight (quiet or no AC)
                        if method == CoolingMethod.NO_ACTION:
                            if fan_available and aqi_ok:
                                method = CoolingMethod.CONTINUE_FAN if fan_running else CoolingMethod.START_FAN
                            elif aqi_ok:
                                method = CoolingMethod.KEEP_WINDOW_OPEN if window_open else CoolingMethod.OPEN_WINDOW
                        if overshoot > 3.0:
                            comfort_note = (
                                f"Comfort window: predicted peak {window_peak_temp:.1f}°F is "
                                f"{overshoot:.1f}°F above comfort range — fan/window may be "
                                f"insufficient; consider enabling AC for overnight maintenance"
                            )
                        else:
                            comfort_note = (
                                f"Comfort window: predicted peak {window_peak_temp:.1f}°F is "
                                f"{overshoot:.1f}°F above comfort range — using fan/window "
                                f"to reduce overnight temperature"
                            )

        # --- Fan requires window open note ---
        # Window fans must work with an open window.  When recommending START_FAN but
        # the window is currently closed, add a reminder to open it too.
        fan_window_note: str | None = None
        if method in (CoolingMethod.START_FAN,) and not window_open:
            fan_window_note = "open window and start fan"

        # --- Timing based on latest viable start from forward scan ---
        # start_hours_from_now: hours until the user's action deadline.
        # If > 15 min away → tell the user when to start (deferred).
        # If ≤ 15 min → act NOW!
        start_hours = best_strategy.get("start_hours_from_now", 0.0) or 0.0
        deferred_minutes = 15  # act if ≤ 15 min until deadline
        current_time: datetime = current_conditions.get("current_time", datetime.now())

        if best_strategy["achieves_target"] and start_hours > (deferred_minutes / 60.0):
            start_by = current_time + timedelta(hours=start_hours)
            hour_str = start_by.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
            timing = f"by {hour_str}"
            if fan_window_note:
                timing = f"{fan_window_note} by {hour_str}"
        elif best_strategy["achieves_target"]:
            timing = f"{fan_window_note} NOW!" if fan_window_note else "NOW!"
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
        if comfort_note:
            reasoning = reasoning.rstrip(".") + ". " + comfort_note + "."

        return CoolingStrategy(
            method=method,
            timing=timing,
            predicted_temp=best_strategy["prediction"].predicted_bedtime_temp,
            target_temp=target_temp,
            reasoning=reasoning,
            confidence=0.7 if best_strategy["achieves_target"] else 0.4,
            start_hours_from_now=best_strategy.get("start_hours_from_now"),
            strategy_hours_to_cool=best_strategy.get("hours_to_cool"),
            alternatives=[
                {
                    "method": s["method"].value,
                    "predicted_temp": round(s["prediction"].predicted_bedtime_temp, 1),
                    "hours_to_cool": round(s["hours_to_cool"], 1) if s["hours_to_cool"] is not None else None,
                    "start_hours_from_now": round(s["start_hours_from_now"], 2) if s["start_hours_from_now"] is not None else None,
                    "achieves_target": s["achieves_target"],
                    "chosen": s is best_strategy,
                }
                for s in strategies
            ],
        )

    def _close_window_reason(
        self,
        indoor_temp: float,
        outdoor_temp: float,
        target_temp: float,
        aqi: float,
        cooling_deficit: float,
        wind_speed: float = 0.0,
        predicted_open_temp: float | None = None,
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
            # Explain what is driving the predicted cool-down: wind or walls
            if wind_speed < 2.0:
                wind_note = (
                    f" Wind is calm ({wind_speed:.1f} mph) — cool-down is mainly through "
                    f"wall conduction, not the window itself."
                )
            elif wind_speed < 5.0:
                wind_note = (
                    f" Light wind ({wind_speed:.1f} mph) is contributing some air exchange "
                    f"through the window."
                )
            else:
                wind_note = (
                    f" Wind at {wind_speed:.1f} mph is actively pushing cold outside air "
                    f"through the window."
                )
            temp_note = (
                f" Room is predicted to reach {predicted_open_temp:.0f}°F with window open."
                if predicted_open_temp is not None else ""
            )
            return (
                f"Close window: room is already at target ({indoor_temp:.1f}°F) and outside "
                f"is {outdoor_temp:.1f}°F — {excess:.1f}°F colder than inside."
                f"{temp_note}{wind_note}"
            )
        return None

    def _format_target_time(self, conditions: dict[str, Any]) -> str:
        """Return a stable 'H:MM AM/PM' label for the target time (no per-minute churn)."""
        target_time_str = conditions.get("target_time", conditions.get("bedtime", ""))
        current_time: datetime = conditions.get("current_time", datetime.now())
        try:
            target_time = datetime.strptime(target_time_str, "%H:%M:%S").time()
            target_dt = current_time.replace(
                hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
            )
            if target_dt < current_time:
                target_dt += timedelta(days=1)
            hour12 = target_dt.hour % 12 or 12
            ampm = "AM" if target_dt.hour < 12 else "PM"
            return f"{hour12}:{target_dt.minute:02d} {ampm}"
        except (ValueError, AttributeError):
            return "target time"

    def _no_action_reasoning(
        self,
        indoor_temp: float,
        target_temp: float,
        outdoor_temp: float,
        ac_running: bool,
        fan_running: bool,
        predicted_bedtime_temp: float | None = None,
        hours_to_target: float = 0.0,
        conditions: dict[str, Any] | None = None,
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

        window_open = conditions.get("window_open", False) if conditions else False
        window_sensor_configured = conditions.get("window_sensor_configured", True) if conditions else True
        if ac_running:
            parts.append("AC is already running and keeping up")
        elif fan_running:
            parts.append("Fan is running effectively")
        elif outdoor_temp < indoor_temp:
            if not window_sensor_configured:
                # No window sensor — don't claim to know whether window is open or closed
                diff = indoor_temp - outdoor_temp
                parts.append(
                    f"Outside is {outdoor_temp:.1f}°F ({diff:.1f}°F cooler — "
                    f"room is cooling slowly through walls)"
                )
            elif window_open:
                parts.append(f"Outside air ({outdoor_temp:.1f}°F) is cooler and providing passive cooling")
            else:
                diff = indoor_temp - outdoor_temp
                parts.append(
                    f"Outside is {outdoor_temp:.1f}°F ({diff:.1f}°F cooler, "
                    f"but window is closed — cooling slowly through walls only)"
                )

        # Warn only if the room is predicted to drop into genuinely cold territory
        cold_threshold = 64.0
        if (
            predicted_bedtime_temp is not None
            and hours_to_target > 0
            and predicted_bedtime_temp < cold_threshold
        ):
            target_label = self._format_target_time(conditions) if conditions else "target time"
            parts.append(
                f"Room is predicted to drop to ~{predicted_bedtime_temp:.0f}°F "
                f"by {target_label} — consider adding heat if that's too cold"
            )

        return ". ".join(parts) + "."

    def _find_latest_viable_start(
        self,
        strategy: str,
        current_conditions: dict[str, Any],
        no_action_hourly: list[dict[str, Any]],
        hours_to_target: float,
        tolerance_hours: float,
        check_outdoor_advantage: bool,
    ) -> dict[str, Any] | None:
        """Find the latest time (hours from now) at which starting this strategy
        can still reach target by target_time + tolerance.

        Scans forward one hour at a time using the no-action temperature trajectory
        as the starting point for each trial.  For fan/window strategies, skips
        hours where outdoor air is not cool enough vs predicted indoor temp.

        Returns {"start_hours_from_now": float, "hours_to_cool": float} or None
        if target is unreachable at any start time.
        """
        now: datetime = current_conditions.get("current_time", datetime.now())
        forecast = current_conditions.get("forecast", [])
        latest_viable: dict[str, Any] | None = None

        for i, pred in enumerate(no_action_hourly):
            hours_offset = float(i)
            remaining = hours_to_target - hours_offset
            if remaining <= 0:
                break

            indoor_at_offset = pred["predicted_temp"]
            future_time = now + timedelta(hours=hours_offset)

            if check_outdoor_advantage:
                fcast = self.thermal_model._get_forecast_for_hour(forecast, future_time)
                outdoor_at_offset = fcast.get(
                    "temperature", current_conditions.get("outdoor_temp", 70.0)
                )
                if indoor_at_offset - outdoor_at_offset < self.min_temp_advantage:
                    continue  # Outside not cool enough to bother at this hour

            shifted = dict(current_conditions)
            shifted["indoor_temp"] = indoor_at_offset
            shifted["current_time"] = future_time

            h = self.thermal_model.find_hours_to_cool_to_target(
                shifted, strategy, max_hours=remaining + tolerance_hours
            )
            if h is not None and h <= remaining + tolerance_hours:
                # Viable — keep scanning to find the LATEST possible start
                latest_viable = {
                    "start_hours_from_now": hours_offset,
                    "hours_to_cool": h,
                }

        return latest_viable

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
        target_time_label = self._format_target_time(conditions)

        parts: list[str] = []

        # --- Extreme conditions note (>15°F gap is a large task) ---
        if deficit > 15:
            parts.append(
                f"Room is very hot ({indoor_temp:.0f}°F) — "
                f"cooling {deficit:.0f}°F by {target_time_label} is a large task"
            )
        else:
            parts.append(
                f"Room is {indoor_temp:.1f}°F, needs to reach {target_temp:.1f}°F "
                f"({deficit:.1f}°F drop) by {target_time_label}"
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
            # Round to nearest 15 minutes to reduce sensor update churn
            rounded = round(hrs / 0.25) * 0.25
            ch = int(rounded)
            cm = round((rounded - ch) * 60)
            if cm == 60:
                ch += 1
                cm = 0
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
                start_offset = strategy.get("start_hours_from_now", 0.0) or 0.0
                if start_offset > 0.25:
                    fan_start_time = current_time + timedelta(hours=start_offset)
                    h_str = fan_start_time.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
                    parts.append(
                        f"Fan can start as late as {h_str} and still reach target "
                        f"({_cool_time_str(hours_to_cool)} run time from that point)"
                    )
                else:
                    parts.append(f"Fan will reach target in {_cool_time_str(hours_to_cool)}")
            if tolerance_minutes > 0 and achieves:
                parts.append(
                    f"Fan preferred over AC to save energy"
                )
            if ac_strategy and ac_strategy.get("hours_to_cool") is not None:
                ac_start = ac_strategy.get("start_hours_from_now")
                if ac_start is not None and ac_start > 0.25:
                    ac_start_time = current_time + timedelta(hours=ac_start)
                    h_str = ac_start_time.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
                    parts.append(f"AC also viable (start by {h_str}) but not needed")
                else:
                    ac_h = ac_strategy["hours_to_cool"]
                    parts.append(f"AC would be faster ({ac_h:.1f}h) but not needed")

        elif method in (CoolingMethod.START_AC, CoolingMethod.CONTINUE_AC):
            # Explain why lower-energy options were rejected
            if not aqi_ok:
                parts.append("Air quality prevents opening windows or using fan")
            elif not fan_available:
                parts.append("No fan available in this room — AC required for active cooling")
            else:
                if fan_strategy and not fan_strategy.get("achieves_target"):
                    # forward scan found no viable start window for fan
                    if forecast_temps and min(forecast_temps) >= target_temp:
                        parts.append(
                            f"Outdoor air ({min(forecast_temps):.0f}°F min forecast) "
                            f"won't drop below target — fan/window cannot cool the room"
                        )
                    else:
                        parts.append(
                            f"Fan cannot reach {target_temp:.0f}°F within the available "
                            f"time window — AC required"
                        )
            if hours_to_cool is not None:
                start_offset = strategy.get("start_hours_from_now", 0.0) or 0.0
                if start_offset > 0.25:
                    ac_start_time = current_time + timedelta(hours=start_offset)
                    h_str = ac_start_time.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
                    parts.append(
                        f"AC can start as late as {h_str} and reach target "
                        f"({_cool_time_str(hours_to_cool)} run time)"
                    )
                else:
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

