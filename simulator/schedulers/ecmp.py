from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from simulator.core.models import RuntimeState
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class EcmpScheduler(Scheduler):
    stable_per_flow: bool = True
    last_path_assignments: dict[str, list[str]] = field(default_factory=dict)
    _next_path_index_by_pair: dict[tuple[str, str], int] = field(default_factory=dict)
    _selection_count_by_pair: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        return None

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if not runtime_state.active_jobs:
            return False
        if not runtime_state.flow_states:
            return True
        return any(flow.status == "completed" for flow in runtime_state.flow_states.values())

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        decision = ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms,
            metadata={
                "scheduler": "ecmp",
                "routing_mode": "stable_per_flow" if self.stable_per_flow else "round_robin",
                "path_selection_count_by_pair": {},
            },
        )

        for job in runtime_state.active_jobs:
            for flow_id, path in self._selected_paths_for_job(runtime_state, job).items():
                decision.path_assignments[flow_id] = path

        decision.metadata["path_selection_count_by_pair"] = dict(self._selection_count_by_pair)
        return decision

    def export_debug_state(self) -> dict[str, Any]:
        return {
            "routing_mode": "stable_per_flow" if self.stable_per_flow else "round_robin",
            "last_path_assignments": dict(self.last_path_assignments),
            "next_path_index_by_pair": {
                f"{source}->{destination}": index
                for (source, destination), index in self._next_path_index_by_pair.items()
            },
            "path_selection_count_by_pair": dict(self._selection_count_by_pair),
        }

    def _selected_paths_for_job(self, runtime_state: RuntimeState, job: UnifiedJob) -> dict[str, list[str]]:
        assignments: dict[str, list[str]] = {}
        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_node in chunk.source_set:
                    for destination_node in chunk.destination_set:
                        if source_node == destination_node:
                            continue
                        flow_id = f"flow::{job.job_id}::{chunk.chunk_id}::{source_node}->{destination_node}"
                        path = self._select_path_for_flow(runtime_state, flow_id, source_node, destination_node)
                        if not path:
                            continue
                        assignments[flow_id] = path
                        self.last_path_assignments[flow_id] = list(path)
        return assignments

    def _select_path_for_flow(
        self,
        runtime_state: RuntimeState,
        flow_id: str,
        source_node: str,
        destination_node: str,
    ) -> list[str]:
        candidate_paths = runtime_state.topology.candidate_paths.get((source_node, destination_node), [])
        if not candidate_paths:
            return []

        if self.stable_per_flow:
            selected_index = self._stable_flow_index(flow_id, len(candidate_paths))
        else:
            pair = (source_node, destination_node)
            selected_index = self._next_path_index_by_pair.get(pair, 0) % len(candidate_paths)
            self._next_path_index_by_pair[pair] = (selected_index + 1) % len(candidate_paths)

        key = f"{source_node}->{destination_node}"
        self._selection_count_by_pair[key] += 1
        return list(candidate_paths[selected_index])

    def _stable_flow_index(self, flow_id: str, candidate_count: int) -> int:
        digest = sha256(flow_id.encode("utf-8")).digest()
        hash_value = int.from_bytes(digest[:8], byteorder="big", signed=False)
        return hash_value % max(1, candidate_count)
