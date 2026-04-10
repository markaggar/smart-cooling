"""
Smart Cooling — Scenario Simulation Tool
=========================================
Runs strategy evaluation against synthetic or real-sensor-based scenarios
using the EXACT same code the HA integration runs.  No HA instance required.

Usage:
  python scripts/simulate_scenario.py                        # run all built-in scenarios
  python scripts/simulate_scenario.py scenarios/cool_eve.yaml
  python scripts/simulate_scenario.py --params data/params_master_bedroom.json scenarios/*.yaml
  python scripts/simulate_scenario.py --list                 # show available built-in scenarios

Options:
  --params FILE    Override physics params with learned values from HA storage export
  --list           List available built-in scenario names and exit
  --scenario NAME  Run one named built-in scenario (partial match, case-insensitive)
  FILE             Path(s) to YAML scenario files (glob supported)

Output anatomy:
  ┌── SCENARIO header ──────────────────────────────────────┐
  │  Inputs + device availability                           │
  │  NO-ACTION TRAJECTORY (what happens if nothing changes) │
  │    Per-hour: indoor temp, outdoor temp, differential,   │
  │    fan viability at that hour, and hours_to_cool        │
  │  STRATEGY EVALUATION (all three strategies)             │
  │    latest start, hours_to_cool, achieves?               │
  │  RECOMMENDATION (method, timing, reasoning)             │
  │  ALTERNATIVES table                                     │
  │  VERDICT (PASS/FAIL vs expected outcome)                │
  └─────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: load integration modules by file path to avoid executing
# __init__.py (which has homeassistant imports we don't want here).
# We create a stub package so relative imports inside the modules resolve.
# ---------------------------------------------------------------------------
import importlib
import importlib.util
import types

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INTEGRATION_DIR = _REPO_ROOT / "custom_components" / "smart_cooling"

# Suppress HA-logger chatter inside the integration modules.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

try:
    import yaml  # PyYAML — present in HA venv; `pip install pyyaml` otherwise
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_module(name: str, rel_path: str) -> types.ModuleType:
    """Load a module by file path under a stub package so relative imports work."""
    pkg_name = "smart_cooling_sim"
    # Ensure the stub package entry exists in sys.modules
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_INTEGRATION_DIR)]  # type: ignore[attr-defined]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg

    full_name = f"{pkg_name}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(
        full_name,
        _INTEGRATION_DIR / rel_path,
        submodule_search_locations=[],
    )
    assert spec and spec.loader, f"Could not locate {rel_path}"
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    sys.modules[full_name] = mod
    # Also register as the bare import name so cross-module relative imports resolve
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_const_mod = _load_module("const", "const.py")
_thermal_mod = _load_module("thermal_model", "thermal_model.py")
_strategy_mod = _load_module("strategy_engine", "strategy_engine.py")

DEFAULT_PHYSICS_PARAMS = _const_mod.DEFAULT_PHYSICS_PARAMS  # type: ignore[attr-defined]
ThermalModel = _thermal_mod.ThermalModel  # type: ignore[attr-defined]
StrategyEngine = _strategy_mod.StrategyEngine  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# ANSI colour helpers (skip on Windows if not supported)
# ---------------------------------------------------------------------------
import os as _os

_USE_COLOR = sys.stdout.isatty() and _os.name != "nt" or _os.environ.get("FORCE_COLOR")

def _c(code: str, text: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text

GREEN   = lambda t: _c("32", t)
RED     = lambda t: _c("31", t)
YELLOW  = lambda t: _c("33", t)
CYAN    = lambda t: _c("36", t)
BOLD    = lambda t: _c("1",  t)
DIM     = lambda t: _c("2",  t)

# ---------------------------------------------------------------------------
# Built-in scenarios
# ---------------------------------------------------------------------------
# Forecast entry shape mirrors what get_forecasts returns after coordinator
# normalisation: {datetime, temperature, wind_speed, precipitation, humidity}

def _forecast_ramp(
    start_hour: int,
    start_temp: float,
    end_temp: float,
    hours: int,
    wind_speed: float = 5.0,
    humidity: float = 55.0,
    base_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build a synthetic hourly forecast with a linear temperature ramp."""
    if base_date is None:
        base_date = datetime.now().replace(minute=0, second=0, microsecond=0)
    entries = []
    for i in range(hours):
        h = (start_hour + i) % 24
        dt = base_date.replace(hour=h) + timedelta(days=(start_hour + i) // 24)
        frac = i / max(hours - 1, 1)
        temp = start_temp + frac * (end_temp - start_temp)
        entries.append({
            "datetime": dt.isoformat(),
            "temperature": round(temp, 1),
            "wind_speed": wind_speed,
            "precipitation": 0.0,
            "humidity": humidity,
        })
    return entries


def _now_at(hour: int, minute: int = 0) -> datetime:
    """Return today's date at the given hour:minute, with seconds=0."""
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


BUILTIN_SCENARIOS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # 1. Cool evening — fan should wait until outdoor drops enough
    # ------------------------------------------------------------------
    {
        "name": "Cool evening — fan deferred to 9 PM",
        "description": (
            "Room 68°F, outdoor 65°F now but dropping to 53°F by 10 PM. "
            "Target 63°F by 10 PM. Fan should be deferred, not AC."
        ),
        "conditions": {
            "indoor_temp": 68.0,
            "outdoor_temp": 65.0,
            "outdoor_humidity": 55.0,
            "aqi": 42.0,
            "wind_speed": 5.0,
            "target_temp": 63.0,
            "target_time": "22:00:00",
            "current_time": _now_at(16, 0),
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "fan_available": True,
            "ac_available": True,
            "fan_sensor_configured": True,
            "ac_sensor_configured": True,
            "window_sensor_configured": True,
            "ac_setpoint": 83.0,
            "forecast": _forecast_ramp(16, 65.0, 53.0, hours=8),
        },
        "expected_method": "start_fan",
        "expected_timing_contains": "by",   # deferred, not NOW!
    },
    # ------------------------------------------------------------------
    # 2. Hot day — AC required immediately
    # ------------------------------------------------------------------
    {
        "name": "Hot afternoon — AC required now",
        "description": (
            "Room 82°F at 4 PM, outdoor 90°F and staying hot. "
            "Target 72°F by 10 PM.  Fan/window can't help — AC needed."
        ),
        "conditions": {
            "indoor_temp": 82.0,
            "outdoor_temp": 90.0,
            "outdoor_humidity": 40.0,
            "aqi": 55.0,
            "wind_speed": 3.0,
            "target_temp": 72.0,
            "target_time": "22:00:00",
            "current_time": _now_at(16, 0),
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "fan_available": True,
            "ac_available": True,
            "fan_sensor_configured": True,
            "ac_sensor_configured": True,
            "window_sensor_configured": True,
            "ac_setpoint": 83.0,
            "forecast": _forecast_ramp(16, 90.0, 78.0, hours=8),
        },
        "expected_method": "start_ac",
        "expected_timing_contains": None,  # any timing acceptable
    },
    # ------------------------------------------------------------------
    # 3. Already cool — no action needed
    # ------------------------------------------------------------------
    {
        "name": "Room already at target — no action",
        "description": (
            "Room 63°F, target 65°F.  Room is already below target. "
            "Should recommend no action."
        ),
        "conditions": {
            "indoor_temp": 63.0,
            "outdoor_temp": 58.0,
            "outdoor_humidity": 60.0,
            "aqi": 30.0,
            "wind_speed": 7.0,
            "target_temp": 65.0,
            "target_time": "22:00:00",
            "current_time": _now_at(20, 0),
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "fan_available": True,
            "ac_available": True,
            "fan_sensor_configured": True,
            "ac_sensor_configured": True,
            "window_sensor_configured": True,
            "ac_setpoint": 83.0,
            "forecast": _forecast_ramp(20, 58.0, 55.0, hours=4),
        },
        "expected_method": "no_action",
        "expected_timing_contains": None,
    },
    # ------------------------------------------------------------------
    # 4. Bad AQI — AC required even though outdoor is cooler
    # ------------------------------------------------------------------
    {
        "name": "Poor air quality — AC despite cool outdoor",
        "description": (
            "Room 72°F, outdoor 60°F (cool enough for fan), but AQI=160 (smoky). "
            "Fan/window blocked by AQI — AC should be recommended."
        ),
        "conditions": {
            "indoor_temp": 72.0,
            "outdoor_temp": 60.0,
            "outdoor_humidity": 45.0,
            "aqi": 160.0,
            "wind_speed": 6.0,
            "target_temp": 65.0,
            "target_time": "22:00:00",
            "current_time": _now_at(18, 0),
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "fan_available": True,
            "ac_available": True,
            "fan_sensor_configured": True,
            "ac_sensor_configured": True,
            "window_sensor_configured": True,
            "ac_setpoint": 83.0,
            "forecast": _forecast_ramp(18, 60.0, 55.0, hours=6),
        },
        "expected_method": "start_ac",
        "expected_timing_contains": None,
    },
    # ------------------------------------------------------------------
    # 5. No AC available — natural or fan, whichever works
    # ------------------------------------------------------------------
    {
        "name": "No AC available — fan only option",
        "description": (
            "Room 74°F, outdoor 62°F and dropping.  AC not available. "
            "Fan or natural ventilation should handle it.  "
            "open_window is preferred if natural alone achieves target."
        ),
        "conditions": {
            "indoor_temp": 74.0,
            "outdoor_temp": 62.0,
            "outdoor_humidity": 50.0,
            "aqi": 40.0,
            "wind_speed": 8.0,
            "target_temp": 68.0,
            "target_time": "22:00:00",
            "current_time": _now_at(18, 0),
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "fan_available": True,
            "ac_available": False,
            "fan_sensor_configured": True,
            "ac_sensor_configured": False,
            "window_sensor_configured": True,
            "ac_setpoint": None,
            "forecast": _forecast_ramp(18, 62.0, 55.0, hours=6),
        },
        # open_window is correct: natural ventilation alone reaches 68°F (6°F drop
        # in 4h), so fan is unnecessary.  Any passive or active strategy that
        # achieves target is acceptable — check achieves_target rather than method.
        "expected_method": "open_window",
        "expected_timing_contains": None,
    },
    # ------------------------------------------------------------------
    # 6. Close call — tight window, fan only viable at last hour
    # ------------------------------------------------------------------
    {
        "name": "Tight window — fan barely viable at last hour",
        "description": (
            "Room 70°F at 8 PM, 2h window to 10 PM target 65°F. "
            "Outdoor drops to 52°F by 9 PM.  Fan should succeed."
        ),
        "conditions": {
            "indoor_temp": 70.0,
            "outdoor_temp": 68.0,
            "outdoor_humidity": 55.0,
            "aqi": 45.0,
            "wind_speed": 5.0,
            "target_temp": 65.0,
            "target_time": "22:00:00",
            "current_time": _now_at(20, 0),
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "fan_available": True,
            "ac_available": True,
            "fan_sensor_configured": True,
            "ac_sensor_configured": True,
            "window_sensor_configured": True,
            "ac_setpoint": 83.0,
            "forecast": _forecast_ramp(20, 68.0, 52.0, hours=4),
        },
        "expected_method": "start_fan",
        "expected_timing_contains": None,
    },
]


