from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.experiment import run_fair_comparison_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fair comparison matrix experiments.")
    parser.add_argument("--matrix", default="configs/experiment/fair_comparison_matrix.yaml", help="Path to the fair comparison matrix YAML file.")
    parser.add_argument("--include-public", action="store_true", help="Run public comparison cases.")
    parser.add_argument("--include-sweeps", action="store_true", help="Run parameter sweep cases.")
    parser.add_argument("--case-id", action="append", default=[], help="Restrict to specific public case ids. Can be repeated.")
    parser.add_argument("--sweep-id", action="append", default=[], help="Restrict to specific parameter sweep ids. Can be repeated.")
    parser.add_argument("--max-public-runs", type=int, default=None, help="Optional cap on the number of public run specs.")
    parser.add_argument("--max-sweep-runs", type=int, default=None, help="Optional cap on the number of sweep run specs.")
    parser.add_argument(
        "--generated-experiment-dir",
        default="configs/experiment/generated",
        help="Directory used to materialize runnable experiment YAML files.",
    )
    args = parser.parse_args()

    include_public = args.include_public or not args.include_sweeps
    manifest = run_fair_comparison_matrix(
        matrix_path=Path(args.matrix),
        include_public=include_public,
        include_sweeps=args.include_sweeps,
        case_ids=args.case_id or None,
        sweep_ids=args.sweep_id or None,
        max_public_runs=args.max_public_runs,
        max_sweep_runs=args.max_sweep_runs,
        generated_experiment_dir=Path(args.generated_experiment_dir),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()