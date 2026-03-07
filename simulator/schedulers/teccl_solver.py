from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING
from typing import Any

from simulator.schedulers.base import EpochAction

if TYPE_CHECKING:
    from simulator.core.models import RuntimeState
    from simulator.schedulers.teccl import TECCLChunkReplicaState
    from simulator.schedulers.teccl import TECCLJobState
    from simulator.schedulers.teccl import TECCLStrategy
    from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class SolverCandidateAction:
    replica_id: str
    current_node: str
    next_node: str
    ultimate_destination: str
    node_kind: str
    expected_arrival_epoch: int
    route_fragment: list[str]
    score: tuple[int, int, int, str, str]


@dataclass(slots=True)
class SolverConstraintReport:
    replica_id: str
    node_id: str
    node_kind: str
    incoming_count: int
    outgoing_count: int
    gpu_buffer_available: bool
    switch_replication_allowed: bool


@dataclass(slots=True)
class SolverResult:
    epoch_actions: list[EpochAction] = field(default_factory=list)
    selected_candidates: list[SolverCandidateAction] = field(default_factory=list)
    constraint_reports: list[SolverConstraintReport] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmallScaleDebugSolver:
    strategy: "TECCLStrategy"
    solver_name: str = field(default="small_scale_debug_solver", init=False)

    def solve_epoch(
        self,
        job: "UnifiedJob",
        job_state: "TECCLJobState",
        runtime_state: "RuntimeState",
        current_epoch: int,
    ) -> SolverResult:
        grouped_candidates: list[list[SolverCandidateAction]] = []
        constraint_reports: list[SolverConstraintReport] = []

        for replica_state in job_state.chunk_replicas.values():
            if replica_state.replica_id in job_state.completed_replica_ids:
                continue
            if not self._dependencies_satisfied(replica_state, job_state):
                continue
            replica_candidates, replica_reports = self._enumerate_replica_candidates(
                replica_state,
                runtime_state,
                current_epoch,
            )
            constraint_reports.extend(replica_reports)
            if replica_candidates:
                grouped_candidates.extend(replica_candidates)

        best_plan = self._search_best_plan(grouped_candidates)
        epoch_actions = [self._candidate_to_epoch_action(job_state, candidate) for candidate in best_plan]
        return SolverResult(
            epoch_actions=epoch_actions,
            selected_candidates=best_plan,
            constraint_reports=constraint_reports,
            metadata={
                "solver_name": self.solver_name,
                "candidate_group_count": len(grouped_candidates),
                "selected_action_count": len(best_plan),
            },
        )

    def _enumerate_replica_candidates(
        self,
        replica_state: "TECCLChunkReplicaState",
        runtime_state: "RuntimeState",
        current_epoch: int,
    ) -> tuple[list[list[SolverCandidateAction]], list[SolverConstraintReport]]:
        candidate_groups: list[list[SolverCandidateAction]] = []
        reports: list[SolverConstraintReport] = []

        for switch_id, arrival_epoch in sorted(replica_state.switch_arrivals.items()):
            incoming_count = 1 if arrival_epoch <= current_epoch else 0
            feasible_candidates: list[SolverCandidateAction] = []
            if arrival_epoch <= current_epoch:
                for destination in self._pending_destinations(replica_state, current_epoch, switch_id, runtime_state, for_switch=True):
                    path = self._shortest_path(runtime_state, switch_id, destination)
                    if len(path) < 2:
                        continue
                    next_hop = path[1]
                    feasible_candidates.append(
                        SolverCandidateAction(
                            replica_id=replica_state.replica_id,
                            current_node=switch_id,
                            next_node=next_hop,
                            ultimate_destination=destination,
                            node_kind="switch",
                            expected_arrival_epoch=current_epoch + self._link_delay_epochs(runtime_state, switch_id, next_hop),
                            route_fragment=[switch_id, next_hop],
                            score=self._candidate_score(replica_state, switch_id, destination, path),
                        )
                    )
            reports.append(
                SolverConstraintReport(
                    replica_id=replica_state.replica_id,
                    node_id=switch_id,
                    node_kind="switch",
                    incoming_count=incoming_count,
                    outgoing_count=len(feasible_candidates),
                    gpu_buffer_available=False,
                    switch_replication_allowed=self.strategy.allow_switch_replication,
                )
            )
            if feasible_candidates:
                if not self.strategy.allow_switch_replication:
                    candidate_groups.append(feasible_candidates)
                else:
                    candidate_groups.extend([[candidate] for candidate in feasible_candidates])

        for gpu_id, available_epoch in sorted(replica_state.gpu_buffers.items()):
            buffer_available = available_epoch <= current_epoch
            feasible_candidates = []
            if buffer_available:
                for destination in self._pending_destinations(replica_state, current_epoch, gpu_id, runtime_state, for_switch=False):
                    path = self._shortest_path(runtime_state, gpu_id, destination)
                    if len(path) < 2:
                        continue
                    next_hop = path[1]
                    feasible_candidates.append(
                        SolverCandidateAction(
                            replica_id=replica_state.replica_id,
                            current_node=gpu_id,
                            next_node=next_hop,
                            ultimate_destination=destination,
                            node_kind="gpu",
                            expected_arrival_epoch=current_epoch + self._link_delay_epochs(runtime_state, gpu_id, next_hop),
                            route_fragment=[gpu_id, next_hop],
                            score=self._candidate_score(replica_state, gpu_id, destination, path),
                        )
                    )
            reports.append(
                SolverConstraintReport(
                    replica_id=replica_state.replica_id,
                    node_id=gpu_id,
                    node_kind="gpu",
                    incoming_count=1 if buffer_available else 0,
                    outgoing_count=len(feasible_candidates),
                    gpu_buffer_available=buffer_available,
                    switch_replication_allowed=False,
                )
            )
            if feasible_candidates:
                if self.strategy.allow_gpu_replication:
                    candidate_groups.extend([[candidate] for candidate in feasible_candidates])
                else:
                    candidate_groups.append(feasible_candidates[:1])

        return candidate_groups, reports

    def _search_best_plan(
        self,
        grouped_candidates: list[list[SolverCandidateAction]],
    ) -> list[SolverCandidateAction]:
        best_plan: list[SolverCandidateAction] = []
        best_score: tuple[int, int, int] | None = None

        def dfs(index: int, chosen: list[SolverCandidateAction], used_switches: set[str]) -> None:
            nonlocal best_plan, best_score
            if index >= len(grouped_candidates):
                score = self._plan_score(chosen)
                if best_score is None or score > best_score:
                    best_score = score
                    best_plan = list(chosen)
                return

            dfs(index + 1, chosen, used_switches)
            for candidate in grouped_candidates[index]:
                if candidate.node_kind == "switch" and not self.strategy.allow_switch_replication:
                    if candidate.current_node in used_switches:
                        continue
                chosen.append(candidate)
                if candidate.node_kind == "switch":
                    used_switches.add(candidate.current_node)
                dfs(index + 1, chosen, used_switches)
                if candidate.node_kind == "switch":
                    used_switches.discard(candidate.current_node)
                chosen.pop()

        dfs(0, [], set())
        return sorted(best_plan, key=lambda item: (item.current_node, item.next_node, item.ultimate_destination))

    def _plan_score(self, chosen: list[SolverCandidateAction]) -> tuple[int, int, int]:
        delivered = len({(item.replica_id, item.ultimate_destination) for item in chosen})
        switch_hops = sum(1 for item in chosen if item.node_kind == "switch")
        shorter_paths = -sum(len(item.route_fragment) for item in chosen)
        return (delivered, switch_hops, shorter_paths)

    def _candidate_score(
        self,
        replica_state: "TECCLChunkReplicaState",
        current_node: str,
        destination: str,
        path: list[str],
    ) -> tuple[int, int, int, str, str]:
        direct_delivery = 1 if destination == path[-1] and len(path) == 2 else 0
        pending_count = len(replica_state.destination_gpus - replica_state.delivered_destinations)
        return (direct_delivery, -pending_count, -len(path), current_node, destination)

    def _candidate_to_epoch_action(
        self,
        job_state: "TECCLJobState",
        candidate: SolverCandidateAction,
    ) -> EpochAction:
        replica_state = job_state.chunk_replicas[candidate.replica_id]
        return EpochAction(
            epoch_index=job_state.current_epoch,
            chunk_id=replica_state.chunk_id,
            source_gpu=replica_state.source_gpu,
            current_node=candidate.current_node,
            next_node=candidate.next_node,
            expected_arrival_epoch=candidate.expected_arrival_epoch,
            route_fragment=list(candidate.route_fragment),
            metadata={
                "scheduler": "teccl",
                "replica_id": candidate.replica_id,
                "demand_id": replica_state.demand_id,
                "node_kind": candidate.node_kind,
                "ultimate_destination": candidate.ultimate_destination,
                "allow_replication": self.strategy.allow_gpu_replication if candidate.node_kind == "gpu" else self.strategy.allow_switch_replication,
                "buffer_enabled": self.strategy.enable_gpu_buffer if candidate.node_kind == "gpu" else self.strategy.enable_switch_buffer,
                "solver_backend": self.solver_name,
            },
        )

    def _pending_destinations(
        self,
        replica_state: "TECCLChunkReplicaState",
        current_epoch: int,
        current_node: str,
        runtime_state: "RuntimeState",
        for_switch: bool,
    ) -> list[str]:
        candidates = []
        for destination in sorted(replica_state.destination_gpus - replica_state.delivered_destinations):
            inflight_epoch = replica_state.inflight_destinations.get(destination)
            if inflight_epoch is not None:
                if for_switch and inflight_epoch > current_epoch:
                    continue
                if not for_switch and inflight_epoch >= current_epoch:
                    continue
            path = self._shortest_path(runtime_state, current_node, destination)
            if len(path) < 2:
                continue
            candidates.append((len(path), destination))
        return [destination for _, destination in sorted(candidates)]

    def _dependencies_satisfied(
        self,
        replica_state: "TECCLChunkReplicaState",
        job_state: "TECCLJobState",
    ) -> bool:
        for parent_chunk_id in replica_state.dependency_parent_ids:
            if not any(
                parent_replica_id.startswith(f"{parent_chunk_id}::") and parent_replica_id in job_state.completed_replica_ids
                for parent_replica_id in job_state.chunk_replicas
            ):
                return False
        return True

    def _shortest_path(self, runtime_state: "RuntimeState", src: str, dst: str) -> list[str]:
        if src == dst:
            return [src]
        queue: deque[list[str]] = deque([[src]])
        visited = {src}
        while queue:
            path = queue.popleft()
            current = path[-1]
            for neighbor in runtime_state.topology.adjacency.get(current, []):
                if neighbor in visited:
                    continue
                next_path = path + [neighbor]
                if neighbor == dst:
                    return next_path
                visited.add(neighbor)
                queue.append(next_path)
        return []

    def _link_delay_epochs(self, runtime_state: "RuntimeState", src: str, dst: str) -> int:
        for link in runtime_state.topology.links:
            if (link.src == src and link.dst == dst) or (link.bidirectional and link.src == dst and link.dst == src):
                link_latency_ms = link.latency_us / 1000.0
                return max(1, ceil(link_latency_ms / self.strategy.epoch_size_ms))
        return 1


