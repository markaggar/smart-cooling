# Smart Cooling Integration for Home Assistant

A self-learning smart cooling integration that predicts indoor temperatures and recommends optimal cooling strategies (window, fan, or AC) to reach your target bedtime temperature.

## Features

- **Physics-based thermal modeling** - Predicts indoor temperature evolution based on outdoor conditions, solar gain, and thermal transfer
- **Multi-strategy optimization** - Evaluates natural cooling, fan cooling, and AC to recommend the most energy-efficient option
- **Self-learning** - Automatically adjusts physics parameters based on prediction accuracy over time
- **Historical data replay** - Test and tune the model using historical sensor data from spreadsheets

## Installation

### HACS (Coming Soon)

This integration will be available through HACS once stable.

### Manual Installation

1. Copy the `custom_components/smart_cooling` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration through the UI: Settings → Devices & Services → Add Integration → Smart Cooling

## Configuration

The integration requires:

**Required Sensors:**
- Indoor temperature sensor (bedroom)
- Outdoor temperature sensor

**Optional Sensors:**
- Indoor humidity sensor
- Air Quality Index (AQI) sensor
- Wind speed sensor
- Weather entity (for forecasts)

**Optional Device State Sensors:**
- Window open/closed binary sensor
- Window fan on/off binary sensor
- AC on/off binary sensor

**Optional Target Entities:**
- Target temperature input_number
- Bedtime input_datetime

## Sensors Created

| Sensor | Description |
|--------|-------------|
| `sensor.smart_cooling_recommendation` | Current cooling recommendation (e.g., "Start fan NOW!") |
| `sensor.smart_cooling_predicted_bedtime_temp` | Predicted temperature at bedtime |
| `sensor.smart_cooling_cooling_deficit` | Degrees above target (cooling needed) |
| `sensor.smart_cooling_prediction_confidence` | Model confidence based on learning |

## Development

### Prerequisites

- Python 3.11+
- Home Assistant development environment (optional for full testing)

### Setup

```bash
cd smart-cooling
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements_dev.txt
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=custom_components/smart_cooling

# Run specific test file
pytest tests/test_thermal_model.py -v
```

### Testing with Historical Data

The integration includes a historical replay system for testing without live sensors:

```python
from custom_components.smart_cooling.historical_replay import (
    HistoricalDataLoader,
    HistoricalReplayEngine,
    generate_synthetic_data,
)
from custom_components.smart_cooling.thermal_model import ThermalModel
from custom_components.smart_cooling.strategy_engine import StrategyEngine

# Option 1: Load from Excel (your historical data)
loader = HistoricalDataLoader()
data = loader.load_from_excel(
    "summer_2025_data.xlsx",
    column_mapping={
        "timestamp": "datetime",
        "indoor_temp": "bedroom_temp",
        "outdoor_temp": "outside_temp",
    }
)

# Option 2: Generate synthetic data for testing
data = generate_synthetic_data(
    start_time=datetime(2024, 7, 15, 6, 0),
    hours=48,
    scenario="hot_day",  # or "cool_day", "mild_day"
)

# Replay through model
model = ThermalModel(config={})
strategy = StrategyEngine(model)
replay = HistoricalReplayEngine(model, strategy)

results = replay.replay_data(data, prediction_horizon_hours=4.0)
metrics = replay.calculate_metrics(results)

print(f"Mean Absolute Error: {metrics['mean_absolute_error']:.2f}°F")
print(f"Prediction Bias: {metrics['mean_error']:.2f}°F")
```

### Deployment to HA Dev

The project uses GitHub Actions to automatically deploy to your development Home Assistant instance.

**Setup GitHub Secrets:**

| Secret | Description |
|--------|-------------|
| `HA_DEV_HOST` | Hostname/IP of your HA Dev instance |
| `HA_DEV_TOKEN` | Long-lived access token for HA API |
| `HA_DEV_SSH_KEY` | SSH private key for rsync deployment |

**Manual Deployment:**

```bash
# Copy to HA Dev
rsync -avz custom_components/smart_cooling/ user@ha-dev:/config/custom_components/smart_cooling/

# Restart HA (via API)
curl -X POST -H "Authorization: Bearer $TOKEN" http://ha-dev:8123/api/services/homeassistant/restart
```

## Learning System

The integration learns from its prediction accuracy:

1. **Records Predictions** - Each recommendation records the predicted bedtime temperature
2. **Records Actuals** - At bedtime, the actual temperature is recorded
3. **Compares & Learns** - If predictions are consistently off, physics parameters are adjusted
4. **Persists Knowledge** - Learned parameters are saved and survive restarts

Learned parameters include:
- `base_heat_gain_rate` - How fast the house gains heat
- `thermal_transfer_coefficient` - Heat transfer through walls
- `fan_cooling_effectiveness` - How effective fan cooling is
- `ac_cooling_rate_mild` / `ac_cooling_rate_hot` - AC effectiveness at different outdoor temps

## Roadmap

- [ ] Initial release with thermal model and strategy engine
- [ ] Historical data import from spreadsheets
- [ ] Learning system validation with real data
- [ ] Automated bedtime actual recording
- [ ] HACS submission
- [ ] Multiple room support
- [ ] Integration with AC/fan automations

## Contributing

Contributions are welcome! Please open an issue or PR.

## License

MIT License - see [LICENSE](LICENSE)
