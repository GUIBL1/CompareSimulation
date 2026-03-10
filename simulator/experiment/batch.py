from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from simulator.experiment.matrix import enumerate_parameter_sweep_runs
from simulator.experiment.matrix import enumerate_public_run_pairs
from simulator.experiment.matrix import load_fair_comparison_matrix
from simulator.experiment.runner import ExperimentRunner


def run_fair_comparison_matrix(
    matrix_path: str | Path,
    include_public: bool = True,
    include_sweeps: bool = False,
    case_ids: list[str] | None = None,
    sweep_ids: list[str] | None = None,
    max_public_runs: int | None = None,
    max_sweep_runs: int | None = None,
    generated_experiment_dir: str | Path | None = None,
) -> dict[str, Any]:
    matrix = load_fair_comparison_matrix(matrix_path)
    workspace_root = matrix.source_path.parent.parent.parent
    generated_dir = Path(generated_experiment_dir).resolve() if generated_experiment_dir else (workspace_root / "configs/experiment/generated").resolve()
    generated_dir.mkdir(parents=True, exist_ok=True)

    selected_specs: list[dict[str, Any]] = []
    if include_public:
        public_specs = _filter_public_specs(enumerate_public_run_pairs(matrix), case_ids)
        if max_public_runs is not None:
            public_specs = public_specs[:max_public_runs]
        selected_specs.extend(public_specs)
    if include_sweeps:
        sweep_specs = _filter_sweep_specs(enumerate_parameter_sweep_runs(matrix), sweep_ids)
        if max_sweep_runs is not None:
            sweep_specs = sweep_specs[:max_sweep_runs]
        selected_specs.extend(sweep_specs)

    run_records: list[dict[str, Any]] = []
    for spec in selected_specs:
        experiment_file = materialize_experiment_from_spec(spec, generated_dir)
        runner = ExperimentRunner(experiment_file)
        result = runner.export_results()
        run_records.append(
            {
                "run_id": _spec_run_id(spec),
                "run_kind": "parameter_sweep" if "sweep_id" in spec else "public_case",
                "scheduler_type": spec["scheduler_type"],
                "experiment_file": str(experiment_file),
                "output_dir": str(result.output_dir),
                "aggregate_metrics": dict(result.aggregate_metrics),
                "exported_files": dict(result.exported_files),
            }
        )

    manifest = {
        "matrix_path": str(matrix.source_path),
        "generated_experiment_dir": str(generated_dir),
        "run_count": len(run_records),
        "runs": run_records,
    }
    manifest_path = _manifest_path(matrix, workspace_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def materialize_experiment_from_spec(spec: dict[str, Any], generated_dir: str | Path) -> Path:
    output_dir = Path(generated_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{_spec_run_id(spec)}.yaml"
    experiment_file = output_dir / file_name
    experiment_file.write_text(
        yaml.safe_dump(_build_experiment_document(spec), sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    return experiment_file


def _build_experiment_document(spec: dict[str, Any]) -> dict[str, Any]:
    scheduler_type = str(spec["scheduler_type"])
    scheduler_parameters = dict(spec.get("scheduler_parameters", {}))
    simulation = dict(spec.get("simulation", {}))
    simulation["random_seed"] = spec["random_seed"]
    simulation["repetitions"] = spec["repetitions"]
    metrics = dict(spec.get("metrics", {}))

    return {
        "meta": {
            "name": _spec_run_id(spec),
            "version": 1,
            "description": _spec_description(spec),
        },
        "inputs": {
            "topology_file": spec["topology_file"],
            "workload_file": spec["workload_file"],
        },
        "scheduler": {
            "type": scheduler_type,
            "crux": _default_crux_scheduler_block(scheduler_parameters if scheduler_type == "crux" else None),
            "teccl": _default_teccl_scheduler_block(scheduler_parameters if scheduler_type == "teccl" else None),
        },
        "simulation": simulation,
        "metrics": metrics,
    }


def _default_crux_scheduler_block(overrides: dict[str, Any] | None) -> dict[str, Any]:
    block = {
        "max_priority_levels": 4,
        "hardware_priority_count": 4,
        "candidate_path_limit": 4,
        "intensity_window_iterations": 3,
        "intensity_definition_mode": "selected_path_max_flow_time",
        "priority_factor_mode": "dlt_aware",
    }
    if overrides:
        block.update(overrides)
    return block


def _default_teccl_scheduler_block(overrides: dict[str, Any] | None) -> dict[str, Any]:
    block = {
        "epoch_size_ms": 1.0,
        "solver_backend": "highs",
        "max_solver_time_ms": 5000,
        "planning_horizon_epochs": 32,
        "solver_threads": 4,
        "enforce_integrality": True,
        "objective_mode": "weighted_early_completion",
        "switch_buffer_policy": "zero",
        "allow_gpu_replication": True,
        "allow_switch_replication": False,
        "enable_gpu_buffer": True,
        "enable_switch_buffer": False,
    }
    if overrides:
        block.update(overrides)
    return block


def _spec_run_id(spec: dict[str, Any]) -> str:
    if "sweep_id" in spec:
        return (
            f"{spec['family']}__{spec['sweep_id']}__{spec['scheduler_type']}__"
            f"{spec['parameter_name']}_{spec['parameter_value']}"
        )
    return f"{spec['family']}__{spec['case_id']}__{spec['scheduler_type']}"


def _spec_description(spec: dict[str, Any]) -> str:
    if "sweep_id" in spec:
        return (
            f"Matrix sweep {spec['sweep_id']} for {spec['scheduler_type']} on {spec['base_case_id']} "
            f"with {spec['parameter_name']}={spec['parameter_value']}"
        )
    return f"Matrix public case {spec['case_id']} for {spec['scheduler_type']}"


def _filter_public_specs(specs: list[dict[str, Any]], case_ids: list[str] | None) -> list[dict[str, Any]]:
    if not case_ids:
        return specs
    allowed = set(case_ids)
    return [spec for spec in specs if spec["case_id"] in allowed]


def _filter_sweep_specs(specs: list[dict[str, Any]], sweep_ids: list[str] | None) -> list[dict[str, Any]]:
    if not sweep_ids:
        return specs
    allowed = set(sweep_ids)
    return [spec for spec in specs if spec["sweep_id"] in allowed]


def _manifest_path(matrix, workspace_root: Path) -> Path:
    return (workspace_root / matrix.defaults.results_root / "batch_manifest.json").resolve()