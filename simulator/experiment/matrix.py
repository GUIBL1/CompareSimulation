from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class MatrixDefaults:
    results_root: str
    repetitions: int
    simulation: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    repeatability: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublicCase:
    case_id: str
    family: str
    topology_file: Path
    workload_file: Path
    random_seed: int
    public_baseline: dict[str, Any] = field(default_factory=dict)
    scheduler_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParameterSweep:
    sweep_id: str
    family: str
    base_case_id: str
    scheduler_type: str
    parameter_name: str
    values: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FairComparisonMatrix:
    name: str
    version: int
    description: str
    source_path: Path
    defaults: MatrixDefaults
    private_parameter_ranges: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    public_cases: list[PublicCase] = field(default_factory=list)
    parameter_sweeps: list[ParameterSweep] = field(default_factory=list)


def load_fair_comparison_matrix(path: str | Path) -> FairComparisonMatrix:
    config_path = Path(path).resolve()
    raw = _read_yaml(config_path)
    meta = _ensure_mapping(raw.get("meta", {}), "meta", config_path)
    defaults_raw = _ensure_mapping(raw.get("defaults", {}), "defaults", config_path)
    defaults = MatrixDefaults(
        results_root=str(defaults_raw.get("results_root", "results/fair_comparison_matrix")),
        repetitions=int(defaults_raw.get("repetitions", 1)),
        simulation=dict(_ensure_mapping(defaults_raw.get("simulation", {}), "defaults.simulation", config_path)),
        metrics=dict(_ensure_mapping(defaults_raw.get("metrics", {}), "defaults.metrics", config_path)),
        repeatability=dict(_ensure_mapping(defaults_raw.get("repeatability", {}), "defaults.repeatability", config_path)),
    )

    matrix = FairComparisonMatrix(
        name=str(meta.get("name", "fair_comparison_matrix")),
        version=int(meta.get("version", 1)),
        description=str(meta.get("description", "")),
        source_path=config_path,
        defaults=defaults,
        private_parameter_ranges={
            str(scheduler_type): {
                str(parameter_name): list(values)
                for parameter_name, values in _ensure_mapping(parameters, f"private_parameter_ranges.{scheduler_type}", config_path).items()
            }
            for scheduler_type, parameters in _ensure_mapping(raw.get("private_parameter_ranges", {}), "private_parameter_ranges", config_path).items()
        },
        public_cases=[_build_public_case(item, config_path) for item in _ensure_list(raw.get("public_cases", []), "public_cases", config_path)],
        parameter_sweeps=[_build_parameter_sweep(item, config_path) for item in _ensure_list(raw.get("parameter_sweeps", []), "parameter_sweeps", config_path)],
    )
    _validate_matrix(matrix)
    return matrix


def enumerate_public_run_pairs(matrix: FairComparisonMatrix) -> list[dict[str, Any]]:
    run_specs: list[dict[str, Any]] = []
    for case in matrix.public_cases:
        for scheduler_type in ("crux", "teccl"):
            run_specs.append(
                {
                    "case_id": case.case_id,
                    "family": case.family,
                    "scheduler_type": scheduler_type,
                    "topology_file": str(case.topology_file),
                    "workload_file": str(case.workload_file),
                    "random_seed": case.random_seed,
                    "repetitions": matrix.defaults.repetitions,
                    "simulation": dict(matrix.defaults.simulation),
                    "metrics": {
                        **dict(matrix.defaults.metrics),
                        "output_dir": f"{matrix.defaults.results_root}/{case.family}/{case.case_id}/{scheduler_type}",
                    },
                    "public_baseline": dict(case.public_baseline),
                    "scheduler_parameters": dict(case.scheduler_overrides.get(scheduler_type, {})),
                    "repeatability": dict(matrix.defaults.repeatability),
                }
            )
    return run_specs


def enumerate_parameter_sweep_runs(matrix: FairComparisonMatrix) -> list[dict[str, Any]]:
    case_lookup = {case.case_id: case for case in matrix.public_cases}
    run_specs: list[dict[str, Any]] = []
    for sweep in matrix.parameter_sweeps:
        base_case = case_lookup[sweep.base_case_id]
        base_parameters = dict(base_case.scheduler_overrides.get(sweep.scheduler_type, {}))
        for value in sweep.values:
            sweep_parameters = dict(base_parameters)
            sweep_parameters[sweep.parameter_name] = value
            run_specs.append(
                {
                    "sweep_id": sweep.sweep_id,
                    "family": sweep.family,
                    "base_case_id": sweep.base_case_id,
                    "scheduler_type": sweep.scheduler_type,
                    "parameter_name": sweep.parameter_name,
                    "parameter_value": value,
                    "topology_file": str(base_case.topology_file),
                    "workload_file": str(base_case.workload_file),
                    "random_seed": base_case.random_seed,
                    "repetitions": matrix.defaults.repetitions,
                    "simulation": dict(matrix.defaults.simulation),
                    "metrics": {
                        **dict(matrix.defaults.metrics),
                        "output_dir": (
                            f"{matrix.defaults.results_root}/{sweep.family}/{sweep.sweep_id}/"
                            f"{sweep.scheduler_type}/{sweep.parameter_name}_{value}"
                        ),
                    },
                    "public_baseline": dict(base_case.public_baseline),
                    "scheduler_parameters": sweep_parameters,
                    "repeatability": dict(matrix.defaults.repeatability),
                }
            )
    return run_specs


