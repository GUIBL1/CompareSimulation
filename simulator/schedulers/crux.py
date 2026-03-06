from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from simulator.core.models import RuntimeState
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class CruxScheduler(Scheduler):
    max_priority_levels: int = 8
    candidate_path_limit: int = 8
    intensity_window_iterations: int = 3
    observed_comm_time_ms: dict[str, float] = field(default_factory=dict)
    last_path_assignments: dict[str, list[str]] = field(default_factory=dict)
    last_priority_assignments: dict[str, int] = field(default_factory=dict)
    last_intensity_scores: dict[str, float] = field(default_factory=dict)

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        self.observed_comm_time_ms.setdefault(job.job_id, max(job.compute_phase_ms, 1.0))

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if not runtime_state.active_jobs:
            return False
        if not runtime_state.flow_states:
            return True
        return any(flow.status == "completed" for flow in runtime_state.flow_states.values())

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        self._refresh_observed_comm_time(runtime_state)
        ranked_jobs = sorted(
            runtime_state.active_jobs,
            key=lambda job: (-self._intensity_score(job), job.arrival_time_ms, job.job_id),
        )
        decision = ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms,
            metadata={
                "scheduler": "crux",
                "intensity_scores": {},
            },
        )
        job_count = max(1, len(ranked_jobs))
        for index, job in enumerate(ranked_jobs):
            intensity = self._intensity_score(job)
            priority = self._compress_priority(index, job_count)
            decision.priority_assignments[job.job_id] = priority
            decision.metadata["intensity_scores"][job.job_id] = intensity
            for flow_id, path in self._select_paths_for_job(job, runtime_state).items():
                decision.path_assignments[flow_id] = path

        self.last_priority_assignments = dict(decision.priority_assignments)
        self.last_intensity_scores = dict(decision.metadata["intensity_scores"])
        return decision

    def export_debug_state(self) -> dict[str, object]:
        return {
            "observed_comm_time_ms": dict(self.observed_comm_time_ms),
            "last_priority_assignments": dict(self.last_priority_assignments),
            "last_intensity_scores": dict(self.last_intensity_scores),
            "last_path_assignments": dict(self.last_path_assignments),
        }

    def _intensity_score(self, job: UnifiedJob) -> float:
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

    def _select_paths_for_job(self, job: UnifiedJob, runtime_state: RuntimeState) -> dict[str, list[str]]:
        assignments: dict[str, list[str]] = {}
        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_node in chunk.source_set:
                    for destination_node in chunk.destination_set:
                        if source_node == destination_node:
                            continue
                        flow_id = f"flow::{job.job_id}::{chunk.chunk_id}::{source_node}->{destination_node}"
                        path = self._select_best_path(runtime_state, flow_id, source_node, destination_node)
                        if path:
                            assignments[flow_id] = path
                            self.last_path_assignments[flow_id] = list(path)
        return assignments

    def _select_best_path(
        self,
        runtime_state: RuntimeState,
        flow_id: str,
        source_node: str,
        destination_node: str,
    ) -> list[str]:
        candidates = runtime_state.topology.candidate_paths.get((source_node, destination_node), [])
        if not candidates:
            return []

        limited_candidates = candidates[: self.candidate_path_limit]
        cached_path = self.last_path_assignments.get(flow_id)
        best_path = min(
            limited_candidates,
            key=lambda path: (
                self._path_cost(runtime_state, path),
                0 if cached_path == path else 1,
                len(path),
                tuple(path),
            ),
        )
        return list(best_path)

    def _path_cost(self, runtime_state: RuntimeState, path: list[str]) -> tuple[float, float, int]:
        link_penalties: list[tuple[float, float]] = []
        for src, dst in zip(path, path[1:]):
            link_state = self._lookup_link_state(runtime_state, src, dst)
            if link_state is None:
                return (float("inf"), float("inf"), len(path))
            contention = len(link_state.active_flows)
            link_penalties.append((link_state.utilization, contention))
        if not link_penalties:
            return (float("inf"), float("inf"), len(path))
        max_utilization = max(item[0] for item in link_penalties)
        total_contention = sum(item[1] for item in link_penalties)
        return (max_utilization, total_contention, len(path))

    def _lookup_link_state(self, runtime_state: RuntimeState, src: str, dst: str):
        for link in runtime_state.topology.links:
            if link.src == src and link.dst == dst:
                return runtime_state.link_states.get(link.link_id)
            if link.bidirectional and link.src == dst and link.dst == src:
                return runtime_state.link_states.get(link.link_id)
        return None
