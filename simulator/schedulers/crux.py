from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil
from time import perf_counter

from simulator.core.models import RuntimeState
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.schedulers.crux_model_input import CruxModelInput
from simulator.schedulers.crux_model_input import build_crux_model_input
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class CruxScheduler(Scheduler):
    max_priority_levels: int = 8
    hardware_priority_count: int | None = None
    candidate_path_limit: int = 8
    intensity_window_iterations: int = 3
    intensity_definition_mode: str = "legacy_observed_comm_time_proxy"
    priority_factor_mode: str = "neutral"
    observed_comm_time_ms: dict[str, float] = field(default_factory=dict)
    last_path_assignments: dict[str, list[str]] = field(default_factory=dict)
    last_priority_assignments: dict[str, int] = field(default_factory=dict)
    last_intensity_scores: dict[str, float] = field(default_factory=dict)
    last_model_input: dict[str, object] = field(default_factory=dict)
    last_scheduler_wall_time_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.hardware_priority_count is not None:
            self.max_priority_levels = max(1, int(self.hardware_priority_count))
        else:
            self.hardware_priority_count = max(1, int(self.max_priority_levels))

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        self.observed_comm_time_ms.setdefault(job.job_id, max(job.compute_phase_ms, 1.0))

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if not runtime_state.active_jobs:
            return False
        if not runtime_state.flow_states:
            return True
        return any(flow.status == "completed" for flow in runtime_state.flow_states.values())

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        started_at = perf_counter()
        self._refresh_observed_comm_time(runtime_state)
        model_input = self._build_model_input(runtime_state)
        provisional_link_loads: dict[str, int] = defaultdict(int)
        ranked_jobs = sorted(
            runtime_state.active_jobs,
            key=lambda job: (-self._intensity_score(job, model_input), job.arrival_time_ms, job.job_id),
        )
        decision = ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms,
            metadata={
                "scheduler": "crux",
                "execution_mode": "legacy_boundary_with_stage1_inputs",
                "intensity_scores": {},
            },
        )
        job_count = max(1, len(ranked_jobs))
        for index, job in enumerate(ranked_jobs):
            intensity = self._intensity_score(job, model_input)
            priority = self._compress_priority(index, job_count)
            decision.priority_assignments[job.job_id] = priority
            decision.metadata["intensity_scores"][job.job_id] = intensity
            for flow_id, path in self._select_paths_for_job(job, runtime_state, provisional_link_loads).items():
                decision.path_assignments[flow_id] = path

        self.last_priority_assignments = dict(decision.priority_assignments)
        self.last_intensity_scores = dict(decision.metadata["intensity_scores"])
        self.last_model_input = model_input.to_debug_dict()
        self.last_scheduler_wall_time_ms = (perf_counter() - started_at) * 1000.0
        return decision

    def export_debug_state(self) -> dict[str, object]:
        return {
            "observed_comm_time_ms": dict(self.observed_comm_time_ms),
            "last_priority_assignments": dict(self.last_priority_assignments),
            "last_intensity_scores": dict(self.last_intensity_scores),
            "last_path_assignments": dict(self.last_path_assignments),
            "crux_scheduler_wall_time_ms": self.last_scheduler_wall_time_ms,
            "stage0_baseline": self._stage0_baseline_inventory(),
            "crux_model_input": dict(self.last_model_input),
            "crux_model_summary": dict(self.last_model_input.get("summary", {})) if self.last_model_input else {},
        }

    def _build_model_input(self, runtime_state: RuntimeState) -> CruxModelInput:
        return build_crux_model_input(
            runtime_state=runtime_state,
            observed_comm_time_ms=self.observed_comm_time_ms,
            candidate_path_limit=self.candidate_path_limit,
            hardware_priority_count=max(1, int(self.hardware_priority_count or self.max_priority_levels)),
            intensity_definition_mode=self.intensity_definition_mode,
            priority_factor_mode=self.priority_factor_mode,
        )

    def _intensity_score(self, job: UnifiedJob, model_input: CruxModelInput | None = None) -> float:
        if model_input is not None:
            job_input = model_input.job_by_id.get(job.job_id)
            if job_input is not None and job_input.intensity is not None:
                return job_input.intensity.intensity_value
        comm_time = self.observed_comm_time_ms.get(job.job_id, 1.0)
        return job.compute_phase_ms / max(comm_time, 1e-6)

    def _refresh_observed_comm_time(self, runtime_state: RuntimeState) -> None:
        for job in runtime_state.active_jobs:
            job_flows = [
                flow
                for flow in runtime_state.flow_states.values()
                if flow.owner_job_id == job.job_id and flow.start_time_ms is not None
            ]
            completed_job_flows = [flow for flow in job_flows if flow.status == "completed" and flow.end_time_ms is not None]
            if completed_job_flows:
                start_time = min(flow.start_time_ms for flow in completed_job_flows if flow.start_time_ms is not None)
                end_time = max(flow.end_time_ms for flow in completed_job_flows if flow.end_time_ms is not None)
                self.observed_comm_time_ms[job.job_id] = max(end_time - start_time, 1e-6)
                continue

            active_flow_estimates = []
            for flow in runtime_state.flow_states.values():
                if flow.owner_job_id != job.job_id or flow.status != "active":
                    continue
                if flow.assigned_bandwidth_gbps > 1e-12:
                    elapsed = runtime_state.now_ms - (flow.start_time_ms or runtime_state.now_ms)
                    active_flow_estimates.append(elapsed + (flow.remaining_size_mb / (flow.assigned_bandwidth_gbps * 0.125)))
                elif flow.start_time_ms is not None:
                    active_flow_estimates.append(max(runtime_state.now_ms - flow.start_time_ms, 1e-6))
            if active_flow_estimates:
                self.observed_comm_time_ms[job.job_id] = max(max(active_flow_estimates), 1e-6)

    def _compress_priority(self, rank_index: int, job_count: int) -> int:
        if self.max_priority_levels <= 1:
            return 0
        bucket_size = max(1, ceil(job_count / self.max_priority_levels))
        return min(rank_index // bucket_size, self.max_priority_levels - 1)

    def _select_paths_for_job(
        self,
        job: UnifiedJob,
        runtime_state: RuntimeState,
        provisional_link_loads: dict[str, int],
    ) -> dict[str, list[str]]:
        assignments: dict[str, list[str]] = {}
        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_node in chunk.source_set:
                    for destination_node in chunk.destination_set:
                        if source_node == destination_node:
                            continue
                        flow_id = f"flow::{job.job_id}::{chunk.chunk_id}::{source_node}->{destination_node}"
                        path = self._select_best_path(
                            runtime_state,
                            flow_id,
                            source_node,
                            destination_node,
                            provisional_link_loads,
                        )
                        if path:
                            assignments[flow_id] = path
                            self.last_path_assignments[flow_id] = list(path)
                            self._reserve_path(path, runtime_state, provisional_link_loads)
        return assignments

    def _select_best_path(
        self,
        runtime_state: RuntimeState,
        flow_id: str,
        source_node: str,
        destination_node: str,
        provisional_link_loads: dict[str, int],
    ) -> list[str]:
        candidates = runtime_state.topology.candidate_paths.get((source_node, destination_node), [])
        if not candidates:
            return []

        limited_candidates = candidates[: self.candidate_path_limit]
        cached_path = self.last_path_assignments.get(flow_id)
        best_path = min(
            limited_candidates,
            key=lambda path: (
                self._path_cost(runtime_state, path, provisional_link_loads),
                0 if cached_path == path else 1,
                len(path),
                self._stable_path_rank(flow_id, path),
            ),
        )
        return list(best_path)

    def _path_cost(
        self,
        runtime_state: RuntimeState,
        path: list[str],
        provisional_link_loads: dict[str, int],
    ) -> tuple[float, int, int, int]:
        link_penalties: list[tuple[float, int]] = []
        for src, dst in zip(path, path[1:]):
            link_id, link_state = self._lookup_link_state(runtime_state, src, dst)
            if link_state is None or link_id is None:
                return (float("inf"), 1 << 30, 1 << 30, len(path))
            projected_contention = len(link_state.active_flows) + provisional_link_loads.get(link_id, 0)
            link_penalties.append((link_state.utilization, projected_contention))
        if not link_penalties:
            return (float("inf"), 1 << 30, 1 << 30, len(path))
        max_utilization = max(item[0] for item in link_penalties)
        max_projected_contention = max(item[1] for item in link_penalties)
        total_projected_contention = sum(item[1] for item in link_penalties)
        return (max_utilization, max_projected_contention, total_projected_contention, len(path))

    def _lookup_link_state(self, runtime_state: RuntimeState, src: str, dst: str):
        for link in runtime_state.topology.links:
            if link.src == src and link.dst == dst:
                return link.link_id, runtime_state.link_states.get(link.link_id)
            if link.bidirectional and link.src == dst and link.dst == src:
                return link.link_id, runtime_state.link_states.get(link.link_id)
        return None, None

    def _reserve_path(self, path: list[str], runtime_state: RuntimeState, provisional_link_loads: dict[str, int]) -> None:
        for src, dst in zip(path, path[1:]):
            link_id, _ = self._lookup_link_state(runtime_state, src, dst)
            if link_id is not None:
                provisional_link_loads[link_id] += 1

    def _stable_path_rank(self, flow_id: str, path: list[str]) -> int:
        digest = hashlib.sha1(f"{flow_id}|{'->'.join(path)}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False)

    def _stage0_baseline_inventory(self) -> dict[str, object]:
        return {
            "status": "completed_2026_03_10",
            "legacy_scheduler_boundary": {
                "scheduler_file": "simulator/schedulers/crux.py",
                "legacy_methods": [
                    "_refresh_observed_comm_time",
                    "_compress_priority",
                    "_select_paths_for_job",
                    "_select_best_path",
                    "_path_cost",
                ],
                "legacy_runtime_semantics": [
                    "runtime bandwidth sharing is still max_min_fair in stage 0/1",
                    "priority compression still uses bucketized rank mapping in stage 0/1",
                ],
            },
            "legacy_preservation_inventory": [
                "retain existing job-level CRUX scheduling entrypoint and ScheduleDecision contract",
                "retain existing observed_comm_time_ms baseline so regression behavior remains comparable",
                "retain exporter/reporting fields consumed by existing CRUX vs TE-CCL comparison scripts",
            ],
            "reused_interfaces": {
                "runtime": [
                    "RuntimeState.active_jobs",
                    "RuntimeState.flow_states",
                    "RuntimeState.link_states",
                    "ScheduleDecision.priority_assignments",
                    "ScheduleDecision.path_assignments",
                ],
                "exporters": [
                    "ExperimentRunner scheduler.export_debug_state",
                    "summary.json aggregate metrics",
                    "scheduler_debug.json scheduler_debug_state",
                ],
            },
            "new_interface_impact": [
                "stage 1 adds simulator/schedulers/crux_model_input.py as the canonical CRUX input mapping layer",
                "scheduler_debug now exports crux_model_input and crux_model_summary for later stage validation",
                "CruxScheduler accepts hardware_priority_count as a forward-compatible alias of max_priority_levels",
            ],
        }
