from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from math import inf

from simulator.core.models import LinkState
from simulator.core.models import RuntimeState
from simulator.topology.models import Link
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class CruxPathLoad:
    path_id: str
    link_ids: list[str]
    max_link_utilization: float
    total_link_utilization: float
    max_projected_contention: int
    total_projected_contention: int
    bottleneck_bandwidth_gbps: float
    total_latency_ms: float
    estimated_transfer_time_ms: float


@dataclass(slots=True)
class CruxPathCandidate:
    path_id: str
    flow_id: str
    owner_job_id: str
    source_node: str
    destination_node: str
    node_path: list[str]
    hop_count: int
    chunk_size_mb: float
    load: CruxPathLoad


@dataclass(slots=True)
class CruxFlowInput:
    flow_id: str
    owner_job_id: str
    demand_id: str
    chunk_id: str
    source_node: str
    destination_node: str
    total_size_mb: float
    path_candidate_ids: list[str] = field(default_factory=list)
    best_candidate_path_id: str = ""
    best_candidate_transfer_time_ms: float = 0.0


@dataclass(slots=True)
class CruxIntensityInput:
    job_id: str
    compute_workload_w: float
    observed_comm_time_proxy_ms: float
    estimated_candidate_comm_time_ms: float
    selected_tj_ms: float
    intensity_value: float
    definition_mode: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CruxPriorityInput:
    job_id: str
    intensity_value: float
    dlt_factor_kj: float
    priority_score_pj: float
    raw_priority_rank: int = -1
    factor_mode: str = "neutral"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CruxJobInput:
    job_id: str
    arrival_time_ms: float
    participant_count: int
    chunk_count: int
    communication_volume_mb: float
    flow_ids: list[str] = field(default_factory=list)
    candidate_path_count: int = 0
    intensity: CruxIntensityInput | None = None
    priority: CruxPriorityInput | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CruxModelInput:
    topology_name: str
    hardware_priority_count: int
    intensity_definition_mode: str
    priority_factor_mode: str
    job_by_id: dict[str, CruxJobInput]
    flow_by_id: dict[str, CruxFlowInput]
    path_by_id: dict[str, CruxPathCandidate]
    flow_to_job_id: dict[str, str]
    flow_to_path_ids: dict[str, list[str]]
    summary: dict[str, int | float | str | bool]
    metadata: dict[str, object] = field(default_factory=dict)

    def to_debug_dict(self) -> dict[str, object]:
        return {
            "topology_name": self.topology_name,
            "hardware_priority_count": self.hardware_priority_count,
            "intensity_definition_mode": self.intensity_definition_mode,
            "priority_factor_mode": self.priority_factor_mode,
            "summary": dict(self.summary),
            "metadata": dict(self.metadata),
            "jobs": {job_id: asdict(job_input) for job_id, job_input in self.job_by_id.items()},
            "flows": {flow_id: asdict(flow_input) for flow_id, flow_input in self.flow_by_id.items()},
            "paths": {path_id: asdict(path_input) for path_id, path_input in self.path_by_id.items()},
            "flow_to_job_id": dict(self.flow_to_job_id),
            "flow_to_path_ids": {flow_id: list(path_ids) for flow_id, path_ids in self.flow_to_path_ids.items()},
        }


