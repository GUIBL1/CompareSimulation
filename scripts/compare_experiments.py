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
from simulator.metrics import generate_experiment_multi_comparison_visuals
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
    parser.add_argument(
        "--experiment",
        action="append",
        default=[],
        help="Experiment config path. Repeat this option to compare multiple experiments.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Display label matching each --experiment in order.",
    )
    parser.add_argument("--experiment-a", default=None, help="First experiment config path (legacy mode).")
    parser.add_argument("--experiment-b", default=None, help="Second experiment config path (legacy mode).")
    parser.add_argument("--experiment-c", default=None, help="Optional third experiment config path for three-way comparison.")
    parser.add_argument("--output-dir", required=True, help="Directory to store both run outputs and comparison outputs.")
    parser.add_argument("--label-a", default=None, help="Display label for the first experiment. Defaults to the experiment name.")
    parser.add_argument("--label-b", default=None, help="Display label for the second experiment. Defaults to the experiment name.")
    parser.add_argument("--label-c", default=None, help="Display label for the third experiment. Defaults to the experiment name.")
    parser.add_argument("--label-d", default=None, help="Display label for the fourth experiment. Defaults to the experiment name.")
    parser.add_argument("--title", default="Experiment Comparison", help="Comparison title prefix.")
    args = parser.parse_args()

    experiment_paths: list[Path]
    if args.experiment:
        experiment_paths = [Path(item).resolve() for item in args.experiment]
    else:
        if not args.experiment_a or not args.experiment_b:
            raise ValueError("Either provide repeated --experiment options or both --experiment-a and --experiment-b")
        experiment_paths = [Path(args.experiment_a).resolve(), Path(args.experiment_b).resolve()]
        if args.experiment_c:
            experiment_paths.append(Path(args.experiment_c).resolve())
    if len(experiment_paths) < 2:
        raise ValueError("At least two experiment files are required")

    experiment_configs = [load_experiment_config(experiment_path) for experiment_path in experiment_paths]
    output_dir = Path(args.output_dir).resolve()
    comparison_dir = output_dir / "comparison"
    output_dir.mkdir(parents=True, exist_ok=True)

    labels: list[str]
    if args.experiment:
        legacy_labels = [args.label_a, args.label_b, args.label_c, args.label_d]
        labels = [
            (
                args.label[index]
                if index < len(args.label) and args.label[index]
                else (legacy_labels[index] if index < len(legacy_labels) and legacy_labels[index] else experiment_configs[index].meta.name)
            )
            for index in range(len(experiment_paths))
        ]
    else:
        labels = [args.label_a or experiment_configs[0].meta.name, args.label_b or experiment_configs[1].meta.name]
        if len(experiment_paths) >= 3:
            labels.append(args.label_c or experiment_configs[2].meta.name)

    run_dirs = [output_dir / f"run_{index + 1}" for index in range(len(experiment_paths))]
    runs = [
        _export_run_result(experiment_file=experiment_paths[index], output_dir=run_dirs[index])
        for index in range(len(experiment_paths))
    ]

    if len(experiment_paths) == 2:
        comparison_outputs = generate_experiment_comparison_visuals(
            result_a_dir=run_dirs[0],
            result_b_dir=run_dirs[1],
            output_dir=comparison_dir,
            label_a=labels[0],
            label_b=labels[1],
            title=args.title,
        )
    elif len(experiment_paths) == 3:
        comparison_outputs = generate_experiment_three_way_comparison_visuals(
            result_a_dir=run_dirs[0],
            result_b_dir=run_dirs[1],
            result_c_dir=run_dirs[2],
            output_dir=comparison_dir,
            label_a=labels[0],
            label_b=labels[1],
            label_c=labels[2],
            title=args.title,
        )
    else:
        comparison_outputs = generate_experiment_multi_comparison_visuals(
            result_dirs=run_dirs,
            output_dir=comparison_dir,
            labels=labels,
            title=args.title,
        )
    comparison_summary = json.loads(Path(comparison_outputs["summary_json"]).read_text(encoding="utf-8"))

    run_output_dirs = {f"run_{index + 1}_output_dir": str(run_dirs[index]) for index in range(len(run_dirs))}
    manifest = {
        "experiments": [str(path) for path in experiment_paths],
        "labels": labels,
        "title": args.title,
        "comparison_output_dir": str(comparison_dir),
        "comparison_outputs": comparison_outputs,
        "comparison_metrics": comparison_summary.get("metrics", []),
        **run_output_dirs,
    }
    manifest_path = output_dir / "comparison_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()