#!/usr/bin/env python3
"""Script to load historical data and test the model.

Usage:
    python scripts/test_with_historical_data.py path/to/data.xlsx
    python scripts/test_with_historical_data.py --synthetic  # Use synthetic data
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.smart_cooling.historical_replay import (
    HistoricalDataLoader,
    HistoricalReplayEngine,
    generate_synthetic_data,
)
from custom_components.smart_cooling.thermal_model import ThermalModel
from custom_components.smart_cooling.strategy_engine import StrategyEngine


def main():
    parser = argparse.ArgumentParser(description="Test thermal model with historical data")
    parser.add_argument(
        "data_file",
        nargs="?",
        help="Path to Excel or CSV file with historical data",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic data instead of file",
    )
    parser.add_argument(
        "--scenario",
        choices=["hot_day", "cool_day", "mild_day"],
        default="hot_day",
        help="Synthetic data scenario (default: hot_day)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Hours of synthetic data to generate (default: 48)",
    )
    parser.add_argument(
        "--horizon",
        type=float,
        default=4.0,
        help="Prediction horizon in hours (default: 4.0)",
    )
    parser.add_argument(
        "--column-map",
        type=str,
        help="Column mapping as JSON string",
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Smart Cooling - Historical Data Test")
    print("=" * 60)
    
    # Load or generate data
    if args.synthetic:
        print(f"\nGenerating {args.hours} hours of {args.scenario} synthetic data...")
        data = generate_synthetic_data(
            start_time=datetime(2024, 7, 15, 6, 0),
            hours=args.hours,
            scenario=args.scenario,
        )
    elif args.data_file:
        print(f"\nLoading data from: {args.data_file}")
        loader = HistoricalDataLoader()
        
        # Parse column mapping if provided
        column_mapping = None
        if args.column_map:
            import json
            column_mapping = json.loads(args.column_map)
        
        file_path = Path(args.data_file)
        if file_path.suffix in [".xlsx", ".xls"]:
            data = loader.load_from_excel(file_path, column_mapping=column_mapping)
        elif file_path.suffix == ".csv":
            data = loader.load_from_csv(file_path, column_mapping=column_mapping)
        else:
            print(f"Error: Unsupported file type: {file_path.suffix}")
            sys.exit(1)
    else:
        print("Error: Either --synthetic or a data file path is required")
        parser.print_help()
        sys.exit(1)
    
    print(f"Loaded {len(data)} data points")
    if data:
        print(f"Date range: {data[0].timestamp} to {data[-1].timestamp}")
        print(f"Indoor temp range: {min(p.indoor_temp for p in data):.1f}°F - {max(p.indoor_temp for p in data):.1f}°F")
        print(f"Outdoor temp range: {min(p.outdoor_temp for p in data):.1f}°F - {max(p.outdoor_temp for p in data):.1f}°F")
    
    # Create model and engines
    print("\nInitializing thermal model...")
    model = ThermalModel(config={})
    strategy = StrategyEngine(model)
    replay = HistoricalReplayEngine(model, strategy)
    
    # Run replay
    print(f"\nRunning replay with {args.horizon}h prediction horizon...")
    results = replay.replay_data(data, prediction_horizon_hours=args.horizon)
    
    print(f"Generated {len(results)} comparison results")
    
    # Calculate metrics
    if results:
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        
        metrics = replay.calculate_metrics(results)
        
        print(f"\nPrediction Accuracy ({len(results)} samples):")
        print(f"  Mean Error (bias):       {metrics['mean_error']:+.2f}°F")
        print(f"  Mean Absolute Error:     {metrics['mean_absolute_error']:.2f}°F")
        print(f"  Root Mean Square Error:  {metrics['rmse']:.2f}°F")
        print(f"  Max Error:               {metrics['max_error']:.2f}°F")
        
        # Strategy distribution
        strategy_counts = {}
        for r in results:
            s = r.strategy_recommended
            strategy_counts[s] = strategy_counts.get(s, 0) + 1
        
        print("\nStrategy Recommendations:")
        for strategy_name, count in sorted(strategy_counts.items(), key=lambda x: -x[1]):
            pct = 100 * count / len(results)
            print(f"  {strategy_name}: {count} ({pct:.1f}%)")
        
        # Suggestions
        print("\nParameter Adjustment Suggestions:")
        suggestions = replay.suggest_parameter_adjustments(results)
        if suggestions:
            for param, value in suggestions.items():
                current = model.params.get(param, "unknown")
                print(f"  {param}: {current} -> {value}")
        else:
            print("  No adjustments needed (bias < 0.5°F)")
        
        # Sample predictions
        print("\nSample Predictions (first 5):")
        print("-" * 70)
        print(f"{'Time':<20} {'Actual':>10} {'Predicted':>10} {'Error':>10} {'Strategy':<15}")
        print("-" * 70)
        for r in results[:5]:
            print(f"{r.timestamp.strftime('%Y-%m-%d %H:%M'):<20} "
                  f"{r.actual_temp:>10.1f} {r.predicted_temp:>10.1f} "
                  f"{r.error:>+10.1f} {r.strategy_recommended:<15}")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
