from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MetaConfig:
    name: str
    version: int = 1
    description: str = ""


@dataclass(slots=True)
class TopologySection:
    mode: str
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NodesSection:
    host_count: int = 0
    switch_count: int = 0
    gpu_per_host: int = 0
    explicit_nodes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class LinksSection:
    default_bandwidth_gbps: float
    default_latency_us: float
    bidirectional: bool = True
    explicit_links: list[dict[str, Any]] = field(default_factory=list)
    overrides: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class RoutingSection:
    ecmp: bool = False
    max_paths_per_pair: int = 1
    path_selection_mode: str = "k_shortest"


@dataclass(slots=True)
class ConstraintsSection:
    oversubscription_ratio: float = 1.0
    switch_buffer_mb: float = 0.0
    host_nic_bandwidth_gbps: float = 0.0


@dataclass(slots=True)
class TopologyConfig:
    meta: MetaConfig
    topology: TopologySection
    nodes: NodesSection
    links: LinksSection
    routing: RoutingSection
    constraints: ConstraintsSection


@dataclass(slots=True)
class WorkloadJobConfig:
    job_id: str
    arrival_time_ms: float
    participants: list[str]
    communication_pattern: str
    total_data_mb: float
    chunk_count: int
    compute_phase_ms: float
    iteration_count: int
    repeat_interval_ms: float
    dependency_mode: str


@dataclass(slots=True)
class WorkloadConfig:
    meta: MetaConfig
    jobs: list[WorkloadJobConfig]


@dataclass(slots=True)
class SchedulerConfig:
    type: str
    crux: dict[str, Any] = field(default_factory=dict)
    teccl: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SimulationConfig:
    time_unit: str
    max_time_ms: int
    bandwidth_sharing_model: str
    random_seed: int
    repetitions: int


@dataclass(slots=True)
class MetricsConfig:
    export_csv: bool = True
    export_json: bool = True
    export_trace: bool = False
    output_dir: str = "results"


@dataclass(slots=True)
class ExperimentInputs:
    topology_file: Path
    workload_file: Path


@dataclass(slots=True)
class ExperimentConfig:
    meta: MetaConfig
    inputs: ExperimentInputs
    scheduler: SchedulerConfig
    simulation: SimulationConfig
    metrics: MetricsConfig