# ---------------------------------------------------------------------------
# Diagnostic wrapper around _find_latest_viable_start
# ---------------------------------------------------------------------------

def _run_scan_with_trace(
    engine: StrategyEngine,
    strategy_name: str,
    current_conditions: dict[str, Any],
    no_action_hourly: list[dict[str, Any]],
    hours_to_target: float,
    tolerance_hours: float,
    check_outdoor_advantage: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Run _find_latest_viable_start and collect a per-hour trace.

    Returns (result, trace) where trace is a list of dicts for display.
    """
    from datetime import timedelta as _td

    now: datetime = current_conditions.get("current_time", datetime.now())
    forecast = current_conditions.get("forecast", [])
    latest_viable = None
    trace = []

    for i, pred in enumerate(no_action_hourly):
        hours_offset = float(i)
        remaining = hours_to_target - hours_offset
        if remaining <= 0:
            break

        indoor_at = pred["predicted_temp"]
        future_time = now + _td(hours=hours_offset)
        outdoor_at = None
        advantage = None
        skipped_reason = None

        if check_outdoor_advantage:
            fcast = engine.thermal_model._get_forecast_for_hour(forecast, future_time)
            outdoor_at = fcast.get("temperature", current_conditions.get("outdoor_temp", 70.0))
            advantage = indoor_at - outdoor_at
            if advantage < engine.min_temp_advantage:
                skipped_reason = f"diff {advantage:+.1f}°F < {engine.min_temp_advantage}°F threshold"
        else:
            fcast = engine.thermal_model._get_forecast_for_hour(forecast, future_time)
            outdoor_at = fcast.get("temperature", current_conditions.get("outdoor_temp", 70.0))
            advantage = indoor_at - outdoor_at

        if skipped_reason:
            trace.append({
                "hour_offset": i,
                "time": future_time,
                "indoor": indoor_at,
                "outdoor": outdoor_at,
                "advantage": advantage,
                "viable": False,
                "skip_reason": skipped_reason,
                "hours_to_cool": None,
                "remaining": remaining,
                "is_latest": False,
            })
            continue

        shifted = dict(current_conditions)
        shifted["indoor_temp"] = indoor_at
        shifted["current_time"] = future_time
        h = engine.thermal_model.find_hours_to_cool_to_target(
            shifted, strategy_name, max_hours=remaining + tolerance_hours
        )

        viable = h is not None and h <= remaining + tolerance_hours
        if viable:
            latest_viable = {"start_hours_from_now": hours_offset, "hours_to_cool": h}

        trace.append({
            "hour_offset": i,
            "time": future_time,
            "indoor": indoor_at,
            "outdoor": outdoor_at,
            "advantage": advantage,
            "viable": viable,
            "skip_reason": None,
            "hours_to_cool": h,
            "remaining": remaining,
            "is_latest": False,
        })

    # Mark the latest viable entry
    for row in reversed(trace):
        if row["viable"]:
            row["is_latest"] = True
            break

    return latest_viable, trace


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0") or "12:00 AM"


def _fmt_hours(h: float | None) -> str:
    if h is None:
        return "--"
    total_min = int(round(h * 60))
    hh, mm = divmod(total_min, 60)
    if hh:
        return f"{hh}h {mm:02d}m" if mm else f"{hh}h"
    return f"{mm}m"


def _print_separator(char: str = "-", width: int = 72) -> None:
    print(char * width)


def _print_header(text: str, width: int = 72) -> None:
    pad = max(0, width - len(text) - 4)
    print(BOLD(f"-- {text} " + "-" * pad))


def _print_scan_table(
    strategy_label: str,
    trace: list[dict[str, Any]],
    check_advantage: bool,
) -> None:
    print(f"\n  {CYAN(strategy_label.upper())} forward scan:")
    header = f"  {'Time':>7}  {'Indoor':>7}  {'Outdoor':>8}  {'Diff':>6}  {'h_to_cool':>10}  {'Remaining':>10}  Status"
    print(DIM(header))
    for row in trace:
        t_str = _fmt_time(row["time"])
        indoor_s = f"{row['indoor']:.1f}°F"
        outdoor_s = f"{row['outdoor']:.1f}°F" if row["outdoor"] is not None else "  —"
        diff_s = f"{row['advantage']:+.1f}°F" if row["advantage"] is not None else "  —"
        h2c_s = _fmt_hours(row["hours_to_cool"])
        rem_s = _fmt_hours(row["remaining"])
        if row["skip_reason"]:
            status = DIM(f"SKIP  ({row['skip_reason']})")
        elif row["viable"] and row["is_latest"]:
            status = GREEN("[ok] LATEST VIABLE START")
        elif row["viable"]:
            status = GREEN("[ok]")
        else:
            h_s = _fmt_hours(row["hours_to_cool"])
            status = DIM(f"[x]  needs {h_s} > remaining {rem_s}")
        print(f"  {t_str:>7}  {indoor_s:>7}  {outdoor_s:>8}  {diff_s:>6}  {h2c_s:>10}  {rem_s:>10}  {status}")


def _print_strategy_summary(label: str, result: dict[str, Any] | None, current_time: datetime) -> None:
    if result is None:
        print(f"  {label:12s}  {RED('CANNOT ACHIEVE TARGET')}")
        return
    start_h = result["start_hours_from_now"]
    cool_h = result["hours_to_cool"]
    start_dt = current_time + timedelta(hours=start_h)
    print(
        f"  {label:12s}  latest start {GREEN(_fmt_time(start_dt))}  "
        f"(+{start_h:.1f}h from now)  run {_fmt_hours(cool_h)}"
    )


# ---------------------------------------------------------------------------
# Core: run one scenario
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: dict[str, Any],
    params: dict[str, float] | None = None,
    verbose: bool = True,
) -> bool:
    """Run a single scenario.  Returns True if it passes the expected outcome."""
    conditions = dict(scenario["conditions"])
    expected_method = scenario.get("expected_method")
    expected_timing = scenario.get("expected_timing_contains")

    # --- Build model and engine ---
    model_config: dict[str, Any] = {}
    thermal_model = ThermalModel(model_config)
    if params:
        thermal_model.update_params(params)

    engine = StrategyEngine(thermal_model)

    current_time: datetime = conditions.get("current_time", datetime.now())

    # --- No-action prediction (trajectory if nothing changes) ---
    no_action_strat = None
    if conditions.get("ac_running"):
        no_action_strat = "ac"
    elif conditions.get("fan_running"):
        no_action_strat = "fan"
    elif conditions.get("window_open"):
        no_action_strat = "natural"

    hours_to_target = engine._hours_from_conditions(conditions)

    no_action_pred = thermal_model.predict_temperature(
        current_conditions=conditions,
        hours_ahead=hours_to_target,
        cooling_strategy=no_action_strat,
    )

    # --- Print scenario header ---
    _print_separator("=")
    print(BOLD(f"SCENARIO: {scenario['name']}"))
    if scenario.get("description"):
        print(f"  {DIM(scenario['description'])}")

    print()
    print(f"  Indoor: {conditions['indoor_temp']}°F   "
          f"Outdoor: {conditions['outdoor_temp']}°F   "
          f"Target: {conditions['target_temp']}°F by {conditions['target_time']}   "
          f"Window: {hours_to_target:.1f}h")
    print(f"  AQI: {conditions.get('aqi', 50)}   "
          f"Wind: {conditions.get('wind_speed', 0)}mph   "
          f"Humidity: {conditions.get('outdoor_humidity', 50)}%")
    avail = []
    if conditions.get("fan_available"):
        avail.append("fan")
    if conditions.get("ac_available"):
        avail.append("AC")
    print(f"  Devices available: {', '.join(avail) or 'none'}")
    if params:
        changed = {k: v for k, v in params.items() if v != DEFAULT_PHYSICS_PARAMS.get(k)}
        if changed:
            print(f"  {YELLOW('Custom params')}: {changed}")

    # --- No-action trajectory ---
    print()
    _print_header("NO-ACTION TRAJECTORY")
    no_action_hourly = no_action_pred.hourly_predictions
    print(f"  {'Hour':>5}  {'Time':>7}  {'Indoor':>7}  {'Outdoor':>8}  {'Diff':>6}")
    print(DIM("  " + "-" * 46))
    forecast = conditions.get("forecast", [])
    for i, entry in enumerate(no_action_hourly):
        future_time = current_time + timedelta(hours=float(i))
        fcast = thermal_model._get_forecast_for_hour(forecast, future_time)
        outdoor_f = fcast.get("temperature", conditions.get("outdoor_temp", 70.0))
        diff = entry["predicted_temp"] - outdoor_f
        print(f"  {i:>5}  {_fmt_time(future_time):>7}  "
              f"{entry['predicted_temp']:>6.1f}°F  {outdoor_f:>7.1f}°F  {diff:>+6.1f}°F")

    # --- Forward scan per strategy ---
    print()
    _print_header("FORWARD SCAN — per-hour viability")

    aqi = conditions.get("aqi", 50)
    aqi_ok = aqi <= engine.aqi_threshold
    tolerance_hours = 30 / 60.0  # match StrategyEngine default

    scan_results: dict[str, Any] = {}

    if aqi_ok:
        res, trace = _run_scan_with_trace(
            engine, "natural", conditions, no_action_hourly,
            hours_to_target, tolerance_hours, check_outdoor_advantage=True,
        )
        scan_results["natural"] = res
        _print_scan_table("natural", trace, check_advantage=True)

        if conditions.get("fan_available"):
            res, trace = _run_scan_with_trace(
                engine, "fan", conditions, no_action_hourly,
                hours_to_target, tolerance_hours, check_outdoor_advantage=True,
            )
            scan_results["fan"] = res
            _print_scan_table("fan", trace, check_advantage=True)
    else:
        print(f"  {YELLOW('AQI too high')} ({aqi}) — natural and fan skipped")
        scan_results["natural"] = None
        scan_results["fan"] = None

    if conditions.get("ac_available"):
        res, trace = _run_scan_with_trace(
            engine, "ac", conditions, no_action_hourly,
            hours_to_target, tolerance_hours, check_outdoor_advantage=False,
        )
        scan_results["ac"] = res
        _print_scan_table("ac", trace, check_advantage=False)

    # --- Strategy comparison ---
    print()
    _print_header("STRATEGY EVALUATION")
    for label, result in scan_results.items():
        _print_strategy_summary(label, result, current_time)

    # --- Final recommendation (using the real engine) ---
    strategy = engine.recommend(
        current_conditions=conditions,
        prediction=no_action_pred,
        tolerance_minutes=30,
    )

    print()
    _print_header("RECOMMENDATION")
    method_str = strategy.method.value
    print(f"  Method : {BOLD(method_str)}")
    print(f"  Timing : {BOLD(strategy.timing)}")
    print(f"  Temp   : predicted {strategy.predicted_temp:.1f}\u00b0F -> target {strategy.target_temp:.1f}\u00b0F")
    print(f"  Confidence: {strategy.confidence:.0%}")
    print(f"  Reasoning: {strategy.reasoning}")

    if strategy.alternatives:
        print()
        _print_header("ALTERNATIVES")
        print(f"  {'Method':14s}  {'Predicted':>10}  {'h_to_cool':>10}  {'Start by':>10}  {'Achieves':>8}  Chosen")
        for alt in strategy.alternatives:
            chosen_s = GREEN("*") if alt.get("chosen") else " "
            ach_s = GREEN("yes") if alt["achieves_target"] else RED("no ")
            start_s = _fmt_hours(alt.get("start_hours_from_now"))
            cool_s = _fmt_hours(alt.get("hours_to_cool"))
            pred_s = f"{alt['predicted_temp']:.1f}°F"
            print(f"  {alt['method']:14s}  {pred_s:>10}  {cool_s:>10}  {start_s:>10}  {ach_s:>8}  {chosen_s}")

    # --- Verdict ---
    print()
    passed = True
    verdict_lines = []

    if expected_method and method_str != expected_method:
        verdict_lines.append(
            RED(f"FAIL  method: got '{method_str}', expected '{expected_method}'")
        )
        passed = False
    elif expected_method:
        verdict_lines.append(GREEN(f"PASS  method: {method_str}"))

    if expected_timing is not None:
        if expected_timing.lower() in strategy.timing.lower():
            verdict_lines.append(GREEN(f"PASS  timing contains '{expected_timing}': '{strategy.timing}'"))
        else:
            verdict_lines.append(
                RED(f"FAIL  timing: expected to contain '{expected_timing}', got '{strategy.timing}'")
            )
            passed = False

    if verdict_lines:
        _print_header("VERDICT")
        for v in verdict_lines:
            print(f"  {v}")

    print()
    return passed


# ---------------------------------------------------------------------------
# Load scenario from YAML file
# ---------------------------------------------------------------------------

def _load_yaml_scenario(path: str) -> dict[str, Any]:
    if not _HAS_YAML:
        raise RuntimeError("PyYAML not installed. Run: pip install pyyaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # Parse current_time string if needed
    cond = data.get("conditions", {})
    if isinstance(cond.get("current_time"), str):
        cond["current_time"] = datetime.fromisoformat(cond["current_time"])
    # Parse forecast datetime strings
    for entry in cond.get("forecast", []):
        if isinstance(entry.get("datetime"), str):
            entry["datetime"] = datetime.fromisoformat(entry["datetime"])
    return data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart Cooling scenario simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("files", nargs="*", help="YAML scenario file(s)")
    parser.add_argument("--params", metavar="FILE",
                        help="JSON params file (from export-params.ps1 or HA .storage)")
    parser.add_argument("--list", action="store_true", dest="list_only",
                        help="List built-in scenario names and exit")
    parser.add_argument("--scenario", metavar="NAME",
                        help="Run one built-in scenario by name (partial match)")
    args = parser.parse_args()

    if args.list_only:
        print(BOLD("Built-in scenarios:"))
        for i, s in enumerate(BUILTIN_SCENARIOS, 1):
            print(f"  {i:2d}. {s['name']}")
        return

    # Load optional params override
    params: dict[str, float] | None = None
    if args.params:
        with open(args.params, encoding="utf-8") as f:
            params = json.load(f)
        print(YELLOW(f"Using learned params from: {args.params}"))
        print()

    # Collect scenarios to run
    scenarios: list[dict[str, Any]] = []

    if args.files:
        for pattern in args.files:
            for path in sorted(glob.glob(pattern)):
                scenarios.append(_load_yaml_scenario(path))
        if not scenarios:
            print(RED(f"No scenario files matched: {args.files}"))
            sys.exit(1)
    elif args.scenario:
        needle = args.scenario.lower()
        matches = [s for s in BUILTIN_SCENARIOS if needle in s["name"].lower()]
        if not matches:
            print(RED(f"No built-in scenario matches '{args.scenario}'"))
            print("Run with --list to see available scenarios.")
            sys.exit(1)
        scenarios = matches
    else:
        scenarios = BUILTIN_SCENARIOS

    # Run
    results = []
    for scenario in scenarios:
        passed = run_scenario(scenario, params=params)
        results.append((scenario["name"], passed))

    # Summary
    _print_separator("=")
    total = len(results)
    passed_n = sum(1 for _, p in results if p)
    print(BOLD(f"SUMMARY: {passed_n}/{total} scenarios passed"))
    for name, passed in results:
        icon = GREEN("[ok]") if passed else RED("[x]")
        print(f"  {icon}  {name}")
    print()

    if passed_n < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