def _build_public_case(raw: dict[str, Any], config_path: Path) -> PublicCase:
    return PublicCase(
        case_id=str(raw.get("case_id", "")),
        family=str(raw.get("family", "")),
        topology_file=_resolve_ref_path(config_path, str(raw.get("topology_file", ""))),
        workload_file=_resolve_ref_path(config_path, str(raw.get("workload_file", ""))),
        random_seed=int(raw.get("random_seed", 0)),
        public_baseline=dict(_ensure_mapping(raw.get("public_baseline", {}), "public_baseline", config_path)),
        scheduler_overrides={
            str(scheduler_type): dict(_ensure_mapping(parameters, f"scheduler_overrides.{scheduler_type}", config_path))
            for scheduler_type, parameters in _ensure_mapping(raw.get("scheduler_overrides", {}), "scheduler_overrides", config_path).items()
        },
        notes=[str(item) for item in _ensure_list(raw.get("notes", []), "notes", config_path)],
    )


def _build_parameter_sweep(raw: dict[str, Any], config_path: Path) -> ParameterSweep:
    return ParameterSweep(
        sweep_id=str(raw.get("sweep_id", "")),
        family=str(raw.get("family", "parameter_sensitivity")),
        base_case_id=str(raw.get("base_case_id", "")),
        scheduler_type=str(raw.get("scheduler_type", "")),
        parameter_name=str(raw.get("parameter_name", "")),
        values=list(_ensure_list(raw.get("values", []), "values", config_path)),
        notes=[str(item) for item in _ensure_list(raw.get("notes", []), "notes", config_path)],
    )


def _validate_matrix(matrix: FairComparisonMatrix) -> None:
    if not matrix.public_cases:
        raise ValueError("Fair comparison matrix must contain at least one public case")
    if matrix.defaults.repetitions <= 0:
        raise ValueError("Fair comparison matrix defaults.repetitions must be > 0")

    case_ids: set[str] = set()
    for case in matrix.public_cases:
        if not case.case_id:
            raise ValueError("Public case case_id is required")
        if case.case_id in case_ids:
            raise ValueError(f"Duplicate public case id: {case.case_id}")
        case_ids.add(case.case_id)
        if not case.family:
            raise ValueError(f"Public case family is required for {case.case_id}")
        if not case.topology_file.exists():
            raise ValueError(f"Referenced topology file does not exist: {case.topology_file}")
        if not case.workload_file.exists():
            raise ValueError(f"Referenced workload file does not exist: {case.workload_file}")
        for scheduler_type in ("crux", "teccl"):
            if scheduler_type not in case.scheduler_overrides:
                raise ValueError(f"Public case {case.case_id} is missing scheduler_overrides.{scheduler_type}")

    sweep_ids: set[str] = set()
    for sweep in matrix.parameter_sweeps:
        if not sweep.sweep_id:
            raise ValueError("Parameter sweep sweep_id is required")
        if sweep.sweep_id in sweep_ids:
            raise ValueError(f"Duplicate parameter sweep id: {sweep.sweep_id}")
        sweep_ids.add(sweep.sweep_id)
        if sweep.base_case_id not in case_ids:
            raise ValueError(f"Parameter sweep {sweep.sweep_id} references unknown base_case_id: {sweep.base_case_id}")
        if sweep.scheduler_type not in {"crux", "teccl"}:
            raise ValueError(f"Parameter sweep {sweep.sweep_id} has unsupported scheduler_type: {sweep.scheduler_type}")
        allowed_parameters = matrix.private_parameter_ranges.get(sweep.scheduler_type, {})
        if sweep.parameter_name not in allowed_parameters:
            raise ValueError(
                f"Parameter sweep {sweep.sweep_id} parameter '{sweep.parameter_name}' is not declared in private_parameter_ranges.{sweep.scheduler_type}"
            )
        if not sweep.values:
            raise ValueError(f"Parameter sweep {sweep.sweep_id} must define at least one value")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _ensure_mapping(raw: Any, section_name: str, path: Path) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Section '{section_name}' must be a mapping in {path}")
    return raw


def _ensure_list(raw: Any, section_name: str, path: Path) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Section '{section_name}' must be a list in {path}")
    return raw


def _resolve_ref_path(config_path: Path, raw_path: str) -> Path:
    if not raw_path:
        raise ValueError(f"Missing referenced file path in {config_path}")
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = [
        (config_path.parent / path).resolve(),
        (config_path.parent.parent / path).resolve(),
        (config_path.parent.parent.parent / path).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]