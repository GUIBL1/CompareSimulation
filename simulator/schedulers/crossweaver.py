from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import exp
from time import perf_counter
from typing import Any

from simulator.core.models import RuntimeState
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class _FlowDemand:
    flow_id: str
    job_id: str
    source: str
    destination: str
    size_mb: float
    dc_source: str
    dc_destination: str
    tor_source: str
    tor_destination: str
    path_candidates: list[list[str]]
    selected_path: list[str] = field(default_factory=list)
    selected_rate_gbps: float = 0.0
    queue_wait_ms: float = 0.0
    propagation_ms: float = 0.0
    dci_link_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _TORDemand:
    tor_source: str
    tor_destination: str
    total_size_mb: float = 0.0


@dataclass(slots=True)
class _FeederDemand:
    source: str
    destination: str
    domain: str
    rate_gbps: float
    pair_key: str
    direction: str  # out / in


@dataclass(slots=True)
class CrossWeaverScheduler(Scheduler):
    slot_ms: float = 1.0
    headroom_ratio: float = 0.1
    epsilon: float = 0.1
    gamma: float = 0.05
    stage1_max_iterations: int = 24
    stage1_binary_search_rounds: int = 42
    stage1_backoff_ratio: float = 0.9
    stage1_backoff_max_rounds: int = 6
    stage1_safety_margin_ratio: float = 0.0
    stage1_rate_smoothing_alpha: float = 1.0
    stage1_rate_change_cap_ratio: float = 1.0
    stage2_max_iterations: int = 32
    stage2_binary_search_rounds: int = 28
    stage2_path_split_k: int = 3
    stage2_initial_max_paths: int = 8
    stage2_max_path_expansion: int = 24
    stage2_path_expansion_step: int = 4
    stage2_softmin_temperature: float = 0.25
    cross_path_ecmp_k: int = 3
    stage1_early_stop_patience: int = 2
    stage2_early_stop_patience: int = 3
    stage2_binary_search_tolerance_ms: float = 1e-3
    feasibility_tolerance: float = 1e-6
    queue_wait_estimation_mode: str = "zero"
    wcmp_update_threshold_l1: float = 0.0
    observed_queue_wait_ms_by_flow: dict[str, float] = field(default_factory=dict)
    last_debug_state: dict[str, Any] = field(default_factory=dict)
    last_scheduler_wall_time_ms: float = 0.0
    last_stage1a_time_ms: float = 0.0
    last_stage1b_time_ms: float = 0.0
    last_stage2_time_ms: float = 0.0
    _prev_cross_rate_by_flow: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _prev_stage1_lambda_by_link: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _prev_stage2_lambda_by_link: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _prev_stage2_t_bracket: tuple[float, float] | None = field(default=None, init=False, repr=False)
    _prev_wcmp_weights: dict[tuple[str, str], list[tuple[list[str], float]]] = field(default_factory=dict, init=False, repr=False)
    _current_link_by_id: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _path_link_id_cache: dict[tuple[str, ...], tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)
    _runtime_link_state_map: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _node_dc_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _node_tor_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _intra_domain_paths_cache: dict[tuple[str, str, int], list[list[str]]] = field(default_factory=dict, init=False, repr=False)
    _inter_dc_hop_count_cache: dict[tuple[str, ...], int] = field(default_factory=dict, init=False, repr=False)
    _dci_link_ids_cache: dict[tuple[str, ...], tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)
    _dci_path_latency_cache: dict[tuple[str, ...], float] = field(default_factory=dict, init=False, repr=False)
    _inv_bandwidth_by_link: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _switch_nodes_cache: set[str] = field(default_factory=set, init=False, repr=False)

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        return None

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if not runtime_state.active_jobs:
            return False
        if len(runtime_state.completed_job_ids) >= len(runtime_state.active_jobs):
            return False
        return True

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        started_at = perf_counter()
        link_by_arc = self._build_link_lookup(runtime_state)
        self._current_link_by_id = self._link_id_map(link_by_arc)
        self._path_link_id_cache = {}
        self._node_dc_cache = {}
        self._node_tor_cache = {}
        self._intra_domain_paths_cache = {}
        self._inter_dc_hop_count_cache = {}
        self._dci_link_ids_cache = {}
        self._dci_path_latency_cache = {}
        self._inv_bandwidth_by_link = {
            link_id: 1.0 / max(float(link.bandwidth_gbps), 1e-9)
            for link_id, link in self._current_link_by_id.items()
        }
        self._switch_nodes_cache = {
            node_id
            for node_id, node in runtime_state.topology.nodes.items()
            if node.node_type == "switch"
        }
        self._refresh_observed_queue_wait(runtime_state, link_by_arc)
        flow_demands, intra_tor_demands = self._build_demands(runtime_state)
        cross_flow_demands = [demand for demand in flow_demands if demand.dc_source != demand.dc_destination]

        if not flow_demands:
            return ScheduleDecision(
                decision_time_ms=runtime_state.now_ms,
                valid_until_ms=runtime_state.now_ms + max(self.slot_ms, 1e-6),
                path_assignments={},
                metadata={
                    "scheduler": "crossweaver",
                    "theta_star": 0.0,
                    "stage2_t_star_ms": 0.0,
                },
            )

        stage1a_started_at = perf_counter()
        theta_star, cross_rate_by_flow = self._stage1a_rate_commitment(
            cross_flow_demands=cross_flow_demands,
            link_by_arc=link_by_arc,
        )
        self.last_stage1a_time_ms = (perf_counter() - stage1a_started_at) * 1000.0
        stage1a_residuals = self._stage1a_constraint_residuals(
            theta=theta_star,
            cross_flow_demands=cross_flow_demands,
            link_by_arc=link_by_arc,
        )
        for demand in cross_flow_demands:
            demand.selected_rate_gbps = cross_rate_by_flow.get(demand.flow_id, 0.0)

        stage1b_started_at = perf_counter()
        stage1b_result = self._stage1b_intra_realization(
            cross_flow_demands=cross_flow_demands,
            runtime_state=runtime_state,
            link_by_arc=link_by_arc,
        )
        self.last_stage1b_time_ms = (perf_counter() - stage1b_started_at) * 1000.0
        cross_rate_by_flow = {demand.flow_id: float(demand.selected_rate_gbps) for demand in cross_flow_demands}
        self._prev_cross_rate_by_flow = dict(cross_rate_by_flow)

        stage2_started_at = perf_counter()
        stage2_inputs = self._merge_tor_demands(
            primary_demands=intra_tor_demands,
            extra_demands=stage1b_result.get("induced_tor_demands", []),
        )
        stage2_result = self._stage2_intra_completion(
            intra_tor_demands=stage2_inputs,
            residual_capacity_by_link=stage1b_result["residual_capacity_by_link"],
            runtime_state=runtime_state,
            link_by_arc=link_by_arc,
        )
        self.last_stage2_time_ms = (perf_counter() - stage2_started_at) * 1000.0

        wcmp_weights = stage2_result["wcmp_weights"]
        path_assignments: dict[str, list[str]] = {}
        for demand in flow_demands:
            if demand.dc_source != demand.dc_destination:
                path_assignments[demand.flow_id] = list(demand.selected_path or self._fallback_path(demand.path_candidates))
                continue
            key = (demand.tor_source, demand.tor_destination)
            weighted_paths = wcmp_weights.get(key, [])
            if weighted_paths:
                path_assignments[demand.flow_id] = self._select_wcmp_path(demand.flow_id, weighted_paths)
            else:
                path_assignments[demand.flow_id] = list(self._fallback_path(demand.path_candidates))

        self.last_debug_state = {
            "scheduler": "crossweaver",
            "crossweaver_scheduler_wall_time_ms": self.last_scheduler_wall_time_ms,
            "crossweaver_stage1a_time_ms": self.last_stage1a_time_ms,
            "crossweaver_stage1b_time_ms": self.last_stage1b_time_ms,
            "crossweaver_stage2_time_ms": self.last_stage2_time_ms,
            "theta_star": theta_star,
            "cross_rate_by_flow_gbps": cross_rate_by_flow,
            "stage1a_constraint_residuals": stage1a_residuals,
            "x_cross_by_link_gbps": stage1b_result["x_cross_by_link"],
            "residual_capacity_by_link_gbps": stage1b_result["residual_capacity_by_link"],
            "stage1_mwu_iterations": stage1b_result["iterations"],
            "stage1_constraint_violation": stage1b_result["max_violation"],
            "stage1b_backoff_scale": stage1b_result.get("rate_scale_factor", 1.0),
            "stage1b_constraint_residuals": stage1b_result["constraint_residuals"],
            "stage1_domain_decomposition": stage1b_result.get("domain_decomposition", {}),
            "stage2_t_star_ms": stage2_result["t_star_ms"],
            "stage2_price_iterations": stage2_result["iterations"],
            "stage2_constraint_violation": stage2_result["max_violation"],
            "stage2_constraint_residuals": stage2_result["constraint_residuals"],
            "stage2_path_expansion_rounds": stage2_result.get("path_expansion_rounds", 0),
            "stage2_max_paths_used": stage2_result.get("max_paths_used", self.stage2_initial_max_paths),
            "wcmp_weights": {
                f"{src}->{dst}": [
                    {
                        "path": path,
                        "weight": weight,
                    }
                    for path, weight in weights
                ]
                for (src, dst), weights in wcmp_weights.items()
            },
        }
        self.last_scheduler_wall_time_ms = (perf_counter() - started_at) * 1000.0
        self.last_debug_state["crossweaver_scheduler_wall_time_ms"] = self.last_scheduler_wall_time_ms

        return ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms + max(self.slot_ms, 1e-6),
            path_assignments=path_assignments,
            metadata={
                "scheduler": "crossweaver",
                "theta_star": theta_star,
                "stage2_t_star_ms": stage2_result["t_star_ms"],
            },
        )

    def export_debug_state(self) -> dict[str, Any]:
        debug_state = dict(self.last_debug_state)
        debug_state.setdefault("crossweaver_scheduler_wall_time_ms", self.last_scheduler_wall_time_ms)
        debug_state.setdefault("crossweaver_stage1a_time_ms", self.last_stage1a_time_ms)
        debug_state.setdefault("crossweaver_stage1b_time_ms", self.last_stage1b_time_ms)
        debug_state.setdefault("crossweaver_stage2_time_ms", self.last_stage2_time_ms)
        return debug_state

    def _build_demands(self, runtime_state: RuntimeState) -> tuple[list[_FlowDemand], list[_TORDemand]]:
        demands: list[_FlowDemand] = []
        intra_tor_demand_map: dict[tuple[str, str], _TORDemand] = {}

        if runtime_state.flow_states:
            for flow in runtime_state.flow_states.values():
                if flow.status == "completed" or float(flow.remaining_size_mb) <= 1e-9:
                    continue
                source = str(flow.source_node or "")
                destination = str(flow.destination_node or "")
                if not source or not destination or source == destination:
                    continue
                source_dc = self._node_dc(runtime_state, source)
                destination_dc = self._node_dc(runtime_state, destination)
                path_candidates = list(runtime_state.topology.candidate_paths.get((source, destination), []))
                if not path_candidates and flow.path:
                    path_candidates = [list(flow.path)]
                tor_source = self._node_tor(runtime_state, source)
                tor_destination = self._node_tor(runtime_state, destination)
                demand = _FlowDemand(
                    flow_id=flow.flow_id,
                    job_id=flow.owner_job_id,
                    source=source,
                    destination=destination,
                    size_mb=float(flow.remaining_size_mb),
                    dc_source=source_dc,
                    dc_destination=destination_dc,
                    tor_source=tor_source,
                    tor_destination=tor_destination,
                    path_candidates=path_candidates,
                )
                demands.append(demand)
                if source_dc == destination_dc and tor_source and tor_destination and tor_source != tor_destination:
                    key = (tor_source, tor_destination)
                    bucket = intra_tor_demand_map.setdefault(
                        key,
                        _TORDemand(tor_source=tor_source, tor_destination=tor_destination, total_size_mb=0.0),
                    )
                    bucket.total_size_mb += float(flow.remaining_size_mb)
            return demands, list(intra_tor_demand_map.values())

        for job in runtime_state.active_jobs:
            for demand in job.communication_demands:
                for chunk in demand.chunks:
                    for source in chunk.source_set:
                        for destination in chunk.destination_set:
                            if source == destination:
                                continue
                            flow_id = f"flow::{job.job_id}::{chunk.chunk_id}::{source}->{destination}"
                            source_dc = self._node_dc(runtime_state, source)
                            destination_dc = self._node_dc(runtime_state, destination)
                            path_candidates = list(runtime_state.topology.candidate_paths.get((source, destination), []))
                            tor_source = self._node_tor(runtime_state, source)
                            tor_destination = self._node_tor(runtime_state, destination)
                            flow_demand = _FlowDemand(
                                flow_id=flow_id,
                                job_id=job.job_id,
                                source=source,
                                destination=destination,
                                size_mb=float(chunk.size_mb),
                                dc_source=source_dc,
                                dc_destination=destination_dc,
                                tor_source=tor_source,
                                tor_destination=tor_destination,
                                path_candidates=path_candidates,
                            )
                            demands.append(flow_demand)

                            if source_dc == destination_dc and tor_source and tor_destination and tor_source != tor_destination:
                                key = (tor_source, tor_destination)
                                bucket = intra_tor_demand_map.setdefault(
                                    key,
                                    _TORDemand(tor_source=tor_source, tor_destination=tor_destination, total_size_mb=0.0),
                                )
                                bucket.total_size_mb += float(chunk.size_mb)

        return demands, list(intra_tor_demand_map.values())

    def _build_link_lookup(self, runtime_state: RuntimeState) -> dict[tuple[str, str], Any]:
        mapping: dict[tuple[str, str], Any] = {}
        for link in runtime_state.topology.links:
            mapping[(link.src, link.dst)] = link
            if link.bidirectional:
                mapping[(link.dst, link.src)] = link
        return mapping

    def _stage1a_rate_commitment(
        self,
        cross_flow_demands: list[_FlowDemand],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> tuple[float, dict[str, float]]:
        if not cross_flow_demands:
            return 0.0, {}

        for demand in cross_flow_demands:
            demand.selected_path = self._select_stage1_cross_path(demand.flow_id, demand.path_candidates, link_by_arc)
            demand.dci_link_ids = self._extract_dci_links_from_path(demand.selected_path, link_by_arc)
            demand.propagation_ms = self._path_latency_ms(demand.dci_link_ids, link_by_arc)
            demand.queue_wait_ms = self._estimate_queue_wait(demand=demand, link_by_arc=link_by_arc)

        theta_low = max((demand.queue_wait_ms + demand.propagation_ms for demand in cross_flow_demands), default=0.0) + 1e-6
        theta_high = max(theta_low * 2.0, max(self.slot_ms, 1.0))
        feasible, best_rates = self._stage1a_feasible(theta_high, cross_flow_demands, link_by_arc)
        guard = 0
        while not feasible and guard < 64:
            theta_high *= 2.0
            feasible, best_rates = self._stage1a_feasible(theta_high, cross_flow_demands, link_by_arc)
            guard += 1

        rounds = max(1, int(self.stage1_binary_search_rounds))
        for _ in range(rounds):
            theta_mid = (theta_low + theta_high) / 2.0
            feasible, rates = self._stage1a_feasible(theta_mid, cross_flow_demands, link_by_arc)
            if feasible:
                theta_high = theta_mid
                best_rates = rates
            else:
                theta_low = theta_mid

        original_path_by_flow = {
            demand.flow_id: (list(demand.selected_path), list(demand.dci_link_ids), demand.propagation_ms)
            for demand in cross_flow_demands
        }
        self._rebalance_cross_paths_by_load(
            cross_flow_demands=cross_flow_demands,
            rate_by_flow=best_rates,
            link_by_arc=link_by_arc,
        )
        rebalanced_feasible, _ = self._stage1a_feasible(theta_high, cross_flow_demands, link_by_arc)
        if not rebalanced_feasible:
            for demand in cross_flow_demands:
                selected_path, dci_link_ids, propagation_ms = original_path_by_flow.get(demand.flow_id, ([], [], 0.0))
                demand.selected_path = selected_path
                demand.dci_link_ids = dci_link_ids
                demand.propagation_ms = propagation_ms

        alpha = min(max(float(self.stage1_rate_smoothing_alpha), 0.0), 1.0)
        cap_ratio = max(float(self.stage1_rate_change_cap_ratio), 0.0)
        committed_rates: dict[str, float] = {}
        for demand in cross_flow_demands:
            raw_rate = max(0.0, float(best_rates.get(demand.flow_id, 0.0)))
            prev_rate = max(0.0, float(self._prev_cross_rate_by_flow.get(demand.flow_id, raw_rate)))
            smoothed = raw_rate if alpha >= 1.0 else ((1.0 - alpha) * prev_rate + alpha * raw_rate)
            if prev_rate > 0.0 and cap_ratio > 0.0:
                max_delta = prev_rate * cap_ratio
                smoothed = min(max(smoothed, prev_rate - max_delta), prev_rate + max_delta)
            committed_rates[demand.flow_id] = max(0.0, smoothed)

        self._prev_cross_rate_by_flow = dict(committed_rates)
        return theta_high, committed_rates

    def _rebalance_cross_paths_by_load(
        self,
        cross_flow_demands: list[_FlowDemand],
        rate_by_flow: dict[str, float],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> None:
        if not cross_flow_demands:
            return
        inter_dc_capacity_by_link: dict[str, float] = {
            link.link_id: float(link.bandwidth_gbps)
            for link in self._iter_unique_links(link_by_arc)
            if bool(link.attributes.get("inter_dc", False))
        }
        if not inter_dc_capacity_by_link:
            return

        dci_load_by_link: dict[str, float] = defaultdict(float)
        ordered_demands = sorted(
            cross_flow_demands,
            key=lambda demand: (-rate_by_flow.get(demand.flow_id, 0.0), demand.flow_id),
        )
        for demand in ordered_demands:
            flow_rate = rate_by_flow.get(demand.flow_id, 0.0)
            best_path: list[str] | None = None
            best_dci_links: list[str] = []
            best_score = float("inf")
            for candidate_path in demand.path_candidates:
                dci_links = self._extract_dci_links_from_path(candidate_path, link_by_arc)
                if not dci_links:
                    continue
                projected_peak_utilization = 0.0
                projected_total_utilization = 0.0
                for link_id in dci_links:
                    cap = max(inter_dc_capacity_by_link.get(link_id, 1e-9), 1e-9)
                    projected_utilization = (dci_load_by_link.get(link_id, 0.0) + flow_rate) / cap
                    projected_peak_utilization = max(projected_peak_utilization, projected_utilization)
                    projected_total_utilization += projected_utilization
                score = projected_peak_utilization * 1000.0 + projected_total_utilization
                if score < best_score:
                    best_score = score
                    best_path = list(candidate_path)
                    best_dci_links = dci_links

            if best_path is None:
                continue
            demand.selected_path = best_path
            demand.dci_link_ids = list(best_dci_links)
            demand.propagation_ms = self._path_latency_ms(demand.dci_link_ids, link_by_arc)
            for link_id in best_dci_links:
                dci_load_by_link[link_id] += flow_rate

    def _stage1a_constraint_residuals(
        self,
        theta: float,
        cross_flow_demands: list[_FlowDemand],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        feasible, rate_by_flow = self._stage1a_feasible(theta, cross_flow_demands, link_by_arc)
        dci_load_by_link: dict[str, float] = defaultdict(float)
        for demand in cross_flow_demands:
            flow_rate = rate_by_flow.get(demand.flow_id, 0.0)
            for link_id in demand.dci_link_ids:
                dci_load_by_link[link_id] += flow_rate

        dci_capacity_residual_by_link: dict[str, float] = {}
        max_positive_violation = 0.0
        for link in self._iter_unique_links(link_by_arc):
            if not bool(link.attributes.get("inter_dc", False)):
                continue
            residual = dci_load_by_link.get(link.link_id, 0.0) - float(link.bandwidth_gbps)
            dci_capacity_residual_by_link[link.link_id] = residual
            max_positive_violation = max(max_positive_violation, max(0.0, residual))

        lower_bound_residual_by_flow = {}
        safety_margin = 1.0 + max(0.0, float(self.stage1_safety_margin_ratio))
        for demand in cross_flow_demands:
            denominator = max(theta - demand.queue_wait_ms - demand.propagation_ms, 1e-6)
            required_rate = max(0.0, (demand.size_mb * safety_margin / denominator) * 8.0)
            lower_bound_residual_by_flow[demand.flow_id] = rate_by_flow.get(demand.flow_id, 0.0) - required_rate

        return {
            "feasible": feasible,
            "theta": theta,
            "dci_load_by_link_gbps": dict(dci_load_by_link),
            "dci_capacity_residual_by_link_gbps": dci_capacity_residual_by_link,
            "dci_capacity_max_positive_violation_gbps": max_positive_violation,
            "rate_lower_bound_residual_by_flow_gbps": lower_bound_residual_by_flow,
        }

    def _stage1a_feasible(
        self,
        theta: float,
        cross_flow_demands: list[_FlowDemand],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> tuple[bool, dict[str, float]]:
        rate_by_flow: dict[str, float] = {}
        demand_sum_by_dci_link: dict[str, float] = defaultdict(float)
        safety_margin = 1.0 + max(0.0, float(self.stage1_safety_margin_ratio))
        for demand in cross_flow_demands:
            denominator = max(theta - demand.queue_wait_ms - demand.propagation_ms, 1e-6)
            rate_gbps = max(0.0, (demand.size_mb * safety_margin / denominator) * 8.0)
            rate_by_flow[demand.flow_id] = rate_gbps
            for link_id in demand.dci_link_ids:
                demand_sum_by_dci_link[link_id] += rate_gbps

        for link in self._iter_unique_links(link_by_arc):
            if not bool(link.attributes.get("inter_dc", False)):
                continue
            link_capacity = float(link.bandwidth_gbps)
            if demand_sum_by_dci_link.get(link.link_id, 0.0) - link_capacity > self.feasibility_tolerance:
                return False, {}
        return True, rate_by_flow

    def _stage1b_intra_realization(
        self,
        cross_flow_demands: list[_FlowDemand],
        runtime_state: RuntimeState,
        link_by_arc: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        all_links = list(self._iter_unique_links(link_by_arc))
        residual_capacity = {link.link_id: float(link.bandwidth_gbps) for link in all_links}
        if not cross_flow_demands:
            return {
                "x_cross_by_link": {},
                "residual_capacity_by_link": residual_capacity,
                "induced_tor_demands": [],
                "iterations": 0,
                "max_violation": 0.0,
                "rate_scale_factor": 1.0,
                "constraint_residuals": {
                    "headroom_residual_by_link_gbps": {},
                    "headroom_max_positive_violation_gbps": 0.0,
                    "residual_capacity_non_negative": True,
                    "y_out_by_pair_gbps": {},
                    "y_in_by_pair_gbps": {},
                },
            }

        c_tilde_by_link = {
            link.link_id: max((1.0 - self.headroom_ratio) * float(link.bandwidth_gbps), 1e-9)
            for link in all_links
            if not bool(link.attributes.get("inter_dc", False))
        }
        feeder_demands = self._build_stage1b_feeders(cross_flow_demands, link_by_arc)

        stage1_path_cap = max(2, min(int(self.stage2_initial_max_paths), int(self.stage2_max_path_expansion)))
        feeder_paths: dict[int, list[list[str]]] = {}
        for index, feeder in enumerate(feeder_demands):
            feeder_paths[index] = self._enumerate_intra_domain_paths(
                runtime_state=runtime_state,
                link_by_arc=link_by_arc,
                src=feeder.source,
                dst=feeder.destination,
                max_paths=stage1_path_cap,
            )

        can_backoff = 0.0 < float(self.stage1_backoff_ratio) < 1.0
        max_backoff_rounds = max(1, int(self.stage1_backoff_max_rounds) if can_backoff else 1)

        best_attempt: dict[str, Any] | None = None
        rate_scale_factor = 1.0
        for backoff_round in range(max_backoff_rounds):
            if backoff_round > 0 and can_backoff:
                rate_scale_factor *= float(self.stage1_backoff_ratio)

            lambda_by_link: dict[str, float] = defaultdict(lambda: 1e-3)
            for link_id, value in self._prev_stage1_lambda_by_link.items():
                lambda_by_link[link_id] = max(1e-6, float(value))

            target_rate_by_feeder = {
                index: max(0.0, feeder.rate_gbps * rate_scale_factor)
                for index, feeder in enumerate(feeder_demands)
            }
            remaining_rate_by_feeder = dict(target_rate_by_feeder)
            allocated_by_link: dict[str, float] = defaultdict(float)
            max_violation = 0.0
            actual_iterations = 0
            stable_iteration_count = 0

            for _ in range(max(1, int(self.stage1_max_iterations))):
                iteration_delta_by_link: dict[str, float] = defaultdict(float)
                total_progress = 0.0
                for index, feeder in enumerate(feeder_demands):
                    remaining_rate = remaining_rate_by_feeder.get(index, 0.0)
                    if remaining_rate <= self.feasibility_tolerance:
                        continue
                    candidate_paths = feeder_paths.get(index, [])
                    if not candidate_paths:
                        continue
                    chosen_path = self._select_min_price_path(
                        path_candidates=candidate_paths,
                        lambda_by_link=lambda_by_link,
                        link_by_arc=link_by_arc,
                        include_inter_dc=False,
                    )
                    if not chosen_path:
                        continue
                    intra_link_ids = [
                        link_id
                        for link_id in self._path_link_ids(chosen_path, link_by_arc)
                        if link_id in c_tilde_by_link
                    ]
                    if not intra_link_ids:
                        continue

                    clipped_rate = remaining_rate
                    for link_id in intra_link_ids:
                        headroom_left = max(
                            0.0,
                            c_tilde_by_link[link_id] - allocated_by_link.get(link_id, 0.0) - iteration_delta_by_link.get(link_id, 0.0),
                        )
                        clipped_rate = min(clipped_rate, headroom_left)
                    if clipped_rate <= self.feasibility_tolerance:
                        continue

                    remaining_rate_by_feeder[index] = max(0.0, remaining_rate - clipped_rate)
                    total_progress += clipped_rate
                    for link_id in intra_link_ids:
                        iteration_delta_by_link[link_id] += clipped_rate

                actual_iterations += 1
                iteration_violation = 0.0
                for link_id, delta in iteration_delta_by_link.items():
                    allocated_by_link[link_id] += delta
                    c_tilde = c_tilde_by_link.get(link_id, 1e-9)
                    base_lambda = max(lambda_by_link.get(link_id, 1e-6), 1e-6)
                    lambda_by_link[link_id] = base_lambda * exp(self.epsilon * (delta / c_tilde))
                    iteration_violation = max(iteration_violation, max(0.0, allocated_by_link[link_id] - c_tilde))

                max_violation = max(max_violation, iteration_violation)
                remaining_total = sum(remaining_rate_by_feeder.values())
                if remaining_total <= self.feasibility_tolerance:
                    stable_iteration_count += 1
                else:
                    stable_iteration_count = 0

                if stable_iteration_count >= max(1, int(self.stage1_early_stop_patience)):
                    break
                if total_progress <= self.feasibility_tolerance:
                    break

            remaining_total = sum(remaining_rate_by_feeder.values())
            realized_rate_by_feeder = {
                index: max(0.0, target_rate_by_feeder.get(index, 0.0) - remaining_rate_by_feeder.get(index, 0.0))
                for index in target_rate_by_feeder
            }
            fully_realized = remaining_total <= self.feasibility_tolerance

            attempt_result = {
                "fully_realized": fully_realized,
                "remaining_total": remaining_total,
                "realized_rate_by_feeder": realized_rate_by_feeder,
                "allocated_by_link": dict(allocated_by_link),
                "lambda_by_link": dict(lambda_by_link),
                "iterations": actual_iterations,
                "max_violation": max_violation,
                "rate_scale_factor": rate_scale_factor,
            }
            best_attempt = attempt_result
            if fully_realized:
                break

        if best_attempt is None:
            best_attempt = {
                "fully_realized": True,
                "remaining_total": 0.0,
                "realized_rate_by_feeder": {},
                "allocated_by_link": {},
                "lambda_by_link": {},
                "iterations": 0,
                "max_violation": 0.0,
                "rate_scale_factor": 1.0,
            }

        final_scale = float(best_attempt.get("rate_scale_factor", 1.0))
        for demand in cross_flow_demands:
            demand.selected_rate_gbps = max(0.0, float(demand.selected_rate_gbps) * final_scale)

        realized_rate_by_feeder = best_attempt.get("realized_rate_by_feeder", {})
        y_out_by_pair_gbps: dict[str, float] = defaultdict(float)
        y_in_by_pair_gbps: dict[str, float] = defaultdict(float)
        induced_tor_demand_map: dict[tuple[str, str], _TORDemand] = {}
        for index, feeder in enumerate(feeder_demands):
            realized_rate = max(0.0, float(realized_rate_by_feeder.get(index, 0.0)))
            if feeder.direction == "out":
                y_out_by_pair_gbps[feeder.pair_key] += realized_rate
            else:
                y_in_by_pair_gbps[feeder.pair_key] += realized_rate
            if realized_rate <= self.feasibility_tolerance:
                continue
            induced_mb = realized_rate * max(self.slot_ms, 1e-6) * 0.125
            key = (feeder.source, feeder.destination)
            bucket = induced_tor_demand_map.setdefault(
                key,
                _TORDemand(tor_source=feeder.source, tor_destination=feeder.destination, total_size_mb=0.0),
            )
            bucket.total_size_mb += induced_mb

        x_cross_by_link = {
            link_id: max(0.0, float(load))
            for link_id, load in best_attempt.get("allocated_by_link", {}).items()
        }

        headroom_residual_by_link: dict[str, float] = {}
        for link in all_links:
            reserved = x_cross_by_link.get(link.link_id, 0.0)
            cap = float(link.bandwidth_gbps)
            residual_capacity[link.link_id] = max(0.0, cap - reserved)
            if link.link_id in c_tilde_by_link:
                headroom_residual_by_link[link.link_id] = reserved - c_tilde_by_link[link.link_id]

        self._prev_stage1_lambda_by_link = {
            link_id: max(1e-6, float(value))
            for link_id, value in best_attempt.get("lambda_by_link", {}).items()
        }

        residual_non_negative = all(value >= -self.feasibility_tolerance for value in residual_capacity.values())
        domain_decomposition: dict[str, dict[str, float]] = defaultdict(lambda: {"cross_flow_count": 0.0, "total_rate_gbps": 0.0})
        for demand in cross_flow_demands:
            if demand.selected_rate_gbps <= 0.0:
                continue
            domain_decomposition[demand.dc_source]["cross_flow_count"] += 1.0
            domain_decomposition[demand.dc_source]["total_rate_gbps"] += demand.selected_rate_gbps
            domain_decomposition[demand.dc_destination]["cross_flow_count"] += 1.0
            domain_decomposition[demand.dc_destination]["total_rate_gbps"] += demand.selected_rate_gbps

        return {
            "x_cross_by_link": x_cross_by_link,
            "residual_capacity_by_link": residual_capacity,
            "induced_tor_demands": list(induced_tor_demand_map.values()),
            "iterations": int(best_attempt.get("iterations", 0)),
            "max_violation": float(best_attempt.get("max_violation", 0.0)),
            "rate_scale_factor": final_scale,
            "domain_decomposition": {k: dict(v) for k, v in domain_decomposition.items()},
            "constraint_residuals": {
                "headroom_residual_by_link_gbps": headroom_residual_by_link,
                "headroom_max_positive_violation_gbps": max(0.0, max(headroom_residual_by_link.values(), default=0.0)),
                "residual_capacity_non_negative": residual_non_negative,
                "y_out_by_pair_gbps": dict(y_out_by_pair_gbps),
                "y_in_by_pair_gbps": dict(y_in_by_pair_gbps),
            },
        }

    def _stage2_intra_completion(
        self,
        intra_tor_demands: list[_TORDemand],
        residual_capacity_by_link: dict[str, float],
        runtime_state: RuntimeState,
        link_by_arc: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        active_demands = [
            demand
            for demand in intra_tor_demands
            if demand.tor_source
            and demand.tor_destination
            and demand.tor_source != demand.tor_destination
            and demand.total_size_mb > self.feasibility_tolerance
        ]
        if not active_demands:
            return {
                "t_star_ms": 0.0,
                "wcmp_weights": {},
                "iterations": 0,
                "max_violation": 0.0,
                "constraint_residuals": {
                    "flow_conservation_residual_by_pair_gbps": {},
                    "flow_conservation_residual_by_pair_mb_per_ms": {},
                    "capacity_residual_by_link_gbps": {},
                    "capacity_max_positive_violation_gbps": 0.0,
                },
            }

        active_pairs = {(demand.tor_source, demand.tor_destination) for demand in active_demands}
        current_max_paths = max(1, int(self.stage2_initial_max_paths))
        tor_paths = self._build_tor_candidate_paths(
            runtime_state=runtime_state,
            link_by_arc=link_by_arc,
            max_paths=current_max_paths,
            active_pairs=active_pairs,
        )

        if self._prev_stage2_t_bracket is not None:
            t_low = max(1e-6, float(self._prev_stage2_t_bracket[0]))
            t_high = max(t_low * 1.01, float(self._prev_stage2_t_bracket[1]))
        else:
            t_low = 1e-6
            t_high = max((demand.total_size_mb for demand in active_demands), default=1.0)

        expansion_rounds = 0
        total_price_iterations = 0
        lambda_seed = dict(self._prev_stage2_lambda_by_link)

        guard = 0
        while True:
            feasible, _, _, _, _, lambda_after, used_iterations = self._stage2_feasible(
                t_candidate_ms=t_high,
                intra_tor_demands=active_demands,
                tor_paths=tor_paths,
                residual_capacity_by_link=residual_capacity_by_link,
                link_by_arc=link_by_arc,
                initial_lambda_by_link=lambda_seed,
            )
            total_price_iterations += used_iterations
            lambda_seed = dict(lambda_after)
            if feasible:
                break
            if current_max_paths < int(self.stage2_max_path_expansion):
                current_max_paths = min(
                    int(self.stage2_max_path_expansion),
                    current_max_paths + max(1, int(self.stage2_path_expansion_step)),
                )
                tor_paths = self._build_tor_candidate_paths(
                    runtime_state=runtime_state,
                    link_by_arc=link_by_arc,
                    max_paths=current_max_paths,
                    active_pairs=active_pairs,
                )
                expansion_rounds += 1
                continue
            t_high *= 2.0
            guard += 1
            if guard >= 48:
                break

        best_path_rate: dict[tuple[tuple[str, str], int], float] = {}
        best_violation = float("inf")
        best_flow_conservation_residual: dict[str, float] = {}
        best_capacity_residual: dict[str, float] = {}
        best_lambda_by_link: dict[str, float] = dict(lambda_seed)
        actual_binary_rounds = 0

        for _ in range(max(1, int(self.stage2_binary_search_rounds))):
            t_mid = (t_low + t_high) / 2.0

            local_paths = tor_paths
            local_max_paths = current_max_paths
            local_expansions = 0
            local_lambda_seed = dict(best_lambda_by_link)

            while True:
                feasible, path_rate, load_by_link, violation, flow_conservation_residual, lambda_after, used_iterations = self._stage2_feasible(
                    t_candidate_ms=t_mid,
                    intra_tor_demands=active_demands,
                    tor_paths=local_paths,
                    residual_capacity_by_link=residual_capacity_by_link,
                    link_by_arc=link_by_arc,
                    initial_lambda_by_link=local_lambda_seed,
                )
                total_price_iterations += used_iterations
                local_lambda_seed = dict(lambda_after)
                if feasible:
                    break
                if local_max_paths >= int(self.stage2_max_path_expansion):
                    break
                local_max_paths = min(
                    int(self.stage2_max_path_expansion),
                    local_max_paths + max(1, int(self.stage2_path_expansion_step)),
                )
                local_paths = self._build_tor_candidate_paths(
                    runtime_state=runtime_state,
                    link_by_arc=link_by_arc,
                    max_paths=local_max_paths,
                    active_pairs=active_pairs,
                )
                local_expansions += 1

            expansion_rounds += local_expansions
            current_max_paths = local_max_paths
            tor_paths = local_paths
            actual_binary_rounds += 1

            if feasible:
                t_high = t_mid
                best_path_rate = path_rate
                best_violation = violation
                best_flow_conservation_residual = flow_conservation_residual
                best_capacity_residual = {
                    link_id: load_by_link.get(link_id, 0.0) - residual_capacity_by_link.get(link_id, 0.0)
                    for link_id in residual_capacity_by_link
                }
                best_lambda_by_link = dict(local_lambda_seed)
            else:
                t_low = t_mid

            if (t_high - t_low) <= max(self.stage2_binary_search_tolerance_ms, self.stage2_binary_search_tolerance_ms * t_high):
                break

        if not best_path_rate:
            for demand in active_demands:
                key = (demand.tor_source, demand.tor_destination)
                candidate_paths = tor_paths.get(key, [])
                if not candidate_paths:
                    continue
                required_rate = (demand.total_size_mb / max(t_high, 1e-9)) * 8.0
                best_path_rate[(key, 0)] = required_rate
            if best_violation == float("inf"):
                best_violation = 0.0

        wcmp_weights: dict[tuple[str, str], list[tuple[list[str], float]]] = {}
        for demand in active_demands:
            key = (demand.tor_source, demand.tor_destination)
            candidate_paths = tor_paths.get(key, [])
            if not candidate_paths:
                continue
            demand_rate = (demand.total_size_mb / max(t_high, 1e-9)) * 8.0
            weighted_paths: list[tuple[list[str], float]] = []
            for index, path in enumerate(candidate_paths):
                rate = best_path_rate.get((key, index), 0.0)
                weight = rate / demand_rate if demand_rate > 1e-9 else 0.0
                if weight > 1e-9:
                    weighted_paths.append((path, weight))
            if not weighted_paths:
                weighted_paths = [(candidate_paths[0], 1.0)]
            else:
                total_weight = sum(weight for _, weight in weighted_paths)
                weighted_paths = [(path, weight / total_weight) for path, weight in weighted_paths]
            wcmp_weights[key] = weighted_paths

        threshold = max(0.0, float(self.wcmp_update_threshold_l1))
        if threshold > 0.0:
            stabilized_wcmp_weights: dict[tuple[str, str], list[tuple[list[str], float]]] = {}
            for key, weights in wcmp_weights.items():
                prev_weights = self._prev_wcmp_weights.get(key)
                if prev_weights is not None and self._wcmp_l1_distance(prev_weights, weights) <= threshold:
                    stabilized_wcmp_weights[key] = [(list(path), float(weight)) for path, weight in prev_weights]
                else:
                    stabilized_wcmp_weights[key] = [(list(path), float(weight)) for path, weight in weights]
            wcmp_weights = stabilized_wcmp_weights

        self._prev_wcmp_weights = {
            key: [(list(path), float(weight)) for path, weight in weights]
            for key, weights in wcmp_weights.items()
        }
        self._prev_stage2_lambda_by_link = {
            link_id: max(0.0, float(value))
            for link_id, value in best_lambda_by_link.items()
        }
        self._prev_stage2_t_bracket = (max(t_low, 1e-6), max(t_high, max(t_low, 1e-6) * 1.000001))

        return {
            "t_star_ms": t_high,
            "wcmp_weights": wcmp_weights,
            "iterations": total_price_iterations,
            "path_expansion_rounds": expansion_rounds,
            "max_paths_used": current_max_paths,
            "max_violation": best_violation,
            "constraint_residuals": {
                "flow_conservation_residual_by_pair_gbps": best_flow_conservation_residual,
                "flow_conservation_residual_by_pair_mb_per_ms": {
                    key: value * 0.125
                    for key, value in best_flow_conservation_residual.items()
                },
                "capacity_residual_by_link_gbps": best_capacity_residual,
                "capacity_max_positive_violation_gbps": max(0.0, max(best_capacity_residual.values(), default=0.0)),
            },
        }

    def _stage2_feasible(
        self,
        t_candidate_ms: float,
        intra_tor_demands: list[_TORDemand],
        tor_paths: dict[tuple[str, str], list[list[str]]],
        residual_capacity_by_link: dict[str, float],
        link_by_arc: dict[tuple[str, str], Any],
        initial_lambda_by_link: dict[str, float] | None = None,
    ) -> tuple[
        bool,
        dict[tuple[tuple[str, str], int], float],
        dict[str, float],
        float,
        dict[str, float],
        dict[str, float],
        int,
    ]:
        lambda_by_link: dict[str, float] = defaultdict(float)
        if initial_lambda_by_link:
            for link_id, value in initial_lambda_by_link.items():
                lambda_by_link[link_id] = max(0.0, float(value))

        path_intra_link_ids: dict[tuple[tuple[str, str], int], tuple[str, ...]] = {}
        link_lookup = self._current_link_by_id
        for demand in intra_tor_demands:
            key = (demand.tor_source, demand.tor_destination)
            candidate_paths = tor_paths.get(key, [])
            for index, path in enumerate(candidate_paths):
                intra_link_ids: list[str] = []
                for link_id in self._path_link_ids(path, link_by_arc):
                    link = link_lookup.get(link_id)
                    if link is None or bool(link.attributes.get("inter_dc", False)):
                        continue
                    intra_link_ids.append(link_id)
                path_intra_link_ids[(key, index)] = tuple(intra_link_ids)

        path_rate: dict[tuple[tuple[str, str], int], float] = defaultdict(float)
        load_by_link: dict[str, float] = defaultdict(float)
        max_violation = 0.0
        stable_iteration_count = 0
        used_iterations = 0

        temperature = max(float(self.stage2_softmin_temperature), 1e-6)

        for _ in range(max(1, int(self.stage2_max_iterations))):
            used_iterations += 1
            path_rate.clear()
            load_by_link.clear()

            for demand in intra_tor_demands:
                key = (demand.tor_source, demand.tor_destination)
                candidate_paths = tor_paths.get(key, [])
                if not candidate_paths:
                    continue
                required_rate = (demand.total_size_mb / max(t_candidate_ms, 1e-9)) * 8.0
                priced_paths = self._select_low_price_paths(
                    path_candidates=candidate_paths,
                    lambda_by_link=lambda_by_link,
                    link_by_arc=link_by_arc,
                    include_inter_dc=False,
                    top_k=max(1, int(self.stage2_path_split_k)),
                )
                if not priced_paths:
                    priced_paths = [(0, candidate_paths[0], 0.0)]

                min_cost = min(price for _, _, price in priced_paths)
                unnormalized_weights: list[float] = []
                for _, _, price in priced_paths:
                    unnormalized_weights.append(exp(-max(0.0, price - min_cost) / temperature))
                weight_sum = sum(unnormalized_weights)
                if weight_sum <= 1e-12:
                    normalized_weights = [1.0 / len(priced_paths)] * len(priced_paths)
                else:
                    normalized_weights = [weight / weight_sum for weight in unnormalized_weights]

                for (path_index, _path, _), weight in zip(priced_paths, normalized_weights, strict=False):
                    allocated_rate = required_rate * weight
                    if allocated_rate <= self.feasibility_tolerance:
                        continue
                    path_rate[(key, path_index)] += allocated_rate
                    for link_id in path_intra_link_ids.get((key, path_index), ()):
                        load_by_link[link_id] += allocated_rate

            max_overload = 0.0
            lambda_delta_max = 0.0
            for link_id, cap in residual_capacity_by_link.items():
                link = link_lookup.get(link_id)
                if link is not None and bool(link.attributes.get("inter_dc", False)):
                    continue
                cap_non_negative = max(float(cap), 0.0)
                load = load_by_link.get(link_id, 0.0)
                overload = max(0.0, load - cap_non_negative)
                max_overload = max(max_overload, overload)
                updated_lambda = max(0.0, lambda_by_link.get(link_id, 0.0) + self.gamma * (load - cap_non_negative))
                lambda_delta_max = max(lambda_delta_max, abs(updated_lambda - lambda_by_link.get(link_id, 0.0)))
                lambda_by_link[link_id] = updated_lambda

            max_violation = max_overload
            if max_overload <= self.feasibility_tolerance and lambda_delta_max <= self.feasibility_tolerance:
                stable_iteration_count += 1
            else:
                stable_iteration_count = 0
            if stable_iteration_count >= max(1, int(self.stage2_early_stop_patience)):
                break

        flow_conservation_residual: dict[str, float] = {}
        max_flow_deficit = 0.0
        for demand in intra_tor_demands:
            key = (demand.tor_source, demand.tor_destination)
            candidate_paths = tor_paths.get(key, [])
            served_rate = sum(path_rate.get((key, path_index), 0.0) for path_index in range(len(candidate_paths)))
            required_rate = (demand.total_size_mb / max(t_candidate_ms, 1e-9)) * 8.0
            residual = served_rate - required_rate
            flow_conservation_residual[f"{demand.tor_source}->{demand.tor_destination}"] = residual
            max_flow_deficit = max(max_flow_deficit, max(0.0, -residual))

        effective_violation = max(max_violation, max_flow_deficit)
        return (
            effective_violation <= self.feasibility_tolerance,
            dict(path_rate),
            dict(load_by_link),
            effective_violation,
            flow_conservation_residual,
            dict(lambda_by_link),
            used_iterations,
        )

    def _build_tor_candidate_paths(
        self,
        runtime_state: RuntimeState,
        link_by_arc: dict[tuple[str, str], Any],
        max_paths: int,
        active_pairs: set[tuple[str, str]] | None = None,
    ) -> dict[tuple[str, str], list[list[str]]]:
        switch_nodes = self._switch_nodes_cache

        pairs = active_pairs
        if pairs is None:
            pairs = {
                (src, dst)
                for src in switch_nodes
                for dst in switch_nodes
                if src != dst and self._node_dc(runtime_state, src) == self._node_dc(runtime_state, dst)
            }

        result: dict[tuple[str, str], list[list[str]]] = {}
        for src, dst in pairs:
            if src == dst:
                continue
            if src not in switch_nodes or dst not in switch_nodes:
                continue
            if self._node_dc(runtime_state, src) != self._node_dc(runtime_state, dst):
                continue
            paths = self._enumerate_intra_domain_paths(
                runtime_state=runtime_state,
                link_by_arc=link_by_arc,
                src=src,
                dst=dst,
                max_paths=max_paths,
            )
            if paths:
                result[(src, dst)] = paths
        return result

    def _select_stage1_cross_path(
        self,
        flow_id: str,
        path_candidates: list[list[str]],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> list[str]:
        if not path_candidates:
            return []
        ranked = sorted(
            path_candidates,
            key=lambda path: (
                self._inter_dc_hop_count(path, link_by_arc),
                self._dci_path_latency_ms(path, link_by_arc),
                len(path),
                path,
            ),
        )
        best_hop_count = self._inter_dc_hop_count(ranked[0], link_by_arc)
        top_group = [path for path in ranked if self._inter_dc_hop_count(path, link_by_arc) == best_hop_count]
        if not top_group:
            return list(ranked[0])

        distinct_by_dci_signature: list[list[str]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        for path in top_group:
            dci_signature = tuple(self._extract_dci_links_from_path(path, link_by_arc))
            if dci_signature in seen_signatures:
                continue
            seen_signatures.add(dci_signature)
            distinct_by_dci_signature.append(path)
        if not distinct_by_dci_signature:
            distinct_by_dci_signature = top_group

        cap = max(1, int(self.cross_path_ecmp_k))
        candidate_group = distinct_by_dci_signature[: min(cap, len(distinct_by_dci_signature))]
        selected_index = abs(hash(flow_id)) % len(candidate_group)
        return list(candidate_group[selected_index])

    def _select_min_price_path(
        self,
        path_candidates: list[list[str]],
        lambda_by_link: dict[str, float],
        link_by_arc: dict[tuple[str, str], Any],
        include_inter_dc: bool,
    ) -> list[str]:
        if not path_candidates:
            return []
        link_lookup = self._current_link_by_id
        inv_bw = self._inv_bandwidth_by_link
        best_path: list[str] = []
        best_cost = float("inf")
        for path in path_candidates:
            cost = 0.0
            for link_id in self._path_link_ids(path, link_by_arc):
                link = link_lookup.get(link_id)
                if link is None:
                    continue
                if not include_inter_dc and bool(link.attributes.get("inter_dc", False)):
                    continue
                cost += lambda_by_link.get(link_id, 0.0) * inv_bw.get(link_id, 1e9)
            if cost < best_cost:
                best_cost = cost
                best_path = list(path)
        return best_path

    def _select_low_price_paths(
        self,
        path_candidates: list[list[str]],
        lambda_by_link: dict[str, float],
        link_by_arc: dict[tuple[str, str], Any],
        include_inter_dc: bool,
        top_k: int,
    ) -> list[tuple[int, list[str], float]]:
        if not path_candidates:
            return []
        link_lookup = self._current_link_by_id
        inv_bw = self._inv_bandwidth_by_link
        scored_paths: list[tuple[int, list[str], float]] = []
        for index, path in enumerate(path_candidates):
            cost = 0.0
            for link_id in self._path_link_ids(path, link_by_arc):
                link = link_lookup.get(link_id)
                if link is None:
                    continue
                if not include_inter_dc and bool(link.attributes.get("inter_dc", False)):
                    continue
                cost += lambda_by_link.get(link_id, 0.0) * inv_bw.get(link_id, 1e9)
            scored_paths.append((index, list(path), cost))
        scored_paths.sort(key=lambda item: (item[2], len(item[1]), item[1]))
        return scored_paths[: max(1, min(int(top_k), len(scored_paths)))]

    def _select_wcmp_path(self, flow_id: str, weighted_paths: list[tuple[list[str], float]]) -> list[str]:
        rolling = sum(weight for _, weight in weighted_paths)
        if rolling <= 0.0:
            return list(weighted_paths[0][0])
        hash_value = abs(hash(flow_id)) % 1_000_000 / 1_000_000.0
        threshold = hash_value * rolling
        cumulative = 0.0
        for path, weight in weighted_paths:
            cumulative += weight
            if cumulative + 1e-12 >= threshold:
                return list(path)
        return list(weighted_paths[-1][0])

    def _build_stage1b_feeders(
        self,
        cross_flow_demands: list[_FlowDemand],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> list[_FeederDemand]:
        feeders: list[_FeederDemand] = []
        for demand in cross_flow_demands:
            if demand.selected_rate_gbps <= 0.0:
                continue
            inter_dc_hop_index = self._first_inter_dc_hop_index(demand.selected_path, link_by_arc)
            if inter_dc_hop_index is None:
                continue
            src_border = demand.selected_path[inter_dc_hop_index]
            dst_border = demand.selected_path[inter_dc_hop_index + 1]

            if demand.tor_source and demand.tor_source != src_border:
                feeders.append(
                    _FeederDemand(
                        source=demand.tor_source,
                        destination=src_border,
                        domain=demand.dc_source,
                        rate_gbps=demand.selected_rate_gbps,
                        pair_key=f"{demand.tor_source}->{src_border}",
                        direction="out",
                    )
                )
            if demand.tor_destination and demand.tor_destination != dst_border:
                feeders.append(
                    _FeederDemand(
                        source=dst_border,
                        destination=demand.tor_destination,
                        domain=demand.dc_destination,
                        rate_gbps=demand.selected_rate_gbps,
                        pair_key=f"{dst_border}->{demand.tor_destination}",
                        direction="in",
                    )
                )
        return feeders

    def _merge_tor_demands(
        self,
        primary_demands: list[_TORDemand],
        extra_demands: list[_TORDemand],
    ) -> list[_TORDemand]:
        merged: dict[tuple[str, str], _TORDemand] = {}
        for demand in [*primary_demands, *extra_demands]:
            key = (demand.tor_source, demand.tor_destination)
            bucket = merged.setdefault(
                key,
                _TORDemand(
                    tor_source=demand.tor_source,
                    tor_destination=demand.tor_destination,
                    total_size_mb=0.0,
                ),
            )
            bucket.total_size_mb += float(demand.total_size_mb)
        return list(merged.values())

    def _first_inter_dc_hop_index(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> int | None:
        for index, (src, dst) in enumerate(zip(path, path[1:])):
            link = link_by_arc.get((src, dst))
            if link is not None and bool(link.attributes.get("inter_dc", False)):
                return index
        return None

    def _extract_dci_links_from_path(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> list[str]:
        key = tuple(path)
        cached = self._dci_link_ids_cache.get(key)
        if cached is not None:
            return list(cached)
        link_ids: list[str] = []
        for src, dst in zip(path, path[1:]):
            link = link_by_arc.get((src, dst))
            if link is None:
                continue
            if bool(link.attributes.get("inter_dc", False)):
                link_ids.append(link.link_id)
        self._dci_link_ids_cache[key] = tuple(link_ids)
        return link_ids

    def _dci_path_latency_ms(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> float:
        key = tuple(path)
        cached = self._dci_path_latency_cache.get(key)
        if cached is not None:
            return cached
        latency_ms = self._path_latency_ms(self._extract_dci_links_from_path(path, link_by_arc), link_by_arc)
        self._dci_path_latency_cache[key] = latency_ms
        return latency_ms

    def _path_link_ids(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> tuple[str, ...]:
        key = tuple(path)
        cached = self._path_link_id_cache.get(key)
        if cached is not None:
            return cached
        link_ids: list[str] = []
        for src, dst in zip(path, path[1:]):
            link = link_by_arc.get((src, dst))
            if link is None:
                return tuple()
            link_ids.append(link.link_id)
        resolved = tuple(link_ids)
        self._path_link_id_cache[key] = resolved
        return resolved

    def _path_latency_ms(self, dci_link_ids: list[str], link_by_arc: dict[tuple[str, str], Any]) -> float:
        if not dci_link_ids:
            return 0.0
        by_id = self._current_link_by_id or self._link_id_map(link_by_arc)
        return sum(float(by_id[link_id].latency_us) / 1000.0 for link_id in dci_link_ids if link_id in by_id)

    def _estimate_queue_wait(self, demand: _FlowDemand, link_by_arc: dict[tuple[str, str], Any]) -> float:
        if self.queue_wait_estimation_mode != "observed":
            return 0.0
        observed = float(self.observed_queue_wait_ms_by_flow.get(demand.flow_id, 0.0))
        if observed > 0.0:
            return observed
        if len(demand.selected_path) < 2:
            return 0.0
        first_link = link_by_arc.get((demand.selected_path[0], demand.selected_path[1]))
        if first_link is None or float(first_link.bandwidth_gbps) <= 1e-9:
            return 0.0
        link_state = self._runtime_link_state_map.get(first_link.link_id)
        if link_state is None:
            return 0.0
        return max(0.0, float(link_state.queue_backlog_mb) / (float(first_link.bandwidth_gbps) * 0.125))

    def _refresh_observed_queue_wait(self, runtime_state: RuntimeState, link_by_arc: dict[tuple[str, str], Any]) -> None:
        self._runtime_link_state_map = dict(runtime_state.link_states)
        for flow in runtime_state.flow_states.values():
            if flow.status == "completed" or not flow.path or len(flow.path) < 2:
                continue
            first_hop = link_by_arc.get((flow.path[0], flow.path[1]))
            if first_hop is None or float(first_hop.bandwidth_gbps) <= 1e-9:
                continue
            link_state = runtime_state.link_states.get(first_hop.link_id)
            if link_state is None:
                continue
            queue_wait_ms = max(0.0, float(link_state.queue_backlog_mb) / (float(first_hop.bandwidth_gbps) * 0.125))
            self.observed_queue_wait_ms_by_flow[flow.flow_id] = queue_wait_ms

    def _inter_dc_hop_count(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> int:
        key = tuple(path)
        cached = self._inter_dc_hop_count_cache.get(key)
        if cached is not None:
            return cached
        count = 0
        for src, dst in zip(path, path[1:]):
            link = link_by_arc.get((src, dst))
            if link is not None and bool(link.attributes.get("inter_dc", False)):
                count += 1
        self._inter_dc_hop_count_cache[key] = count
        return count

    def _fallback_path(self, path_candidates: list[list[str]]) -> list[str]:
        return list(path_candidates[0]) if path_candidates else []

    def _node_dc(self, runtime_state: RuntimeState, node_id: str) -> str:
        cached = self._node_dc_cache.get(node_id)
        if cached is not None:
            return cached
        node = runtime_state.topology.nodes.get(node_id)
        if node is None:
            self._node_dc_cache[node_id] = "unknown"
            return "unknown"
        dc = str(node.attributes.get("dc", "unknown"))
        self._node_dc_cache[node_id] = dc
        return dc

    def _node_tor(self, runtime_state: RuntimeState, node_id: str) -> str:
        cached = self._node_tor_cache.get(node_id)
        if cached is not None:
            return cached
        node = runtime_state.topology.nodes.get(node_id)
        if node is None:
            self._node_tor_cache[node_id] = ""
            return ""
        if node.node_type == "switch" and str(node.attributes.get("role", "")).lower() == "leaf":
            self._node_tor_cache[node_id] = node_id
            return node_id
        candidate = str(node.attributes.get("tor_id", ""))
        if candidate:
            self._node_tor_cache[node_id] = candidate
            return candidate
        for neighbor in runtime_state.topology.adjacency.get(node_id, []):
            neighbor_node = runtime_state.topology.nodes.get(neighbor)
            if neighbor_node is None:
                continue
            if neighbor_node.node_type == "switch" and str(neighbor_node.attributes.get("role", "")).lower() == "leaf":
                self._node_tor_cache[node_id] = neighbor
                return neighbor
        self._node_tor_cache[node_id] = ""
        return ""

    def _enumerate_intra_domain_paths(
        self,
        runtime_state: RuntimeState,
        link_by_arc: dict[tuple[str, str], Any],
        src: str,
        dst: str,
        max_paths: int,
    ) -> list[list[str]]:
        cache_key = (src, dst, int(max_paths))
        cached = self._intra_domain_paths_cache.get(cache_key)
        if cached is not None:
            return cached
        if src == dst:
            self._intra_domain_paths_cache[cache_key] = [[src]]
            return [[src]]
        domain = self._node_dc(runtime_state, src)
        if domain == "unknown" or self._node_dc(runtime_state, dst) != domain:
            self._intra_domain_paths_cache[cache_key] = []
            return []

        queue: deque[list[str]] = deque([[src]])
        results: list[list[str]] = []
        shortest_len: int | None = None

        while queue and len(results) < max_paths:
            path = queue.popleft()
            tail = path[-1]
            if shortest_len is not None and len(path) > shortest_len:
                continue
            if tail == dst:
                shortest_len = len(path)
                results.append(path)
                continue
            for neighbor in runtime_state.topology.adjacency.get(tail, []):
                if neighbor in path:
                    continue
                if self._node_dc(runtime_state, neighbor) != domain:
                    continue
                link = link_by_arc.get((tail, neighbor))
                if link is None or bool(link.attributes.get("inter_dc", False)):
                    continue
                queue.append(path + [neighbor])
        self._intra_domain_paths_cache[cache_key] = results
        return results

    def _iter_unique_links(self, link_by_arc: dict[tuple[str, str], Any]) -> list[Any]:
        unique_links: list[Any] = []
        seen_link_ids: set[str] = set()
        for link in link_by_arc.values():
            if link.link_id in seen_link_ids:
                continue
            seen_link_ids.add(link.link_id)
            unique_links.append(link)
        return unique_links

    def _wcmp_l1_distance(
        self,
        left: list[tuple[list[str], float]],
        right: list[tuple[list[str], float]],
    ) -> float:
        left_map = {tuple(path): float(weight) for path, weight in left}
        right_map = {tuple(path): float(weight) for path, weight in right}
        keys = set(left_map.keys()) | set(right_map.keys())
        return sum(abs(left_map.get(key, 0.0) - right_map.get(key, 0.0)) for key in keys)

    def _link_id_map(self, link_by_arc: dict[tuple[str, str], Any]) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for link in link_by_arc.values():
            mapping[link.link_id] = link
        return mapping
