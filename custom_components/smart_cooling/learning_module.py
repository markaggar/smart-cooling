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
        
        # Load persisted state
        self._load_state()

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
            predicted_temp=prediction.predicted_bedtime_temp,
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
                    record.timestamp,
                    record.target_datetime,
                )
        
        if matched:
            self._pending_predictions = [
                r for r in self._pending_predictions
                if r.actual_temp is None
            ]
            self._save_state()

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
        """Analyze errors and compute parameter adjustments.
        
        Uses simple gradient-based learning:
        - If we consistently predict too hot, reduce heat gain params
        - If we consistently predict too cold, increase heat gain params
        - Similar logic for cooling rate params
        
        Returns updated parameters if changes warranted, None otherwise.
        """
        # Need enough recent data with actuals
        records_with_actuals = [
            r for r in self._historical_records[-100:]
            if r.actual_temp is not None
        ]
        
        if len(records_with_actuals) < 20:
            return None  # Not enough data
        
        # Calculate bias (systematic over/under prediction)
        errors = [r.prediction_error() for r in records_with_actuals if r.prediction_error() is not None]
        mean_error = sum(errors) / len(errors)
        
        # Only update if bias is significant (> 0.5°F)
        if abs(mean_error) < 0.5:
            return None
        
        _LOGGER.info(
            "Learning: mean prediction error is %.2f°F, adjusting parameters",
            mean_error,
        )
        
        # Start with current learned params
        updated_params = dict(self._learned_params)
        
        # If we predict too cold (actual is hotter), increase heat gain
        # If we predict too hot (actual is colder), decrease heat gain
        adjustment = -mean_error * self.learning_rate
        
        # Apply adjustment to heat gain rate
        current_heat_gain = updated_params.get(
            "base_heat_gain_rate",
            DEFAULT_PHYSICS_PARAMS["base_heat_gain_rate"],
        )
        new_heat_gain = current_heat_gain + adjustment
        # Clamp to reasonable range
        new_heat_gain = max(0.5, min(5.0, new_heat_gain))
        updated_params["base_heat_gain_rate"] = round(new_heat_gain, 2)
        
        # Save and return
        self._learned_params = updated_params
        self._save_state()
        
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
            self._save_state()
            _LOGGER.info("Imported %d historical records", imported)
        
        return imported

    async def clear_learned_params(self) -> None:
        """Reset learned parameters to defaults."""
        self._learned_params = {}
        self._save_state()
        _LOGGER.info("Cleared learned parameters")

    async def clear_history(self) -> None:
        """Clear all historical records."""
        self._historical_records = []
        self._pending_predictions = []
        self._save_state()
        _LOGGER.info("Cleared historical records")
