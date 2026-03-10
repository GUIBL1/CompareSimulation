from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from simulator.experiment.teccl_planning import run_teccl_planning_export


def main() -> None:
	parser = argparse.ArgumentParser(description="Build and solve the stage-3 TE-CCL MILP, then export solver stats artifacts.")
	parser.add_argument("--experiment", required=True, help="TE-CCL experiment config path.")
	parser.add_argument("--output-dir", default=None, help="Optional output directory. Defaults to experiment metrics.output_dir.")
	args = parser.parse_args()

	result = run_teccl_planning_export(
		experiment_file=Path(args.experiment).resolve(),
		output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
	)
	payload = {
		"experiment_name": result.experiment_name,
		"output_dir": str(result.output_dir),
		"exported_files": result.exported_files,
		"solver_stats": result.solver_stats.to_dict(),
	}
	print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
	main()