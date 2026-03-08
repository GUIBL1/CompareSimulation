#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <experiment-a.yaml> <experiment-b.yaml> <output-dir> [extra compare_experiments.py args...]"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="/home/code/miniconda3/envs/networkSimulation/bin/python"

EXPERIMENT_A="$1"
EXPERIMENT_B="$2"
OUTPUT_DIR="$3"
shift 3

cd "$ROOT_DIR"

"$PYTHON_BIN" scripts/compare_experiments.py \
  --experiment-a "$EXPERIMENT_A" \
  --experiment-b "$EXPERIMENT_B" \
  --output-dir "$OUTPUT_DIR" \
  "$@"