def build_crux_model_input(
    runtime_state: RuntimeState,
    observed_comm_time_ms: dict[str, float],
    candidate_path_limit: int,
    hardware_priority_count: int,
    intensity_definition_mode: str = "legacy_observed_comm_time_proxy",
    priority_factor_mode: str = "neutral",
) -> CruxModelInput:
    path_limit = max(1, candidate_path_limit)
    priority_count = max(1, hardware_priority_count)
    edge_lookup = _build_edge_lookup(runtime_state)
    link_by_id = {link.link_id: link for link in runtime_state.topology.links}

    job_by_id: dict[str, CruxJobInput] = {}
    flow_by_id: dict[str, CruxFlowInput] = {}
    path_by_id: dict[str, CruxPathCandidate] = {}
    flow_to_job_id: dict[str, str] = {}
    flow_to_path_ids: dict[str, list[str]] = {}
    unique_link_ids: set[str] = set()

    for job in runtime_state.active_jobs:
        communication_volume_mb = 0.0
        flow_ids: list[str] = []
        candidate_path_count = 0
        best_flow_transfer_times_ms: list[float] = []

        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_node in chunk.source_set:
                    for destination_node in chunk.destination_set:
                        if source_node == destination_node:
                            continue
                        flow_id = f"flow::{job.job_id}::{chunk.chunk_id}::{source_node}->{destination_node}"
                        communication_volume_mb += chunk.size_mb
                        flow_ids.append(flow_id)
                        flow_to_job_id[flow_id] = job.job_id
                        path_ids: list[str] = []
                        best_path_id = ""
                        best_transfer_time_ms = inf
                        candidate_paths = runtime_state.topology.candidate_paths.get((source_node, destination_node), [])[:path_limit]
                        for candidate_index, node_path in enumerate(candidate_paths):
                            link_ids = _path_to_link_ids(node_path, edge_lookup)
                            if not link_ids:
                                continue
                            unique_link_ids.update(link_ids)
                            path_id = f"path::{flow_id}::{candidate_index}"
                            path_load = _build_path_load(
                                path_id=path_id,
                                link_ids=link_ids,
                                chunk_size_mb=chunk.size_mb,
                                runtime_state=runtime_state,
                                link_by_id=link_by_id,
                            )
                            path_candidate = CruxPathCandidate(
                                path_id=path_id,
                                flow_id=flow_id,
                                owner_job_id=job.job_id,
                                source_node=source_node,
                                destination_node=destination_node,
                                node_path=list(node_path),
                                hop_count=max(0, len(node_path) - 1),
                                chunk_size_mb=chunk.size_mb,
                                load=path_load,
                            )
                            path_by_id[path_id] = path_candidate
                            path_ids.append(path_id)
                            candidate_path_count += 1
                            if _path_is_better(path_candidate, best_transfer_time_ms, best_path_id, path_by_id):
                                best_path_id = path_id
                                best_transfer_time_ms = path_load.estimated_transfer_time_ms

                        if best_transfer_time_ms < inf:
                            best_flow_transfer_times_ms.append(best_transfer_time_ms)
                        flow_by_id[flow_id] = CruxFlowInput(
                            flow_id=flow_id,
                            owner_job_id=job.job_id,
                            demand_id=demand.demand_id,
                            chunk_id=chunk.chunk_id,
                            source_node=source_node,
                            destination_node=destination_node,
                            total_size_mb=chunk.size_mb,
                            path_candidate_ids=path_ids,
                            best_candidate_path_id=best_path_id,
                            best_candidate_transfer_time_ms=0.0 if best_transfer_time_ms == inf else best_transfer_time_ms,
                        )
                        flow_to_path_ids[flow_id] = path_ids

        observed_proxy_ms = float(observed_comm_time_ms.get(job.job_id, max(job.compute_phase_ms, 1.0)) or max(job.compute_phase_ms, 1.0))
        estimated_candidate_comm_time_ms = max(best_flow_transfer_times_ms, default=observed_proxy_ms)
        selected_tj_ms = _select_tj_ms(
            intensity_definition_mode=intensity_definition_mode,
            observed_proxy_ms=observed_proxy_ms,
            estimated_candidate_comm_time_ms=estimated_candidate_comm_time_ms,
        )
        compute_workload_w = max(job.compute_phase_ms, 1e-6)
        intensity_value = compute_workload_w / max(selected_tj_ms, 1e-6)
        dlt_factor_kj = _compute_dlt_factor(job=job, priority_factor_mode=priority_factor_mode)
        priority_score_pj = intensity_value * dlt_factor_kj
        job_by_id[job.job_id] = CruxJobInput(
            job_id=job.job_id,
            arrival_time_ms=job.arrival_time_ms,
            participant_count=len(job.participants),
            chunk_count=sum(len(demand.chunks) for demand in job.communication_demands),
            communication_volume_mb=communication_volume_mb,
            flow_ids=flow_ids,
            candidate_path_count=candidate_path_count,
            intensity=CruxIntensityInput(
                job_id=job.job_id,
                compute_workload_w=compute_workload_w,
                observed_comm_time_proxy_ms=observed_proxy_ms,
                estimated_candidate_comm_time_ms=estimated_candidate_comm_time_ms,
                selected_tj_ms=selected_tj_ms,
                intensity_value=intensity_value,
                definition_mode=intensity_definition_mode,
                metadata={
                    "compute_workload_source": "UnifiedJob.compute_phase_ms",
                    "tj_proxy_source": intensity_definition_mode,
                },
            ),
            priority=CruxPriorityInput(
                job_id=job.job_id,
                intensity_value=intensity_value,
                dlt_factor_kj=dlt_factor_kj,
                priority_score_pj=priority_score_pj,
                factor_mode=priority_factor_mode,
                metadata={
                    "participant_count": len(job.participants),
                    "chunk_count": sum(len(demand.chunks) for demand in job.communication_demands),
                    "faithful_approximation": priority_factor_mode == "neutral",
                },
            ),
            metadata={
                "communication_pattern": str(job.metadata.get("communication_pattern", "")),
                "dependency_mode": str(job.metadata.get("dependency_mode", "")),
            },
        )

    ranked_jobs = sorted(
        job_by_id.values(),
        key=lambda job_input: (
            -(job_input.priority.priority_score_pj if job_input.priority is not None else 0.0),
            job_input.arrival_time_ms,
            job_input.job_id,
        ),
    )
    for rank_index, job_input in enumerate(ranked_jobs):
        if job_input.priority is not None:
            job_input.priority.raw_priority_rank = rank_index

    priority_scores = [
        job_input.priority.priority_score_pj
        for job_input in job_by_id.values()
        if job_input.priority is not None
    ]
    intensity_values = [
        job_input.intensity.intensity_value
        for job_input in job_by_id.values()
        if job_input.intensity is not None
    ]
    summary = {
        "job_count": len(job_by_id),
        "flow_count": len(flow_by_id),
        "path_candidate_count": len(path_by_id),
        "unique_link_count": len(unique_link_ids),
        "hardware_priority_count": priority_count,
        "average_intensity": sum(intensity_values) / len(intensity_values) if intensity_values else 0.0,
        "max_intensity": max(intensity_values, default=0.0),
        "average_priority_score": sum(priority_scores) / len(priority_scores) if priority_scores else 0.0,
        "max_priority_score": max(priority_scores, default=0.0),
    }
    metadata = {
        "compute_workload_mapping": "W_j := UnifiedJob.compute_phase_ms",
        "selected_tj_mapping": _describe_tj_mapping(intensity_definition_mode),
        "priority_factor_mapping": _describe_priority_factor_mapping(priority_factor_mode),
        "legacy_boundary_mode": intensity_definition_mode == "legacy_observed_comm_time_proxy" and priority_factor_mode == "neutral",
    }
    return CruxModelInput(
        topology_name=runtime_state.topology.name,
        hardware_priority_count=priority_count,
        intensity_definition_mode=intensity_definition_mode,
        priority_factor_mode=priority_factor_mode,
        job_by_id=job_by_id,
        flow_by_id=flow_by_id,
        path_by_id=path_by_id,
        flow_to_job_id=flow_to_job_id,
        flow_to_path_ids=flow_to_path_ids,
        summary=summary,
        metadata=metadata,
    )


