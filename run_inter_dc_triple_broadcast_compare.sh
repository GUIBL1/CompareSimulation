#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="/home/code/miniconda3/envs/networkSimulation/bin/python"

cd "$ROOT_DIR"

echo "[1/3] Running triple-channel inter-dc broadcast experiments"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
from simulator.experiment.runner import ExperimentRunner

for experiment_path in [
    Path("configs/experiment/inter_dc_triple_broadcast_crux.yaml"),
    Path("configs/experiment/inter_dc_triple_broadcast_teccl.yaml"),
]:
    runner = ExperimentRunner(experiment_path)
    result = runner.export_results()
    print({
        "experiment": result.experiment_name,
        "scheduler": result.scheduler_type,
        "output_dir": str(result.output_dir),
        "aggregate_metrics": result.aggregate_metrics,
    })
PY

echo "[2/3] Generating triple-channel inter-dc broadcast visuals"
"$PYTHON_BIN" scripts/visualize_crux_vs_teccl.py \
    --crux-result results/inter_dc_triple_broadcast_crux \
    --teccl-result results/inter_dc_triple_broadcast_teccl \
    --output-dir results/visualizations/inter_dc_triple_broadcast \
    --title "Inter-DC Triple Broadcast: CRUX vs TECCL"

echo "[3/3] Comparison summary"
"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

summary_path = Path("results/visualizations/inter_dc_triple_broadcast/comparison_summary.json")
print(json.dumps(json.loads(summary_path.read_text(encoding="utf-8")), indent=2, ensure_ascii=False))
PY

echo "Finished. Results are in results/inter_dc_triple_broadcast_crux, results/inter_dc_triple_broadcast_teccl, and results/visualizations/inter_dc_triple_broadcast"