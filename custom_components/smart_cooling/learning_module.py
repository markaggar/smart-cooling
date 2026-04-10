"""Learning module for parameter optimization."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DEFAULT_LEARNING_RATE, DEFAULT_PHYSICS_PARAMS

_LOGGER = logging.getLogger(__name__)


@dataclass
class PredictionRecord:
    """Record of a prediction for later comparison."""
    
    timestamp: str
    predicted_temp: float
    actual_temp: float | None
    conditions: dict[str, Any]
    params_used: dict[str, float]
    # ISO datetime of the target time this prediction was made for (e.g. bedtime)
    target_datetime: str | None = None
    
    def prediction_error(self) -> float | None:
        """Calculate error if actual is known."""
        if self.actual_temp is None:
            return None
        return self.actual_temp - self.predicted_temp


class LearningModule:
    """Learns optimal physics parameters from prediction accuracy."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        learning_rate: float = DEFAULT_LEARNING_RATE,
    ) -> None:
        """Initialize learning module."""
        self.hass = hass
        self.entry_id = entry_id
        self.learning_rate = learning_rate
        
        # Storage paths
        self._storage_dir = Path(hass.config.path(".storage")) / "smart_cooling"
        self._params_file = self._storage_dir / f"params_{entry_id}.json"
        self._history_file = self._storage_dir / f"history_{entry_id}.json"
        
        # In-memory state
        self._learned_params: dict[str, float] = {}
        self._pending_predictions: list[PredictionRecord] = []
        self._historical_records: list[PredictionRecord] = []
        # Persisted state is loaded asynchronously via async_load() to avoid
        # blocking the event loop during integration setup.

    def _load_state(self) -> None:
        """Load persisted parameters and history."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Load learned parameters
        if self._params_file.exists():
            try:
                with open(self._params_file) as f:
                    self._learned_params = json.load(f)
                _LOGGER.info("Loaded learned parameters: %s", self._learned_params)
            except (json.JSONDecodeError, OSError) as err:
                _LOGGER.warning("Failed to load params: %s", err)
        
        # Load historical records
        if self._history_file.exists():
            try:
                with open(self._history_file) as f:
                    data = json.load(f)
                    self._historical_records = [
                        PredictionRecord(**r) for r in data
                    ]
                _LOGGER.info("Loaded %d historical records", len(self._historical_records))
            except (json.JSONDecodeError, OSError) as err:
                _LOGGER.warning("Failed to load history: %s", err)

    async def async_load(self) -> None:
        """Load persisted parameters and history without blocking the event loop."""
        await self.hass.async_add_executor_job(self._load_state)

    def _save_state(self) -> None:
        """Persist parameters and history."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Save parameters
        try:
            with open(self._params_file, "w") as f:
                json.dump(self._learned_params, f, indent=2)
        except OSError as err:
            _LOGGER.error("Failed to save params: %s", err)
        
        # Save history (keep last 1000 records)
        try:
            records_to_save = self._historical_records[-1000:]
            with open(self._history_file, "w") as f:
                json.dump([asdict(r) for r in records_to_save], f, indent=2)
        except OSError as err:
            _LOGGER.error("Failed to save history: %s", err)

    async def save_params(self, params: dict[str, float]) -> None:
        """Merge new params into learned params and persist."""
        self._learned_params.update(params)
        await self.hass.async_add_executor_job(self._save_state)

    def get_learned_params(self) -> dict[str, float]:
        """Get current learned parameters."""
        return dict(self._learned_params)

    def get_confidence(self) -> float:
        """Calculate prediction confidence based on historical accuracy.
        
        Returns value 0-1 where 1 is very confident.
        """
        # Need at least 10 records with actuals to have any confidence
        records_with_actuals = [
            r for r in self._historical_records
            if r.actual_temp is not None
        ]
        
        if len(records_with_actuals) < 10:
            return 0.5  # Baseline confidence
        
        # Calculate mean absolute error over recent predictions
        recent = records_with_actuals[-50:]  # Last 50 predictions
        errors = [abs(r.prediction_error()) for r in recent if r.prediction_error() is not None]
        
        if not errors:
            return 0.5
        
        mae = sum(errors) / len(errors)
        
        # Convert MAE to confidence (lower error = higher confidence)
        # MAE of 0 = confidence 1.0, MAE of 5+ = confidence ~0.3
        confidence = max(0.3, 1.0 - (mae / 7.0))
        
        return confidence

    def record_prediction(
        self,
        timestamp: datetime,
        conditions: dict[str, Any],
        prediction: Any,  # TemperaturePrediction
        target_datetime: datetime | None = None,
    ) -> None:
        """Record a prediction for later comparison with actual.
        
        target_datetime is the time by which the room should reach target temp.
        Only one record is kept per target_datetime to avoid flooding pending list.
        """
        # Deduplicate: if we already have a pending prediction for this target time,
        # replace it with the more recent one (better current conditions data).
        target_dt_str = target_datetime.isoformat() if target_datetime else None
        if target_dt_str:
            self._pending_predictions = [
                r for r in self._pending_predictions
                if r.target_datetime != target_dt_str
            ]

        # Get the parameters that were used for this prediction
        params_used = dict(DEFAULT_PHYSICS_PARAMS)
        params_used.update(self._learned_params)
        
        # Serialize conditions (remove non-serializable items)
        serializable_conditions = {
            k: v for k, v in conditions.items()
            if k != "current_time" and k != "forecast"
        }
        serializable_conditions["current_time"] = timestamp.isoformat()
        
        record = PredictionRecord(
            timestamp=timestamp.isoformat(),
            predicted_temp=prediction.predicted_target_temp,
            actual_temp=None,  # Will be filled in later
            conditions=serializable_conditions,
            params_used=params_used,
            target_datetime=target_dt_str,
        )
        
        self._pending_predictions.append(record)
        
        # Keep only last 100 pending (older ones probably won't get actuals)
        self._pending_predictions = self._pending_predictions[-100:]

    async def record_actual(
        self, timestamp: datetime, actual_temp: float
    ) -> None:
        """Record actual temperature and match with predictions.
        
        Matches pending predictions whose target_datetime is close to timestamp.
        """
        matched = False
        
        for record in self._pending_predictions:
            if record.target_datetime is None:
                continue
            try:
                target_dt = datetime.fromisoformat(record.target_datetime)
            except ValueError:
                continue
            # Match if the target time is within 30 minutes of the measured actual
            time_diff = abs((timestamp - target_dt).total_seconds())
            if time_diff < 1800:
                record.actual_temp = actual_temp
                self._historical_records.append(record)
                matched = True
                _LOGGER.debug(
                    "Matched actual %.1f°F with prediction %.1f°F (error: %.1f°F) "
                    "[predicted at %s for target %s]",
                    actual_temp,
                    record.predicted_temp,
                    actual_temp - record.predicted_temp,
                    record.timestamp,
                    record.target_datetime,
                )
        
        if matched:
            self._pending_predictions = [
                r for r in self._pending_predictions
                if r.actual_temp is None
            ]
            await self.hass.async_add_executor_job(self._save_state)
        elif self._pending_predictions:
            _LOGGER.debug(
                "record_actual: no pending prediction matched timestamp %s "
                "(actual=%.1f°F, %d pending)",
                timestamp.isoformat(),
                actual_temp,
                len(self._pending_predictions),
            )

    async def try_complete_predictions(
        self, current_time: datetime, current_indoor_temp: float
    ) -> None:
        """Called every update cycle to check if any predictions' target time has passed.
        
        When target_datetime <= now, we can record the actual indoor temperature
        and mark that prediction complete for learning.
        """
        overdue = [
            r for r in self._pending_predictions
            if r.target_datetime is not None
            and datetime.fromisoformat(r.target_datetime) <= current_time
        ]
        if overdue:
            await self.record_actual(current_time, current_indoor_temp)

    async def compute_parameter_updates(self) -> dict[str, float] | None:
        """Adjust physics parameters based on prediction errors, segmented by what was running.

        Segments history into four modes:
          - Passive (no AC, fan, or window): bias → base_heat_gain_rate
          - Window only: bias → natural_cooling_effectiveness
          - Fan (fan or fan+window): bias → fan_cooling_effectiveness
          - AC: bias → ac_cooling_rate_mild / ac_cooling_rate_hot

        Error sign convention: prediction_error = actual - predicted.
          Positive → room ended up warmer than predicted.
          Passive: warmer than expected → heat gain was underestimated → raise base_heat_gain_rate.
          Cooling modes: warmer than expected → cooling was overestimated → lower the rate.

        Returns updated parameters dict if any change was made, else None.
        """
        records_with_actuals = [
            r for r in self._historical_records[-200:]
            if r.actual_temp is not None and r.prediction_error() is not None
        ]

        if len(records_with_actuals) < 10:
            return None

        def _mean(recs: list[PredictionRecord]) -> float:
            errs: list[float] = [e for r in recs if (e := r.prediction_error()) is not None]
            return sum(errs) / len(errs)

        updated_params = dict(self._learned_params)
        changed = False

        # --- Passive: no cooling device was running ---
        passive = [
            r for r in records_with_actuals
            if not r.conditions.get("ac_running")
            and not r.conditions.get("fan_running")
            and not r.conditions.get("window_open")
        ]
        if len(passive) >= 5:
            me = _mean(passive)
            if abs(me) >= 0.5:
                # Positive error → actual warmer → heat gain was too low → raise
                cur = updated_params.get("base_heat_gain_rate", DEFAULT_PHYSICS_PARAMS["base_heat_gain_rate"])
                updated_params["base_heat_gain_rate"] = round(
                    max(0.1, min(5.0, cur + me * self.learning_rate)), 3
                )
                changed = True
                _LOGGER.info(
                    "Learning (passive, n=%d): error %.2f°F → base_heat_gain_rate %.3f",
                    len(passive), me, updated_params["base_heat_gain_rate"],
                )

        # --- Window (natural ventilation, no fan, no AC) ---
        window_only = [
            r for r in records_with_actuals
            if not r.conditions.get("ac_running")
            and not r.conditions.get("fan_running")
            and r.conditions.get("window_open")
        ]
        if len(window_only) >= 3:
            me = _mean(window_only)
            if abs(me) >= 0.5:
                # Positive error → natural cooling delivered less than modeled → lower effectiveness
                cur = updated_params.get("natural_cooling_effectiveness", DEFAULT_PHYSICS_PARAMS["natural_cooling_effectiveness"])
                updated_params["natural_cooling_effectiveness"] = round(
                    max(0.01, min(1.0, cur - me * self.learning_rate * 0.05)), 4
                )
                changed = True
                _LOGGER.info(
                    "Learning (window, n=%d): error %.2f°F → natural_cooling_effectiveness %.4f",
                    len(window_only), me, updated_params["natural_cooling_effectiveness"],
                )

        # --- Fan (fan on, with or without window, no AC) ---
        fan_recs = [
            r for r in records_with_actuals
            if not r.conditions.get("ac_running")
            and r.conditions.get("fan_running")
        ]
        if len(fan_recs) >= 3:
            me = _mean(fan_recs)
            if abs(me) >= 0.5:
                # Positive error → fan delivered less cooling than modeled → lower effectiveness
                cur = updated_params.get("fan_cooling_effectiveness", DEFAULT_PHYSICS_PARAMS["fan_cooling_effectiveness"])
                updated_params["fan_cooling_effectiveness"] = round(
                    max(0.01, min(1.0, cur - me * self.learning_rate * 0.05)), 4
                )
                changed = True
                _LOGGER.info(
                    "Learning (fan, n=%d): error %.2f°F → fan_cooling_effectiveness %.4f",
                    len(fan_recs), me, updated_params["fan_cooling_effectiveness"],
                )

        # --- AC: split by outdoor temp at prediction time ---
        ac_recs = [r for r in records_with_actuals if r.conditions.get("ac_running")]
        ac_mild = [r for r in ac_recs if float(r.conditions.get("outdoor_temp", 80)) < 82]
        ac_hot  = [r for r in ac_recs if float(r.conditions.get("outdoor_temp", 80)) >= 82]

        if len(ac_mild) >= 3:
            me = _mean(ac_mild)
            if abs(me) >= 0.5:
                # Positive error → AC delivered less cooling → lower rate
                cur = updated_params.get("ac_cooling_rate_mild", DEFAULT_PHYSICS_PARAMS["ac_cooling_rate_mild"])
                updated_params["ac_cooling_rate_mild"] = round(
                    max(0.5, min(15.0, cur - me * self.learning_rate)), 3
                )
                changed = True
                _LOGGER.info(
                    "Learning (AC mild, n=%d): error %.2f°F → ac_cooling_rate_mild %.3f",
                    len(ac_mild), me, updated_params["ac_cooling_rate_mild"],
                )

        if len(ac_hot) >= 3:
            me = _mean(ac_hot)
            if abs(me) >= 0.5:
                cur = updated_params.get("ac_cooling_rate_hot", DEFAULT_PHYSICS_PARAMS["ac_cooling_rate_hot"])
                updated_params["ac_cooling_rate_hot"] = round(
                    max(0.5, min(15.0, cur - me * self.learning_rate)), 3
                )
                changed = True
                _LOGGER.info(
                    "Learning (AC hot, n=%d): error %.2f°F → ac_cooling_rate_hot %.3f",
                    len(ac_hot), me, updated_params["ac_cooling_rate_hot"],
                )

        if not changed:
            return None

        self._learned_params = updated_params
        await self.hass.async_add_executor_job(self._save_state)
        return updated_params

    # --- Historical Data Import for Testing ---
    
    async def import_historical_data(
        self, records: list[dict[str, Any]]
    ) -> int:
        """Import historical data for learning.
        
        Each record should have:
        - timestamp: ISO datetime string
        - predicted_temp: What was predicted (or estimated)
        - actual_temp: What actually happened
        - conditions: Dict of sensor values at prediction time
        
        Returns number of records imported.
        """
        imported = 0
        
        for record in records:
            try:
                pred_record = PredictionRecord(
                    timestamp=record["timestamp"],
                    predicted_temp=record["predicted_temp"],
                    actual_temp=record["actual_temp"],
                    conditions=record.get("conditions", {}),
                    params_used=record.get("params_used", dict(DEFAULT_PHYSICS_PARAMS)),
                )
                self._historical_records.append(pred_record)
                imported += 1
            except (KeyError, TypeError) as err:
                _LOGGER.warning("Failed to import record: %s", err)
                continue
        
        if imported > 0:
            await self.hass.async_add_executor_job(self._save_state)
            _LOGGER.info("Imported %d historical records", imported)
        
        return imported

    async def clear_learned_params(self) -> None:
        """Reset learned parameters to defaults."""
        self._learned_params = {}
        await self.hass.async_add_executor_job(self._save_state)
        _LOGGER.info("Cleared learned parameters")

    async def clear_history(self) -> None:
        """Clear all historical records."""
        self._historical_records = []
        self._pending_predictions = []
        await self.hass.async_add_executor_job(self._save_state)
        _LOGGER.info("Cleared historical records")
