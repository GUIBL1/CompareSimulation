from __future__ import annotations

from collections import deque
import hashlib
from dataclasses import dataclass, field
from math import ceil
from time import perf_counter
from typing import Any

from simulator.core.models import RuntimeState
from simulator.schedulers.base import EpochAction
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.schedulers.teccl_highs_backend import TECCLHighsSolveConfig
from simulator.schedulers.teccl_highs_backend import solve_teccl_milp
from simulator.schedulers.teccl_metrics import build_teccl_solver_stats
from simulator.schedulers.teccl_milp_builder import TECCLMILPBuildConfig
from simulator.schedulers.teccl_milp_builder import build_teccl_milp_model
from simulator.schedulers.teccl_model_input import build_teccl_model_input
from simulator.schedulers.teccl_model_input import infer_planning_horizon_epochs
from simulator.schedulers.teccl_runtime_adapter import build_teccl_plan_decision
from simulator.schedulers.teccl_solver import ExactMILPTECCLSolver
from simulator.schedulers.teccl_solver import HeuristicTECCLSolver
from simulator.schedulers.teccl_solver import SmallScaleDebugSolver
from simulator.schedulers.teccl_solution_decoder import TECCLExecutionPlan
from simulator.schedulers.teccl_solution_decoder import decode_teccl_solution
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class TECCLChunkReplicaState:
    replica_id: str
    job_id: str
    demand_id: str
    chunk_id: str
    source_gpu: str
    destination_gpus: set[str]
    dependency_parent_ids: list[str] = field(default_factory=list)
    delivered_destinations: set[str] = field(default_factory=set)
    inflight_destinations: dict[str, int] = field(default_factory=dict)
    gpu_buffers: dict[str, int] = field(default_factory=dict)
    switch_arrivals: dict[str, int] = field(default_factory=dict)
    last_epoch_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TECCLJobState:
    job_id: str
    current_epoch: int = 0
    chunk_replicas: dict[str, TECCLChunkReplicaState] = field(default_factory=dict)
    processed_flow_ids: set[str] = field(default_factory=set)
    completed_replica_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TECCLStrategy:
    epoch_size_ms: float = 1.0
    solver_backend: str = "small_scale_debug_solver"
    max_solver_time_ms: int = 120000
    max_epoch_count: int | None = None
    planning_horizon_epochs: int | None = None
    mip_gap: float | None = None
    solver_threads: int | None = None
    enforce_integrality: bool = True
    objective_mode: str = "weighted_early_completion"
    switch_buffer_policy: str = "zero"
    solver_log_to_console: bool = False
    extract_all_variable_values: bool = True
    allow_gpu_replication: bool = True
    allow_switch_replication: bool = False
    enable_gpu_buffer: bool = True
    enable_switch_buffer: bool = False


