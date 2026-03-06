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


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _meta_from_dict(raw: dict[str, Any]) -> MetaConfig:
    return MetaConfig(
        name=str(raw.get("name", "unnamed")),
        version=int(raw.get("version", 1)),
        description=str(raw.get("description", "")),
    )


def _resolve_input_path(config_path: Path, raw_path: str) -> Path:
    input_path = Path(raw_path)
    if input_path.is_absolute():
        return input_path

    candidates = [
        (config_path.parent / input_path).resolve(),
        (config_path.parent.parent / input_path).resolve(),
        (config_path.parent.parent.parent / input_path).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def load_topology_config(path: str | Path) -> TopologyConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    return TopologyConfig(
        meta=_meta_from_dict(raw.get("meta", {})),
        topology=TopologySection(**raw.get("topology", {})),
        nodes=NodesSection(**raw.get("nodes", {})),
        links=LinksSection(**raw.get("links", {})),
        routing=RoutingSection(**raw.get("routing", {})),
        constraints=ConstraintsSection(**raw.get("constraints", {})),
    )


def load_workload_config(path: str | Path) -> WorkloadConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    jobs = [WorkloadJobConfig(**job) for job in raw.get("jobs", [])]
    return WorkloadConfig(meta=_meta_from_dict(raw.get("meta", {})), jobs=jobs)


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    inputs = raw.get("inputs", {})
    scheduler_raw = raw.get("scheduler", {})
    return ExperimentConfig(
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
        simulation=SimulationConfig(**raw.get("simulation", {})),
        metrics=MetricsConfig(**raw.get("metrics", {})),
    )
