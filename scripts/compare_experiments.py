from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.config.loaders import load_experiment_config
from simulator.experiment.runner import ExperimentRunner
from simulator.metrics import export_experiment_results
from simulator.metrics import generate_experiment_comparison_visuals
from simulator.metrics import generate_experiment_three_way_comparison_visuals


def _export_run_result(experiment_file: Path, output_dir: Path):
    runner = ExperimentRunner(experiment_file)
    experiment = load_experiment_config(experiment_file)
    run_result = runner.run()
    exported = export_experiment_results(
        experiment=experiment,
        output_dir=output_dir,
        run_records=[
            {
                "repetition_index": record.repetition_index,
                "runtime": record.runtime,
                "scheduler_debug_state": record.scheduler_debug_state,
            }
            for record in run_result.repetitions
        ],
    )
    run_result.output_dir = output_dir
    run_result.exported_files = exported
    return run_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run experiment configs, compare them, and write all outputs under one directory.")
    parser.add_argument("--experiment-a", required=True, help="First experiment config path.")
    parser.add_argument("--experiment-b", required=True, help="Second experiment config path.")
    parser.add_argument("--experiment-c", default=None, help="Optional third experiment config path for three-way comparison.")
    parser.add_argument("--output-dir", required=True, help="Directory to store both run outputs and comparison outputs.")
    parser.add_argument("--label-a", default=None, help="Display label for the first experiment. Defaults to the experiment name.")
    parser.add_argument("--label-b", default=None, help="Display label for the second experiment. Defaults to the experiment name.")
    parser.add_argument("--label-c", default=None, help="Display label for the third experiment. Defaults to the experiment name.")
    parser.add_argument("--title", default="Experiment Comparison", help="Comparison title prefix.")
    args = parser.parse_args()

    experiment_a = Path(args.experiment_a).resolve()
    experiment_b = Path(args.experiment_b).resolve()
    experiment_c = Path(args.experiment_c).resolve() if args.experiment_c else None
    experiment_a_config = load_experiment_config(experiment_a)
    experiment_b_config = load_experiment_config(experiment_b)
    experiment_c_config = load_experiment_config(experiment_c) if experiment_c is not None else None
    output_dir = Path(args.output_dir).resolve()
    run_a_dir = output_dir / "run_a"
    run_b_dir = output_dir / "run_b"
    run_c_dir = output_dir / "run_c"
    comparison_dir = output_dir / "comparison"
    output_dir.mkdir(parents=True, exist_ok=True)
    label_a = args.label_a or experiment_a_config.meta.name
    label_b = args.label_b or experiment_b_config.meta.name
    label_c = args.label_c or (experiment_c_config.meta.name if experiment_c_config is not None else None)

    run_a = _export_run_result(experiment_a, run_a_dir)
    run_b = _export_run_result(experiment_b, run_b_dir)
    run_c = _export_run_result(experiment_c, run_c_dir) if experiment_c is not None else None
    if run_c is not None:
        comparison_outputs = generate_experiment_three_way_comparison_visuals(
            result_a_dir=run_a_dir,
            result_b_dir=run_b_dir,
            result_c_dir=run_c_dir,
            output_dir=comparison_dir,
            label_a=label_a,
            label_b=label_b,
            label_c=label_c,
            title=args.title,
        )
    else:
        comparison_outputs = generate_experiment_comparison_visuals(
            result_a_dir=run_a_dir,
            result_b_dir=run_b_dir,
            output_dir=comparison_dir,
            label_a=label_a,
            label_b=label_b,
            title=args.title,
        )
    comparison_summary = json.loads(Path(comparison_outputs["summary_json"]).read_text(encoding="utf-8"))

    manifest = {
        "experiment_a": str(experiment_a),
        "experiment_b": str(experiment_b),
        "experiment_c": str(experiment_c) if experiment_c is not None else None,
        "label_a": label_a,
        "label_b": label_b,
        "label_c": label_c,
        "title": args.title,
        "run_a_output_dir": str(run_a_dir),
        "run_b_output_dir": str(run_b_dir),
        "run_c_output_dir": str(run_c_dir) if run_c is not None else None,
        "comparison_output_dir": str(comparison_dir),
        "comparison_outputs": comparison_outputs,
        "comparison_metrics": comparison_summary.get("metrics", []),
    }
    manifest_path = output_dir / "comparison_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()