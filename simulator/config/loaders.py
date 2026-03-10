from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from simulator.config.models import ConstraintsSection
from simulator.config.models import ExperimentConfig
from simulator.config.models import ExperimentInputs
from simulator.config.models import LinksSection
from simulator.config.models import MetaConfig
from simulator.config.models import MetricsConfig
from simulator.config.models import NodesSection
from simulator.config.models import RoutingSection
from simulator.config.models import SchedulerConfig
from simulator.config.models import SimulationConfig
from simulator.config.models import TopologyConfig
from simulator.config.models import TopologySection
from simulator.config.models import WorkloadConfig
from simulator.config.models import WorkloadJobConfig


class ConfigValidationError(ValueError):
    """Raised when a config file violates the project's input contract."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigValidationError(f"Config file does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigValidationError(f"YAML root must be a mapping: {path}")
    return data


def _meta_from_dict(raw: dict[str, Any]) -> MetaConfig:
    return MetaConfig(
        name=str(raw.get("name", "unnamed")),
        version=int(raw.get("version", 1)),
        description=str(raw.get("description", "")),
    )


def _resolve_input_path(config_path: Path, raw_path: str) -> Path:
    if not raw_path:
        raise ConfigValidationError(f"Missing required input path in: {config_path}")

    input_path = Path(raw_path)
    if input_path.is_absolute():
        if not input_path.exists():
            raise ConfigValidationError(f"Referenced input file does not exist: {input_path}")
        return input_path

    candidates = [
        (config_path.parent / input_path).resolve(),
        (config_path.parent.parent / input_path).resolve(),
        (config_path.parent.parent.parent / input_path).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ConfigValidationError(
        f"Referenced input file does not exist: {raw_path} resolved from {config_path}"
    )


def _ensure_mapping(raw: Any, section_name: str, path: Path) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(f"Section '{section_name}' must be a mapping in {path}")
    return raw


def _ensure_list(raw: Any, section_name: str, path: Path) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigValidationError(f"Section '{section_name}' must be a list in {path}")
    return raw


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigValidationError(message)


def _validate_topology_config(config: TopologyConfig, path: Path) -> None:
    _require(config.meta.name != "", f"Topology meta.name is required in {path}")
    _require(config.topology.type != "", f"Topology topology.type is required in {path}")
    _require(
        config.topology.mode in {"generated", "explicit"},
        f"Topology topology.mode must be 'generated' or 'explicit' in {path}",
    )
    _require(
        config.links.default_bandwidth_gbps > 0,
        f"Topology links.default_bandwidth_gbps must be > 0 in {path}",
    )
    _require(
        config.links.default_latency_us >= 0,
        f"Topology links.default_latency_us must be >= 0 in {path}",
    )

    if config.topology.mode == "generated":
        _require(config.nodes.host_count > 0, f"Topology nodes.host_count must be > 0 in {path}")
        _require(config.nodes.switch_count > 0, f"Topology nodes.switch_count must be > 0 in {path}")
        _require(config.nodes.gpu_per_host > 0, f"Topology nodes.gpu_per_host must be > 0 in {path}")

    if config.topology.mode == "explicit":
        _require(
            len(config.nodes.explicit_nodes) > 0,
            f"Topology explicit mode requires nodes.explicit_nodes in {path}",
        )
        _require(
            len(config.links.explicit_links) > 0,
            f"Topology explicit mode requires links.explicit_links in {path}",
        )
        for node in config.nodes.explicit_nodes:
            _require("node_id" in node, f"Each explicit node must contain node_id in {path}")
            _require("node_type" in node, f"Each explicit node must contain node_type in {path}")
        for link in config.links.explicit_links:
            _require("src" in link, f"Each explicit link must contain src in {path}")
            _require("dst" in link, f"Each explicit link must contain dst in {path}")


def _normalize_topology_config(config: TopologyConfig) -> TopologyConfig:
    if config.topology.mode == "generated" and config.nodes.gpu_per_host <= 0:
        raw_value = config.topology.parameters.get("gpu_per_host", 0)
        if isinstance(raw_value, int | float) and raw_value > 0:
            config.nodes.gpu_per_host = int(raw_value)
    return config


def _validate_workload_config(config: WorkloadConfig, path: Path) -> None:
    _require(config.meta.name != "", f"Workload meta.name is required in {path}")
    _require(len(config.jobs) > 0, f"Workload jobs must not be empty in {path}")
    for job in config.jobs:
        _require(job.job_id != "", f"Workload job_id is required in {path}")
        _require(job.arrival_time_ms >= 0, f"Workload arrival_time_ms must be >= 0 in {path}")
        _require(len(job.participants) > 0, f"Workload participants must not be empty for {job.job_id} in {path}")
        _require(job.total_data_mb > 0, f"Workload total_data_mb must be > 0 for {job.job_id} in {path}")
        _require(job.chunk_count > 0, f"Workload chunk_count must be > 0 for {job.job_id} in {path}")
        _require(job.compute_phase_ms >= 0, f"Workload compute_phase_ms must be >= 0 for {job.job_id} in {path}")
        _require(job.iteration_count > 0, f"Workload iteration_count must be > 0 for {job.job_id} in {path}")
        _require(job.repeat_interval_ms >= 0, f"Workload repeat_interval_ms must be >= 0 for {job.job_id} in {path}")
        _require(
            job.communication_pattern != "",
            f"Workload communication_pattern is required for {job.job_id} in {path}",
        )
        _require(job.dependency_mode != "", f"Workload dependency_mode is required for {job.job_id} in {path}")


def _validate_experiment_config(config: ExperimentConfig, path: Path) -> None:
    _require(config.meta.name != "", f"Experiment meta.name is required in {path}")
    _require(
        config.scheduler.type in {"crux", "teccl"},
        f"Experiment scheduler.type must be 'crux' or 'teccl' in {path}",
    )
    _require(config.simulation.max_time_ms > 0, f"Experiment simulation.max_time_ms must be > 0 in {path}")
    _require(config.simulation.repetitions > 0, f"Experiment simulation.repetitions must be > 0 in {path}")
    _require(config.metrics.output_dir != "", f"Experiment metrics.output_dir is required in {path}")

    if config.scheduler.type == "crux":
        _require(
            "max_priority_levels" in config.scheduler.crux or "hardware_priority_count" in config.scheduler.crux,
            (
                "Experiment scheduler.crux.max_priority_levels or "
                f"scheduler.crux.hardware_priority_count is required when scheduler.type=crux in {path}"
            ),
        )

    if config.scheduler.type == "teccl":
        _require(
            "epoch_size_ms" in config.scheduler.teccl,
            f"Experiment scheduler.teccl.epoch_size_ms is required when scheduler.type=teccl in {path}",
        )
        _require(
            "solver_backend" in config.scheduler.teccl,
            f"Experiment scheduler.teccl.solver_backend is required when scheduler.type=teccl in {path}",
        )


def load_topology_config(path: str | Path) -> TopologyConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    config = TopologyConfig(
        meta=_meta_from_dict(raw.get("meta", {})),
        topology=TopologySection(**_ensure_mapping(raw.get("topology", {}), "topology", config_path)),
        nodes=NodesSection(**_ensure_mapping(raw.get("nodes", {}), "nodes", config_path)),
        links=LinksSection(**_ensure_mapping(raw.get("links", {}), "links", config_path)),
        routing=RoutingSection(**_ensure_mapping(raw.get("routing", {}), "routing", config_path)),
        constraints=ConstraintsSection(**_ensure_mapping(raw.get("constraints", {}), "constraints", config_path)),
    )
    config = _normalize_topology_config(config)
    _validate_topology_config(config, config_path)
    return config


def load_workload_config(path: str | Path) -> WorkloadConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    jobs = [
        WorkloadJobConfig(**job)
        for job in _ensure_list(raw.get("jobs", []), "jobs", config_path)
    ]
    config = WorkloadConfig(meta=_meta_from_dict(raw.get("meta", {})), jobs=jobs)
    _validate_workload_config(config, config_path)
    return config


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    inputs = _ensure_mapping(raw.get("inputs", {}), "inputs", config_path)
    scheduler_raw = _ensure_mapping(raw.get("scheduler", {}), "scheduler", config_path)
    config = ExperimentConfig(
        meta=_meta_from_dict(raw.get("meta", {})),
        inputs=ExperimentInputs(
            topology_file=_resolve_input_path(config_path, inputs.get("topology_file", "")),
            workload_file=_resolve_input_path(config_path, inputs.get("workload_file", "")),
        ),
        scheduler=SchedulerConfig(
            type=str(scheduler_raw.get("type", "crux")),
            crux=dict(scheduler_raw.get("crux", {})),
            teccl=dict(scheduler_raw.get("teccl", {})),
        ),
        simulation=SimulationConfig(**_ensure_mapping(raw.get("simulation", {}), "simulation", config_path)),
        metrics=MetricsConfig(**_ensure_mapping(raw.get("metrics", {}), "metrics", config_path)),
    )
    _validate_experiment_config(config, config_path)
    return config