def _build_edge_lookup(runtime_state: RuntimeState) -> dict[tuple[str, str], Link]:
    edge_lookup: dict[tuple[str, str], Link] = {}
    for link in runtime_state.topology.links:
        edge_lookup[(link.src, link.dst)] = link
        if link.bidirectional:
            edge_lookup[(link.dst, link.src)] = link
    return edge_lookup


def _path_to_link_ids(node_path: list[str], edge_lookup: dict[tuple[str, str], Link]) -> list[str]:
    link_ids: list[str] = []
    for source_node, destination_node in zip(node_path, node_path[1:]):
        link = edge_lookup.get((source_node, destination_node))
        if link is None:
            return []
        link_ids.append(link.link_id)
    return link_ids


def _build_path_load(
    path_id: str,
    link_ids: list[str],
    chunk_size_mb: float,
    runtime_state: RuntimeState,
    link_by_id: dict[str, Link],
) -> CruxPathLoad:
    max_link_utilization = 0.0
    total_link_utilization = 0.0
    max_projected_contention = 0
    total_projected_contention = 0
    bottleneck_bandwidth_gbps = inf
    total_latency_ms = 0.0

    for link_id in link_ids:
        link = link_by_id[link_id]
        link_state = runtime_state.link_states.get(link_id)
        link_utilization = float(link_state.utilization) if link_state is not None else 0.0
        active_flow_count = len(link_state.active_flows) if link_state is not None else 0
        projected_contention = max(1, active_flow_count + 1)
        projected_bandwidth_gbps = link.bandwidth_gbps / projected_contention if link.bandwidth_gbps > 0 else 0.0

        max_link_utilization = max(max_link_utilization, link_utilization)
        total_link_utilization += link_utilization
        max_projected_contention = max(max_projected_contention, projected_contention)
        total_projected_contention += projected_contention
        bottleneck_bandwidth_gbps = min(bottleneck_bandwidth_gbps, projected_bandwidth_gbps)
        total_latency_ms += link.latency_us / 1000.0

    if bottleneck_bandwidth_gbps == inf:
        bottleneck_bandwidth_gbps = 0.0
    transfer_time_ms = inf
    if bottleneck_bandwidth_gbps > 1e-12:
        transfer_time_ms = (chunk_size_mb / (bottleneck_bandwidth_gbps * 0.125)) + total_latency_ms

    return CruxPathLoad(
        path_id=path_id,
        link_ids=list(link_ids),
        max_link_utilization=max_link_utilization,
        total_link_utilization=total_link_utilization,
        max_projected_contention=max_projected_contention,
        total_projected_contention=total_projected_contention,
        bottleneck_bandwidth_gbps=bottleneck_bandwidth_gbps,
        total_latency_ms=total_latency_ms,
        estimated_transfer_time_ms=transfer_time_ms,
    )