@dataclass(slots=True)
class HeuristicTECCLSolver(SmallScaleDebugSolver):
    solver_name: str = field(default="heuristic_solver", init=False)

    def solve_epoch(
        self,
        job: "UnifiedJob",
        job_state: "TECCLJobState",
        runtime_state: "RuntimeState",
        current_epoch: int,
    ) -> SolverResult:
        grouped_candidates: list[list[SolverCandidateAction]] = []
        constraint_reports: list[SolverConstraintReport] = []

        for replica_state in job_state.chunk_replicas.values():
            if replica_state.replica_id in job_state.completed_replica_ids:
                continue
            if not self._dependencies_satisfied(replica_state, job_state):
                continue
            replica_candidates, replica_reports = self._enumerate_replica_candidates(
                replica_state,
                runtime_state,
                current_epoch,
            )
            constraint_reports.extend(replica_reports)
            if replica_candidates:
                grouped_candidates.extend(replica_candidates)

        chosen = self._greedy_select(grouped_candidates)
        epoch_actions = [self._candidate_to_epoch_action(job_state, candidate) for candidate in chosen]
        return SolverResult(
            epoch_actions=epoch_actions,
            selected_candidates=chosen,
            constraint_reports=constraint_reports,
            metadata={
                "solver_name": self.solver_name,
                "candidate_group_count": len(grouped_candidates),
                "selected_action_count": len(chosen),
                "applicability": "prefer medium/large candidate spaces where exhaustive search cost grows quickly",
                "error_boundary": "greedy heuristic does not guarantee optimal delivered destination count against small_scale_debug_solver",
            },
        )

    def _greedy_select(
        self,
        grouped_candidates: list[list[SolverCandidateAction]],
    ) -> list[SolverCandidateAction]:
        chosen: list[SolverCandidateAction] = []
        used_switches: set[str] = set()
        delivered_targets: set[tuple[str, str]] = set()

        for group in grouped_candidates:
            best_candidate: SolverCandidateAction | None = None
            for candidate in sorted(group, key=self._heuristic_sort_key):
                if candidate.node_kind == "switch" and not self.strategy.allow_switch_replication:
                    if candidate.current_node in used_switches:
                        continue
                target_key = (candidate.replica_id, candidate.ultimate_destination)
                if target_key in delivered_targets:
                    continue
                best_candidate = candidate
                break

            if best_candidate is None:
                continue
            chosen.append(best_candidate)
            delivered_targets.add((best_candidate.replica_id, best_candidate.ultimate_destination))
            if best_candidate.node_kind == "switch":
                used_switches.add(best_candidate.current_node)

        return sorted(chosen, key=lambda item: (item.current_node, item.next_node, item.ultimate_destination))

    def _heuristic_sort_key(self, candidate: SolverCandidateAction) -> tuple[int, int, int, int, str, str]:
        direct_delivery = 0 if len(candidate.route_fragment) == 2 else 1
        node_bias = 0 if candidate.node_kind == "gpu" else 1
        expected_arrival = candidate.expected_arrival_epoch
        path_len = len(candidate.route_fragment)
        return (direct_delivery, expected_arrival, node_bias, path_len, candidate.current_node, candidate.ultimate_destination)