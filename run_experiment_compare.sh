#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="/home/code/miniconda3/envs/networkSimulation/bin/python"

usage() {
  echo "Usage:"
  echo "  $0 --output-dir <dir> --experiment <exp1.yaml> --experiment <exp2.yaml> --experiment <exp3.yaml> --experiment <exp4.yaml> [--experiment <expN.yaml> ...] [--label <label1> --label <label2> ...] [extra compare_experiments.py args...]"
  echo "  $0 <exp1.yaml> <exp2.yaml> <exp3.yaml> <exp4.yaml> <output-dir> [extra compare_experiments.py args...]"
}

experiments=()
labels=()
extra_args=()
output_dir=""

if [[ $# -gt 0 && "$1" != --* ]]; then
  if [[ $# -lt 5 ]]; then
    usage
    exit 1
  fi
  experiments+=("$1" "$2" "$3" "$4")
  output_dir="$5"
  shift 5
  extra_args+=("$@")
else
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --experiment)
        [[ $# -ge 2 ]] || { echo "Missing value for --experiment"; exit 1; }
        experiments+=("$2")
        shift 2
        ;;
      --label)
        [[ $# -ge 2 ]] || { echo "Missing value for --label"; exit 1; }
        labels+=("$2")
        shift 2
        ;;
      --output-dir)
        [[ $# -ge 2 ]] || { echo "Missing value for --output-dir"; exit 1; }
        output_dir="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --)
        shift
        extra_args+=("$@")
        break
        ;;
      *)
        extra_args+=("$1")
        shift
        ;;
    esac
  done
fi

if [[ ${#experiments[@]} -lt 4 ]]; then
  echo "At least 4 experiments are required."
  usage
  exit 1
fi

if [[ -z "$output_dir" ]]; then
  echo "--output-dir is required."
  usage
  exit 1
fi

cd "$ROOT_DIR"

cmd=("$PYTHON_BIN" scripts/compare_experiments.py --output-dir "$output_dir")
for experiment in "${experiments[@]}"; do
  cmd+=(--experiment "$experiment")
done
for label in "${labels[@]}"; do
  cmd+=(--label "$label")
done
cmd+=("${extra_args[@]}")

"${cmd[@]}"