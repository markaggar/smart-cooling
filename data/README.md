# Sample Historical Data

Place your historical sensor data files here.

## Expected Format

### Excel/CSV Columns

The data loader expects these columns (or you can provide a mapping):

| Column | Required | Description |
|--------|----------|-------------|
| `timestamp` | Yes | DateTime in ISO format |
| `indoor_temp` | Yes | Indoor temperature (°F) |
| `outdoor_temp` | Yes | Outdoor temperature (°F) |
| `target_temp` | No | Target temperature (default: 72°F) |
| `humidity` | No | Indoor humidity (%) |
| `aqi` | No | Air Quality Index |
| `wind_speed` | No | Wind speed (mph) |
| `cloud_coverage` | No | Cloud coverage (%) |
| `uv_index` | No | UV index |
| `window_open` | No | Window state (true/false) |
| `fan_running` | No | Fan state (true/false) |
| `ac_running` | No | AC state (true/false) |

### Example Column Mapping

If your spreadsheet uses different column names:

```python
column_mapping = {
    "timestamp": "DateTime",           # Your timestamp column
    "indoor_temp": "Master Bedroom",   # Your indoor temp column
    "outdoor_temp": "Outside Temp",    # Your outdoor temp column
    "humidity": "Bedroom Humidity",
}
```

## Loading Your Data

```python
from custom_components.smart_cooling.historical_replay import HistoricalDataLoader

loader = HistoricalDataLoader()

# From Excel
data = loader.load_from_excel(
    "data/summer_2025.xlsx",
    sheet_name="Sensor Data",
    column_mapping={
        "timestamp": "DateTime",
        "indoor_temp": "Bedroom Temp",
        "outdoor_temp": "Outside Temp",
    }
)

# From CSV
data = loader.load_from_csv(
    "data/exported_sensors.csv",
    column_mapping={...}
)

print(f"Loaded {len(data)} data points")
print(f"Date range: {data[0].timestamp} to {data[-1].timestamp}")
```
