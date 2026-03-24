#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="/home/inspur-02/.conda/envs/networkSimulation/bin/python"

usage() {
  echo "Usage:"
  echo "  $0 --output-dir <dir> --experiment <exp1.yaml> --experiment <exp2.yaml> [--experiment <expN.yaml> ...] [--label <label1> --label <label2> ...] [extra compare_experiments.py args...]"
  echo "  $0 <exp1.yaml> <exp2.yaml> [<expN.yaml> ...] <output-dir> [-- extra compare_experiments.py args...]"
}

experiments=()
labels=()
extra_args=()
output_dir=""

if [[ $# -gt 0 && "$1" != --* ]]; then
  positional=()
  while [[ $# -gt 0 && "$1" != --* ]]; do
    positional+=("$1")
    shift
  done

  if [[ ${#positional[@]} -lt 3 ]]; then
    usage
    exit 1
  fi

  output_index=$((${#positional[@]} - 1))
  output_dir="${positional[$output_index]}"
  for ((i=0; i<output_index; i++)); do
    experiments+=("${positional[$i]}")
  done

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --label)
        [[ $# -ge 2 ]] || { echo "Missing value for --label"; exit 1; }
        labels+=("$2")
        shift 2
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

if [[ ${#experiments[@]} -lt 2 ]]; then
  echo "At least 2 experiments are required."
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