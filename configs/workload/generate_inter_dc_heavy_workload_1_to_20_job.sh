#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

TOPOLOGY_FILE="configs/topology/inter_dc_triple_topology.yaml"
WORKLOAD_DIR="configs/workload"

cd "$ROOT_DIR"

for i in $(seq 1 20); do
  OUTPUT_FILE="$WORKLOAD_DIR/inter_dc_heavy_workload_${i}_job.yaml"
  echo "[${i}/20] 生成 ${OUTPUT_FILE} (structured_round_count=${i})"

  "$PYTHON_BIN" configs/workload/generate_workload.py \
    --topology-file "$TOPOLOGY_FILE" \
    --output-file "$OUTPUT_FILE" \
    --mode 2 \
    --random-seed 42 \
    --mode2-flow-mode 2 \
    --structured-collective-gpu-count 4 \
    --structured-collective-total-data-mb 4096.0 \
    --structured-collective-chunk-count 16 \
    --structured-collective-job-count-per-dc 1 \
    --structured-cross-dc-job-count 1 \
    --structured-cross-dc-total-data-mb 1024 \
    --structured-cross-dc-chunk-count 8 \
    --structured-round-count "$i"
done

echo "完成：已生成 inter_dc_heavy_workload_1_job.yaml 到 inter_dc_heavy_workload_20_job.yaml"