def _path_is_better(
    candidate: CruxPathCandidate,
    best_transfer_time_ms: float,
    best_path_id: str,
    existing_paths: dict[str, CruxPathCandidate],
) -> bool:
    candidate_key = (
        candidate.load.estimated_transfer_time_ms,
        candidate.load.max_link_utilization,
        candidate.load.max_projected_contention,
        candidate.hop_count,
        candidate.path_id,
    )
    if not best_path_id:
        return True
    best_candidate = existing_paths.get(best_path_id)
    if best_candidate is None:
        return True
    best_key = (
        best_transfer_time_ms,
        best_candidate.load.max_link_utilization,
        best_candidate.load.max_projected_contention,
        best_candidate.hop_count,
        best_candidate.path_id,
    )
    return candidate_key < best_key


def _select_tj_ms(
    intensity_definition_mode: str,
    observed_proxy_ms: float,
    estimated_candidate_comm_time_ms: float,
) -> float:
    if intensity_definition_mode == "path_estimated_comm_time":
        return max(estimated_candidate_comm_time_ms, 1e-6)
    return max(observed_proxy_ms, 1e-6)


def _compute_dlt_factor(job: UnifiedJob, priority_factor_mode: str) -> float:
    if priority_factor_mode == "neutral":
        return 1.0
    if priority_factor_mode == "participant_scaled":
        return float(max(1, len(job.participants)))
    return 1.0


def _describe_tj_mapping(intensity_definition_mode: str) -> str:
    if intensity_definition_mode == "path_estimated_comm_time":
        return "t_j := max(best candidate estimated transfer time per flow)"
    return "t_j := observed communication time proxy from legacy scheduler baseline"


def _describe_priority_factor_mapping(priority_factor_mode: str) -> str:
    if priority_factor_mode == "participant_scaled":
        return "k_j := participant_count"
    return "k_j := 1.0 (stage-1 faithful approximation placeholder)"