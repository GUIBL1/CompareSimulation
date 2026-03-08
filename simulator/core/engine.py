from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import inf

from simulator.config.models import ExperimentConfig
from simulator.core.models import FlowState
from simulator.core.models import LinkState
from simulator.core.models import RuntimeEvent
from simulator.core.models import RuntimeState
from simulator.schedulers.base import EpochAction
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.topology.models import Link
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class RuntimeEngine:
    max_time_ms: float
    bandwidth_sharing_model: str = "max_min_fair"
    _link_lookup: dict[tuple[str, str], Link] = field(default_factory=dict, init=False)

    def run(self, runtime: RuntimeState, scheduler: Scheduler, config: ExperimentConfig) -> RuntimeState:
        if self.bandwidth_sharing_model != "max_min_fair":
            raise ValueError(f"Unsupported bandwidth_sharing_model: {self.bandwidth_sharing_model}")

        self._link_lookup = self._build_link_lookup(runtime)
        self._initialize_link_states(runtime)
        self._push_event(runtime, runtime.now_ms, "schedule", {"reason": "initial", "force": True})

        while runtime.now_ms < self.max_time_ms:
            next_schedule_time = runtime.pending_events[0].time_ms if runtime.pending_events else inf
            next_completion_time = self._estimate_next_completion_time(runtime)
            next_time = min(next_schedule_time, next_completion_time, self.max_time_ms)
            if next_time is inf:
                break

            self._advance_runtime(runtime, next_time)

            if next_completion_time <= runtime.now_ms + 1e-9:
                completed_any = self._complete_ready_flows(runtime)
                if completed_any:
                    self._push_event(runtime, runtime.now_ms, "schedule", {"reason": "flow_completion"})

            self._process_due_events(runtime, scheduler)

            if self._all_jobs_completed(runtime):
                runtime.pending_events.clear()
                break

            if not runtime.pending_events and not self._has_active_flows(runtime):
                break

        runtime.metadata["max_time_ms"] = config.simulation.max_time_ms
        runtime.metadata["bandwidth_sharing_model"] = config.simulation.bandwidth_sharing_model
        runtime.metadata["completed_flow_count"] = len(runtime.completed_flow_ids)
        runtime.metadata["completed_job_count"] = len(runtime.completed_job_ids)
        return runtime

    def _initialize_link_states(self, runtime: RuntimeState) -> None:
        if runtime.link_states:
            return
        runtime.link_states = {
            link.link_id: LinkState(
                link_id=link.link_id,
                bandwidth_gbps=link.bandwidth_gbps,
                latency_us=link.latency_us,
            )
            for link in runtime.topology.links
        }
        self._record_link_snapshot(runtime, reason="initial")

    def _build_link_lookup(self, runtime: RuntimeState) -> dict[tuple[str, str], Link]:
        lookup: dict[tuple[str, str], Link] = {}
        for link in runtime.topology.links:
            lookup[(link.src, link.dst)] = link
            if link.bidirectional:
                lookup[(link.dst, link.src)] = link
        return lookup

    def _push_event(self, runtime: RuntimeState, time_ms: float, event_type: str, payload: dict | None = None) -> None:
        payload = payload or {}
        heappush(runtime.pending_events, RuntimeEvent(time_ms=max(runtime.now_ms, time_ms), event_type=event_type, payload=payload))

    def _process_due_events(self, runtime: RuntimeState, scheduler: Scheduler) -> None:
        while runtime.pending_events and runtime.pending_events[0].time_ms <= runtime.now_ms + 1e-9:
            event = heappop(runtime.pending_events)
            if event.event_type != "schedule":
                continue
            if event.payload.get("force") or scheduler.maybe_reschedule(runtime):
                decision = scheduler.compute_schedule(runtime)
                self._apply_schedule_decision(runtime, decision)
                runtime.metadata.setdefault("schedule_history", []).append(
                    {
                        "time_ms": runtime.now_ms,
                        "metadata": dict(decision.metadata),
                        "flow_assignment_count": len(decision.flow_assignments),
                        "path_assignment_count": len(decision.path_assignments),
                        "priority_assignment_count": len(decision.priority_assignments),
                        "epoch_action_count": len(decision.epoch_actions),
                    }
                )
                self._update_completed_jobs_from_decision(runtime, decision)
                if decision.valid_until_ms > runtime.now_ms + 1e-9:
                    self._push_event(runtime, decision.valid_until_ms, "schedule", {"reason": "decision_expiry"})

    def _apply_schedule_decision(self, runtime: RuntimeState, decision: ScheduleDecision) -> None:
        for job_id, priority in decision.priority_assignments.items():
            for flow in runtime.flow_states.values():
                if flow.owner_job_id == job_id and flow.status != "completed":
                    flow.priority = priority

        scheduler_name = str(decision.metadata.get("scheduler", ""))
        if decision.epoch_actions:
            for action in decision.epoch_actions:
                self._materialize_epoch_action(runtime, action)
        elif scheduler_name != "teccl":
            for job in runtime.active_jobs:
                self._materialize_job_flows(runtime, job, decision)

        for flow_id, path in decision.path_assignments.items():
            flow = runtime.flow_states.get(flow_id)
            if flow is None or flow.status == "completed":
                continue
            flow.path = list(path)
            flow.traversed_link_ids = self._path_to_link_ids(path)

        self._recompute_link_allocations(runtime)

    def _materialize_epoch_action(self, runtime: RuntimeState, action: EpochAction) -> None:
        replica_id = str(action.metadata.get("replica_id", ""))
        ultimate_destination = str(action.metadata.get("ultimate_destination", action.next_node))
        flow_id = (
            f"epoch::{action.epoch_index}::{action.chunk_id}::{replica_id}::{action.current_node}->{action.next_node}"
            f"::{ultimate_destination}::{action.source_gpu}"
        )
        if flow_id in runtime.flow_states:
            return
        path = self._resolve_route_fragment(runtime, action.current_node, action.next_node, action.route_fragment)
        if not path:
            return
        owner_job_id = action.chunk_id.split("_chunk_")[0]
        chunk_size_mb = self._lookup_chunk_size(runtime, owner_job_id, action.chunk_id)
        runtime.flow_states[flow_id] = FlowState(
            flow_id=flow_id,
            owner_job_id=owner_job_id,
            total_size_mb=chunk_size_mb,
            remaining_size_mb=chunk_size_mb,
            chunk_id=action.chunk_id,
            source_node=action.source_gpu,
            destination_node=action.next_node,
            current_node=action.current_node,
            status="active",
            start_time_ms=runtime.now_ms,
            path=path,
            traversed_link_ids=self._path_to_link_ids(path),
            metadata={
                "epoch_index": action.epoch_index,
                "expected_arrival_epoch": action.expected_arrival_epoch,
                "scheduler": "teccl",
                **dict(action.metadata),
            },
        )

    def _materialize_job_flows(self, runtime: RuntimeState, job: UnifiedJob, decision: ScheduleDecision) -> None:
        priority = decision.priority_assignments.get(job.job_id)
        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_node in chunk.source_set:
                    for destination_node in chunk.destination_set:
                        if source_node == destination_node:
                            continue
                        flow_id = f"flow::{job.job_id}::{chunk.chunk_id}::{source_node}->{destination_node}"
                        flow = runtime.flow_states.get(flow_id)
                        if flow is None:
                            path = decision.path_assignments.get(flow_id) or self._default_path(runtime, source_node, destination_node)
                            if not path:
                                continue
                            flow = FlowState(
                                flow_id=flow_id,
                                owner_job_id=job.job_id,
                                total_size_mb=chunk.size_mb,
                                remaining_size_mb=chunk.size_mb,
                                demand_id=demand.demand_id,
                                chunk_id=chunk.chunk_id,
                                source_node=source_node,
                                destination_node=destination_node,
                                current_node=source_node,
                                priority=priority,
                                status="active",
                                start_time_ms=runtime.now_ms,
                                path=list(path),
                                traversed_link_ids=self._path_to_link_ids(path),
                                metadata={
                                    "collective_type": demand.collective_type,
                                    "dependency_mode": demand.dependency_mode,
                                    "chunk_index": chunk.chunk_index,
                                },
                            )
                            runtime.flow_states[flow_id] = flow
                        elif flow.status != "completed":
                            if priority is not None:
                                flow.priority = priority
                            if decision.path_assignments.get(flow_id):
                                flow.path = list(decision.path_assignments[flow_id])
                                flow.traversed_link_ids = self._path_to_link_ids(flow.path)

    def _default_path(self, runtime: RuntimeState, source_node: str, destination_node: str) -> list[str]:
        return list(runtime.topology.candidate_paths.get((source_node, destination_node), [[]])[0]) if runtime.topology.candidate_paths.get((source_node, destination_node)) else []

    def _resolve_route_fragment(
        self,
        runtime: RuntimeState,
        current_node: str,
        next_node: str,
        route_fragment: list[str],
    ) -> list[str]:
        if len(route_fragment) > 1:
            candidate = list(route_fragment)
            if self._path_to_link_ids(candidate):
                return candidate
        return self._default_path(runtime, current_node, next_node)

    def _path_to_link_ids(self, path: list[str]) -> list[str]:
        link_ids: list[str] = []
        for src, dst in zip(path, path[1:]):
            link = self._link_lookup.get((src, dst))
            if link is None:
                return []
            link_ids.append(link.link_id)
        return link_ids

    def _recompute_link_allocations(self, runtime: RuntimeState) -> None:
        active_flows = [flow for flow in runtime.flow_states.values() if flow.status == "active" and flow.remaining_size_mb > 1e-9]
        for link_state in runtime.link_states.values():
            link_state.active_flows = []
            link_state.utilization = 0.0

        for flow in active_flows:
            flow.assigned_bandwidth_gbps = 0.0
            if not flow.traversed_link_ids:
                continue
            for link_id in flow.traversed_link_ids:
                runtime.link_states[link_id].active_flows.append(flow.flow_id)

        for flow in active_flows:
            if not flow.traversed_link_ids:
                continue
            fair_shares = []
            for link_id in flow.traversed_link_ids:
                link_state = runtime.link_states[link_id]
                active_count = len(link_state.active_flows)
                if active_count <= 0:
                    continue
                fair_shares.append(link_state.bandwidth_gbps / active_count)
            flow.assigned_bandwidth_gbps = min(fair_shares) if fair_shares else 0.0

        for link_state in runtime.link_states.values():
            if link_state.bandwidth_gbps <= 0:
                link_state.utilization = 0.0
                continue
            consumed = sum(
                runtime.flow_states[flow_id].assigned_bandwidth_gbps
                for flow_id in link_state.active_flows
                if flow_id in runtime.flow_states
            )
            link_state.utilization = min(1.0, consumed / link_state.bandwidth_gbps)
        self._record_link_snapshot(runtime, reason="allocation")

    def _estimate_next_completion_time(self, runtime: RuntimeState) -> float:
        completion_times: list[float] = []
        for flow in runtime.flow_states.values():
            if flow.status != "active" or flow.remaining_size_mb <= 1e-9 or flow.assigned_bandwidth_gbps <= 1e-12:
                continue
            completion_times.append(runtime.now_ms + (flow.remaining_size_mb / self._gbps_to_mb_per_ms(flow.assigned_bandwidth_gbps)))
        return min(completion_times, default=inf)

    def _advance_runtime(self, runtime: RuntimeState, target_time_ms: float) -> None:
        if target_time_ms <= runtime.now_ms + 1e-12:
            runtime.now_ms = max(runtime.now_ms, target_time_ms)
            return
        delta_ms = target_time_ms - runtime.now_ms
        for flow in runtime.flow_states.values():
            if flow.status != "active" or flow.assigned_bandwidth_gbps <= 1e-12:
                continue
            transferred_mb = self._gbps_to_mb_per_ms(flow.assigned_bandwidth_gbps) * delta_ms
            flow.remaining_size_mb = max(0.0, flow.remaining_size_mb - transferred_mb)
        for link_state in runtime.link_states.values():
            total_rate_mb_per_ms = sum(
                self._gbps_to_mb_per_ms(runtime.flow_states[flow_id].assigned_bandwidth_gbps)
                for flow_id in link_state.active_flows
                if flow_id in runtime.flow_states and runtime.flow_states[flow_id].status == "active"
            )
            link_state.transmitted_mb += total_rate_mb_per_ms * delta_ms
            link_state.last_update_ms = target_time_ms
        runtime.now_ms = target_time_ms
        self._record_link_snapshot(runtime, reason="advance")

    def _complete_ready_flows(self, runtime: RuntimeState) -> bool:
        completed_any = False
        for flow in runtime.flow_states.values():
            if flow.status == "active" and flow.remaining_size_mb <= 1e-9:
                flow.status = "completed"
                flow.remaining_size_mb = 0.0
                flow.current_node = flow.destination_node
                flow.end_time_ms = runtime.now_ms
                flow.assigned_bandwidth_gbps = 0.0
                if flow.flow_id not in runtime.completed_flow_ids:
                    runtime.completed_flow_ids.append(flow.flow_id)
                completed_any = True
        if completed_any:
            self._mark_completed_jobs(runtime)
            self._recompute_link_allocations(runtime)
        return completed_any

    def _mark_completed_jobs(self, runtime: RuntimeState) -> None:
        if runtime.metadata.get("scheduler_type") == "teccl":
            return
        for job in runtime.active_jobs:
            if job.job_id in runtime.completed_job_ids:
                continue
            job_flow_states = [flow for flow in runtime.flow_states.values() if flow.owner_job_id == job.job_id]
            if job_flow_states and all(flow.status == "completed" for flow in job_flow_states):
                runtime.completed_job_ids.append(job.job_id)

    def _lookup_chunk_size(self, runtime: RuntimeState, owner_job_id: str, chunk_id: str) -> float:
        for job in runtime.active_jobs:
            if job.job_id != owner_job_id:
                continue
            for demand in job.communication_demands:
                for chunk in demand.chunks:
                    if chunk.chunk_id == chunk_id:
                        return chunk.size_mb
        return 0.0

    def _has_active_flows(self, runtime: RuntimeState) -> bool:
        return any(flow.status == "active" and flow.remaining_size_mb > 1e-9 for flow in runtime.flow_states.values())

    def _all_jobs_completed(self, runtime: RuntimeState) -> bool:
        return bool(runtime.active_jobs) and len(runtime.completed_job_ids) == len(runtime.active_jobs)

    def _gbps_to_mb_per_ms(self, gbps: float) -> float:
        return gbps * 0.125

    def _record_link_snapshot(self, runtime: RuntimeState, reason: str) -> None:
        for link_state in runtime.link_states.values():
            snapshot = {
                "time_ms": runtime.now_ms,
                "utilization": link_state.utilization,
                "active_flow_count": len(link_state.active_flows),
                "queue_backlog_mb": link_state.queue_backlog_mb,
                "transmitted_mb": link_state.transmitted_mb,
                "reason": reason,
            }
            if link_state.utilization_history:
                last_snapshot = link_state.utilization_history[-1]
                if (
                    abs(float(last_snapshot["time_ms"]) - runtime.now_ms) <= 1e-12
                    and abs(float(last_snapshot["utilization"]) - link_state.utilization) <= 1e-12
                    and int(last_snapshot["active_flow_count"]) == len(link_state.active_flows)
                    and abs(float(last_snapshot["transmitted_mb"]) - link_state.transmitted_mb) <= 1e-12
                ):
                    last_snapshot["reason"] = reason
                    continue
            link_state.utilization_history.append(snapshot)

    def _update_completed_jobs_from_decision(self, runtime: RuntimeState, decision: ScheduleDecision) -> None:
        if decision.metadata.get("scheduler") != "teccl":
            return
        job_states = decision.metadata.get("job_states", {})
        for job_id, job_state in job_states.items():
            if job_id in runtime.completed_job_ids:
                continue
            chunk_replicas = job_state.get("chunk_replicas", {})
            if not chunk_replicas:
                continue
            if all(
                not replica_state.get("pending_destinations")
                and not replica_state.get("inflight_destinations")
                and not replica_state.get("switch_arrivals")
                for replica_state in chunk_replicas.values()
            ):
                runtime.completed_job_ids.append(job_id)