@dataclass(slots=True)
class TECCLScheduler(Scheduler):
    strategy: TECCLStrategy = field(default_factory=TECCLStrategy)
    pending_jobs: list[str] = field(default_factory=list)
    job_states: dict[str, TECCLJobState] = field(default_factory=dict)
    last_solver_report: dict[str, object] = field(default_factory=dict)
    planned_execution: TECCLExecutionPlan | None = None
    emitted_plan_epochs: set[int] = field(default_factory=set)
    teccl_solver_stats: dict[str, object] = field(default_factory=dict)
    planner_model_summary: dict[str, object] = field(default_factory=dict)

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        if job.job_id not in self.pending_jobs:
            self.pending_jobs.append(job.job_id)
        self.job_states.setdefault(job.job_id, self._build_job_state(job))

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if self.strategy.epoch_size_ms <= 0:
            raise ValueError("epoch_size_ms must be positive")
        epoch_position = runtime_state.now_ms / self.strategy.epoch_size_ms
        return abs(epoch_position - round(epoch_position)) < 1e-9

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        current_epoch = int(ceil(runtime_state.now_ms / self.strategy.epoch_size_ms))
        if self.strategy.solver_backend == "highs":
            return self._compute_planned_schedule(runtime_state, current_epoch)
        epoch_actions: list[EpochAction] = []
        solver_reports: dict[str, object] = {}
        for job in runtime_state.active_jobs:
            job_state = self.job_states.setdefault(job.job_id, self._build_job_state(job))
            job_state.current_epoch = current_epoch
            self._synchronize_job_state(job_state, runtime_state, current_epoch)
            solver_result = self._solve_job_epoch(job, job_state, runtime_state, current_epoch)
            self._apply_solver_result(job_state, solver_result)
            epoch_actions.extend(solver_result.epoch_actions)
            solver_reports[job.job_id] = {
                "metadata": solver_result.metadata,
                "constraint_reports": [
                    {
                        "replica_id": report.replica_id,
                        "node_id": report.node_id,
                        "node_kind": report.node_kind,
                        "incoming_count": report.incoming_count,
                        "outgoing_count": report.outgoing_count,
                        "gpu_buffer_available": report.gpu_buffer_available,
                        "switch_replication_allowed": report.switch_replication_allowed,
                    }
                    for report in solver_result.constraint_reports
                ],
                "selected_candidates": [
                    {
                        "replica_id": candidate.replica_id,
                        "current_node": candidate.current_node,
                        "next_node": candidate.next_node,
                        "ultimate_destination": candidate.ultimate_destination,
                        "node_kind": candidate.node_kind,
                        "expected_arrival_epoch": candidate.expected_arrival_epoch,
                    }
                    for candidate in solver_result.selected_candidates
                ],
            }
        self.last_solver_report = solver_reports
        return ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms + self.strategy.epoch_size_ms,
            epoch_actions=epoch_actions,
            metadata={
                "scheduler": "teccl",
                "solver_backend": self.strategy.solver_backend,
                "allow_gpu_replication": self.strategy.allow_gpu_replication,
                "allow_switch_replication": self.strategy.allow_switch_replication,
                "enable_gpu_buffer": self.strategy.enable_gpu_buffer,
                "enable_switch_buffer": self.strategy.enable_switch_buffer,
                "job_states": self._build_debug_job_summary(),
                "solver_reports": solver_reports,
            },
        )

    def export_debug_state(self) -> dict[str, object]:
        return {
            "pending_jobs": list(self.pending_jobs),
            "strategy": {
                "epoch_size_ms": self.strategy.epoch_size_ms,
                "solver_backend": self.strategy.solver_backend,
                "max_epoch_count": self.strategy.max_epoch_count,
                "planning_horizon_epochs": self.strategy.planning_horizon_epochs,
                "mip_gap": self.strategy.mip_gap,
                "solver_threads": self.strategy.solver_threads,
                "enforce_integrality": self.strategy.enforce_integrality,
                "objective_mode": self.strategy.objective_mode,
                "switch_buffer_policy": self.strategy.switch_buffer_policy,
                "allow_gpu_replication": self.strategy.allow_gpu_replication,
                "allow_switch_replication": self.strategy.allow_switch_replication,
                "enable_gpu_buffer": self.strategy.enable_gpu_buffer,
                "enable_switch_buffer": self.strategy.enable_switch_buffer,
            },
            "job_states": self._build_debug_job_summary(),
            "solver_reports": dict(self.last_solver_report),
            "teccl_solver_stats": dict(self.teccl_solver_stats),
            "teccl_plan_summary": {
                "planned_transfer_count": len(self.planned_execution.all_transfers) if self.planned_execution else 0,
                "planned_epoch_count": len(self.planned_execution.transfers_by_epoch) if self.planned_execution else 0,
                "emitted_epoch_count": len(self.emitted_plan_epochs),
                "planner_model_summary": dict(self.planner_model_summary),
            },
        }

    def _compute_planned_schedule(self, runtime_state: RuntimeState, current_epoch: int) -> ScheduleDecision:
        if self.planned_execution is None:
            self._build_planned_execution(runtime_state)
        decision = build_teccl_plan_decision(
            plan=self.planned_execution,
            current_epoch=current_epoch,
            decision_time_ms=runtime_state.now_ms,
            epoch_size_ms=self.strategy.epoch_size_ms,
            solver_stats=self.teccl_solver_stats,
            emitted_epochs=self.emitted_plan_epochs,
        )
        if decision.epoch_actions:
            self.emitted_plan_epochs.add(current_epoch)
        return decision

    def _build_planned_execution(self, runtime_state: RuntimeState) -> None:
        planning_horizon_epochs = self.strategy.max_epoch_count or self.strategy.planning_horizon_epochs
        if planning_horizon_epochs is None:
            planning_horizon_epochs = infer_planning_horizon_epochs(
                jobs=runtime_state.active_jobs,
                topology=runtime_state.topology,
                epoch_size_ms=self.strategy.epoch_size_ms,
                max_time_ms=float(runtime_state.metadata.get("simulation_max_time_ms", 0.0) or 0.0),
            )

        wall_start = perf_counter()
        build_start = perf_counter()
        model_input = build_teccl_model_input(
            topology=runtime_state.topology,
            jobs=runtime_state.active_jobs,
            epoch_size_ms=self.strategy.epoch_size_ms,
            planning_horizon_epochs=planning_horizon_epochs,
        )
        build_result = build_teccl_milp_model(
            model_input=model_input,
            config=TECCLMILPBuildConfig(
                enforce_integrality=self.strategy.enforce_integrality,
                objective_mode=self.strategy.objective_mode,
                switch_buffer_policy=self.strategy.switch_buffer_policy,
            ),
        )
        model_build_time_ms = (perf_counter() - build_start) * 1000.0
        solve_result = solve_teccl_milp(
            build_result=build_result,
            config=TECCLHighsSolveConfig(
                max_solver_time_ms=self.strategy.max_solver_time_ms,
                mip_gap=self.strategy.mip_gap,
                solver_threads=self.strategy.solver_threads,
                log_to_console=self.strategy.solver_log_to_console,
                extract_all_variable_values=self.strategy.extract_all_variable_values,
            ),
        )
        if not solve_result.has_usable_solution:
            raise ValueError(f"HiGHS planner did not produce an executable TE-CCL plan: {solve_result.model_status}")
        total_wall_time_ms = (perf_counter() - wall_start) * 1000.0
        self.planned_execution = decode_teccl_solution(build_result, solve_result)
        self.planner_model_summary = dict(build_result.summary)
        self.teccl_solver_stats = build_teccl_solver_stats(
            experiment_name=str(runtime_state.metadata.get("experiment_name", "unknown_experiment")),
            solver_backend="highs",
            topology=runtime_state.topology,
            jobs=runtime_state.active_jobs,
            model_input=model_input,
            build_result=build_result,
            solve_result=solve_result,
            model_build_time_ms=model_build_time_ms,
            total_wall_time_ms=total_wall_time_ms,
        ).to_dict()
        self.last_solver_report = {
            "planner": {
                "status": solve_result.model_status,
                "has_usable_solution": solve_result.has_usable_solution,
                "objective_value": solve_result.objective_value,
                "planned_transfer_count": len(self.planned_execution.all_transfers),
                "planned_epoch_count": len(self.planned_execution.transfers_by_epoch),
            }
        }

    def _solve_job_epoch(
        self,
        job: UnifiedJob,
        job_state: TECCLJobState,
        runtime_state: RuntimeState,
        current_epoch: int,
    ):
        if self.strategy.solver_backend == "small_scale_debug_solver":
            solver = SmallScaleDebugSolver(strategy=self.strategy)
            return solver.solve_epoch(job, job_state, runtime_state, current_epoch)
        if self.strategy.solver_backend == "exact_milp_solver":
            solver = ExactMILPTECCLSolver(strategy=self.strategy)
            return solver.solve_epoch(job, job_state, runtime_state, current_epoch)
        if self.strategy.solver_backend == "heuristic_solver":
            solver = HeuristicTECCLSolver(strategy=self.strategy)
            return solver.solve_epoch(job, job_state, runtime_state, current_epoch)
        if self.strategy.solver_backend == "highs":
            raise RuntimeError("highs backend should use the planned MILP execution path")
        raise ValueError(f"Unsupported TECCL solver_backend: {self.strategy.solver_backend}")

    def _build_epoch_actions(
        self,
        job: UnifiedJob,
        job_state: TECCLJobState,
        runtime_state: RuntimeState,
        current_epoch: int,
    ) -> list[EpochAction]:
        actions: list[EpochAction] = []
        if not job.participants:
            return actions
        for replica_state in job_state.chunk_replicas.values():
            if replica_state.replica_id in job_state.completed_replica_ids:
                continue
            if not self._dependencies_satisfied(replica_state, job_state):
                continue

            scheduled_from_switch = False
            for switch_id, arrival_epoch in list(replica_state.switch_arrivals.items()):
                if arrival_epoch > current_epoch:
                    continue
                next_destination = self._select_switch_destination(replica_state, current_epoch, switch_id, runtime_state)
                if next_destination is None:
                    replica_state.switch_arrivals.pop(switch_id, None)
                    continue
                path = self._shortest_path(
                    runtime_state,
                    switch_id,
                    next_destination,
                    tie_break_key=f"{replica_state.replica_id}::{switch_id}::{next_destination}::switch",
                )
                if len(path) < 2:
                    replica_state.switch_arrivals.pop(switch_id, None)
                    continue
                next_hop = path[1]
                actions.append(
                    self._create_epoch_action(
                        replica_state=replica_state,
                        current_node=switch_id,
                        next_node=next_hop,
                        current_epoch=current_epoch,
                        runtime_state=runtime_state,
                        route_fragment=[switch_id, next_hop],
                        node_kind="switch",
                        ultimate_destination=next_destination,
                    )
                )
                replica_state.inflight_destinations[next_destination] = self._link_delay_epochs(runtime_state, switch_id, next_hop) + current_epoch
                replica_state.last_epoch_actions.append({
                    "epoch": current_epoch,
                    "current_node": switch_id,
                    "next_node": next_hop,
                    "ultimate_destination": next_destination,
                    "node_kind": "switch",
                })
                replica_state.switch_arrivals.pop(switch_id, None)
                scheduled_from_switch = True
                if not self.strategy.allow_switch_replication:
                    break

            for gpu_id, available_epoch in sorted(replica_state.gpu_buffers.items()):
                if available_epoch > current_epoch:
                    continue
                pending_destinations = self._select_gpu_destinations(replica_state, current_epoch, gpu_id, runtime_state)
                if not pending_destinations:
                    continue
                max_targets = len(pending_destinations) if self.strategy.allow_gpu_replication else 1
                for destination in pending_destinations[:max_targets]:
                    path = self._shortest_path(
                        runtime_state,
                        gpu_id,
                        destination,
                        tie_break_key=f"{replica_state.replica_id}::{gpu_id}::{destination}::gpu",
                    )
                    if len(path) < 2:
                        continue
                    next_hop = path[1]
                    actions.append(
                        self._create_epoch_action(
                            replica_state=replica_state,
                            current_node=gpu_id,
                            next_node=next_hop,
                            current_epoch=current_epoch,
                            runtime_state=runtime_state,
                            route_fragment=[gpu_id, next_hop],
                            node_kind="gpu",
                            ultimate_destination=destination,
                        )
                    )
                    replica_state.inflight_destinations[destination] = self._link_delay_epochs(runtime_state, gpu_id, next_hop) + current_epoch
                    replica_state.last_epoch_actions.append({
                        "epoch": current_epoch,
                        "current_node": gpu_id,
                        "next_node": next_hop,
                        "ultimate_destination": destination,
                        "node_kind": "gpu",
                    })
                if scheduled_from_switch and not self.strategy.allow_gpu_replication:
                    break
        return actions

    def _apply_solver_result(self, job_state: TECCLJobState, solver_result) -> None:
        for candidate in solver_result.selected_candidates:
            replica_state = job_state.chunk_replicas.get(candidate.replica_id)
            if replica_state is None:
                continue
            replica_state.inflight_destinations[candidate.ultimate_destination] = candidate.expected_arrival_epoch
            replica_state.last_epoch_actions.append(
                {
                    "epoch": job_state.current_epoch,
                    "current_node": candidate.current_node,
                    "next_node": candidate.next_node,
                    "ultimate_destination": candidate.ultimate_destination,
                    "node_kind": candidate.node_kind,
                }
            )
            if candidate.node_kind == "switch":
                replica_state.switch_arrivals.pop(candidate.current_node, None)

    def _build_job_state(self, job: UnifiedJob) -> TECCLJobState:
        job_state = TECCLJobState(job_id=job.job_id)
        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_gpu in chunk.source_set:
                    destination_gpus = {destination for destination in chunk.destination_set if destination != source_gpu}
                    replica_id = f"{chunk.chunk_id}::{source_gpu}"
                    job_state.chunk_replicas[replica_id] = TECCLChunkReplicaState(
                        replica_id=replica_id,
                        job_id=job.job_id,
                        demand_id=demand.demand_id,
                        chunk_id=chunk.chunk_id,
                        source_gpu=source_gpu,
                        destination_gpus=destination_gpus,
                        dependency_parent_ids=list(chunk.dependency_parent_ids),
                        delivered_destinations={source_gpu} if source_gpu in chunk.destination_set else set(),
                        gpu_buffers={source_gpu: 0} if self.strategy.enable_gpu_buffer else {},
                    )
        return job_state

    def _synchronize_job_state(self, job_state: TECCLJobState, runtime_state: RuntimeState, current_epoch: int) -> None:
        for flow in runtime_state.flow_states.values():
            if flow.owner_job_id != job_state.job_id or flow.flow_id in job_state.processed_flow_ids:
                continue
            if flow.status != "completed":
                continue
            if flow.metadata.get("scheduler") != "teccl":
                continue

            job_state.processed_flow_ids.add(flow.flow_id)
            replica_id = flow.metadata.get("replica_id")
            if not replica_id or replica_id not in job_state.chunk_replicas:
                continue

            replica_state = job_state.chunk_replicas[replica_id]
            arrival_node = flow.destination_node or flow.current_node
            if arrival_node is None:
                continue
            expected_arrival_epoch = int(flow.metadata.get("expected_arrival_epoch", current_epoch))
            arrival_epoch = max(current_epoch, expected_arrival_epoch)
            node_type = self._node_type(runtime_state, arrival_node)
            if node_type == "gpu" and self.strategy.enable_gpu_buffer:
                replica_state.gpu_buffers[arrival_node] = min(replica_state.gpu_buffers.get(arrival_node, arrival_epoch), arrival_epoch)
                if arrival_node in replica_state.destination_gpus:
                    replica_state.delivered_destinations.add(arrival_node)
                    replica_state.inflight_destinations.pop(arrival_node, None)
            elif node_type == "switch":
                replica_state.switch_arrivals[arrival_node] = min(
                    replica_state.switch_arrivals.get(arrival_node, arrival_epoch),
                    arrival_epoch,
                )

            if replica_state.destination_gpus.issubset(replica_state.delivered_destinations):
                job_state.completed_replica_ids.add(replica_id)

    def _dependencies_satisfied(self, replica_state: TECCLChunkReplicaState, job_state: TECCLJobState) -> bool:
        for parent_chunk_id in replica_state.dependency_parent_ids:
            if not any(
                parent_replica_id.startswith(f"{parent_chunk_id}::") and parent_replica_id in job_state.completed_replica_ids
                for parent_replica_id in job_state.chunk_replicas
            ):
                return False
        return True

    def _select_switch_destination(
        self,
        replica_state: TECCLChunkReplicaState,
        current_epoch: int,
        current_node: str,
        runtime_state: RuntimeState,
    ) -> str | None:
        destinations = []
        for destination in sorted(replica_state.destination_gpus - replica_state.delivered_destinations):
            inflight_epoch = replica_state.inflight_destinations.get(destination)
            if inflight_epoch is not None and inflight_epoch > current_epoch:
                continue
            path = self._shortest_path(
                runtime_state,
                current_node,
                destination,
                tie_break_key=f"{replica_state.replica_id}::{current_node}::{destination}::switch-dest",
            )
            if len(path) < 2:
                continue
            destinations.append((len(path), destination))
        destinations = [destination for _, destination in sorted(destinations)]
        return destinations[0] if destinations else None

    def _select_gpu_destinations(
        self,
        replica_state: TECCLChunkReplicaState,
        current_epoch: int,
        current_node: str,
        runtime_state: RuntimeState,
    ) -> list[str]:
        candidates = []
        for destination in sorted(replica_state.destination_gpus - replica_state.delivered_destinations):
            inflight_epoch = replica_state.inflight_destinations.get(destination)
            if inflight_epoch is not None and inflight_epoch >= current_epoch:
                continue
            path = self._shortest_path(
                runtime_state,
                current_node,
                destination,
                tie_break_key=f"{replica_state.replica_id}::{current_node}::{destination}::gpu-dest",
            )
            if len(path) < 2:
                continue
            candidates.append((len(path), destination))
        return [destination for _, destination in sorted(candidates)]

    def _create_epoch_action(
        self,
        replica_state: TECCLChunkReplicaState,
        current_node: str,
        next_node: str,
        current_epoch: int,
        runtime_state: RuntimeState,
        route_fragment: list[str],
        node_kind: str,
        ultimate_destination: str,
    ) -> EpochAction:
        arrival_epoch = current_epoch + self._link_delay_epochs(runtime_state, current_node, next_node)
        return EpochAction(
            epoch_index=current_epoch,
            chunk_id=replica_state.chunk_id,
            source_gpu=replica_state.source_gpu,
            current_node=current_node,
            next_node=next_node,
            expected_arrival_epoch=arrival_epoch,
            route_fragment=route_fragment,
            metadata={
                "scheduler": "teccl",
                "replica_id": replica_state.replica_id,
                "demand_id": replica_state.demand_id,
                "node_kind": node_kind,
                "ultimate_destination": ultimate_destination,
                "allow_replication": self.strategy.allow_gpu_replication if node_kind == "gpu" else self.strategy.allow_switch_replication,
                "buffer_enabled": self.strategy.enable_gpu_buffer if node_kind == "gpu" else self.strategy.enable_switch_buffer,
            },
        )

    def _link_delay_epochs(self, runtime_state: RuntimeState, src: str, dst: str) -> int:
        for link in runtime_state.topology.links:
            if (link.src == src and link.dst == dst) or (link.bidirectional and link.src == dst and link.dst == src):
                link_latency_ms = link.latency_us / 1000.0
                return max(1, ceil(link_latency_ms / self.strategy.epoch_size_ms))
        return 1

    def _node_type(self, runtime_state: RuntimeState, node_id: str) -> str:
        node = runtime_state.topology.nodes.get(node_id)
        return node.node_type if node is not None else "unknown"

    def _shortest_path(
        self,
        runtime_state: RuntimeState,
        src: str,
        dst: str,
        tie_break_key: str | None = None,
    ) -> list[str]:
        if src == dst:
            return [src]
        queue: deque[list[str]] = deque([[src]])
        candidates: list[list[str]] = []
        shortest_length: int | None = None
        while queue:
            path = queue.popleft()
            current = path[-1]
            if shortest_length is not None and len(path) > shortest_length:
                continue
            if current == dst:
                shortest_length = len(path)
                candidates.append(path)
                continue
            for neighbor in runtime_state.topology.adjacency.get(current, []):
                if neighbor in path:
                    continue
                queue.append(path + [neighbor])
        if not candidates:
            return []
        return list(
            min(
                candidates,
                key=lambda path: (
                    self._path_cost(runtime_state, path),
                    self._stable_path_rank(tie_break_key or f"{src}->{dst}", path),
                ),
            )
        )

    def _path_cost(self, runtime_state: RuntimeState, path: list[str]) -> tuple[float, int, int]:
        link_penalties: list[tuple[float, int]] = []
        for src, dst in zip(path, path[1:]):
            link_state = self._lookup_link_state(runtime_state, src, dst)
            if link_state is None:
                return (float("inf"), 1 << 30, len(path))
            link_penalties.append((link_state.utilization, len(link_state.active_flows)))
        if not link_penalties:
            return (float("inf"), 1 << 30, len(path))
        return (
            max(item[0] for item in link_penalties),
            sum(item[1] for item in link_penalties),
            len(path),
        )

    def _lookup_link_state(self, runtime_state: RuntimeState, src: str, dst: str):
        for link in runtime_state.topology.links:
            if (link.src == src and link.dst == dst) or (link.bidirectional and link.src == dst and link.dst == src):
                return runtime_state.link_states.get(link.link_id)
        return None

    def _stable_path_rank(self, tie_break_key: str, path: list[str]) -> int:
        digest = hashlib.sha1(f"{tie_break_key}|{'->'.join(path)}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False)

    def _build_debug_job_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {}
        for job_id, job_state in self.job_states.items():
            summary[job_id] = {
                "current_epoch": job_state.current_epoch,
                "processed_flow_ids": len(job_state.processed_flow_ids),
                "completed_replica_ids": sorted(job_state.completed_replica_ids),
                "chunk_replicas": {
                    replica_id: {
                        "delivered_destinations": sorted(replica_state.delivered_destinations),
                        "pending_destinations": sorted(replica_state.destination_gpus - replica_state.delivered_destinations),
                        "inflight_destinations": dict(replica_state.inflight_destinations),
                        "gpu_buffers": dict(replica_state.gpu_buffers),
                        "switch_arrivals": dict(replica_state.switch_arrivals),
                    }
                    for replica_id, replica_state in job_state.chunk_replicas.items()
                },
            }
        return summary
