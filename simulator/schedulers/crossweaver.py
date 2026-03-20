from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import exp
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
class CrossWeaverScheduler(Scheduler):
    slot_ms: float = 1.0
    headroom_ratio: float = 0.1
    epsilon: float = 0.1
    gamma: float = 0.05
    stage1_max_iterations: int = 24
    stage2_max_iterations: int = 32
    stage2_binary_search_rounds: int = 28
    feasibility_tolerance: float = 1e-6
    queue_wait_estimation_mode: str = "zero"
    observed_queue_wait_ms_by_flow: dict[str, float] = field(default_factory=dict)
    last_debug_state: dict[str, Any] = field(default_factory=dict)

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        return None

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if not runtime_state.active_jobs:
            return False
        if not runtime_state.flow_states:
            return True
        return any(flow.status == "completed" for flow in runtime_state.flow_states.values())

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        link_by_arc = self._build_link_lookup(runtime_state)
        flow_demands, intra_tor_demands = self._build_demands(runtime_state)
        cross_flow_demands = [demand for demand in flow_demands if demand.dc_source != demand.dc_destination]

        theta_star, cross_rate_by_flow = self._stage1a_rate_commitment(
            cross_flow_demands=cross_flow_demands,
            link_by_arc=link_by_arc,
        )
        stage1a_residuals = self._stage1a_constraint_residuals(
            theta=theta_star,
            cross_flow_demands=cross_flow_demands,
            link_by_arc=link_by_arc,
        )
        for demand in cross_flow_demands:
            demand.selected_rate_gbps = cross_rate_by_flow.get(demand.flow_id, 0.0)

        stage1b_result = self._stage1b_intra_realization(
            cross_flow_demands=cross_flow_demands,
            link_by_arc=link_by_arc,
        )

        stage2_result = self._stage2_intra_completion(
            intra_tor_demands=intra_tor_demands,
            residual_capacity_by_link=stage1b_result["residual_capacity_by_link"],
            runtime_state=runtime_state,
            link_by_arc=link_by_arc,
        )

        wcmp_weights = stage2_result["wcmp_weights"]
        path_assignments: dict[str, list[str]] = {}
        for demand in flow_demands:
            if demand.dc_source != demand.dc_destination:
                path_assignments[demand.flow_id] = list(demand.selected_path or self._fallback_path(demand.path_candidates))
                continue
            key = (demand.tor_source, demand.tor_destination)
            weighted_paths = wcmp_weights.get(key, [])
            if weighted_paths:
                chosen_path = self._select_wcmp_path(demand.flow_id, weighted_paths)
                path_assignments[demand.flow_id] = chosen_path
            else:
                path_assignments[demand.flow_id] = list(self._fallback_path(demand.path_candidates))

        self.last_debug_state = {
            "scheduler": "crossweaver",
            "theta_star": theta_star,
            "cross_rate_by_flow_gbps": cross_rate_by_flow,
            "stage1a_constraint_residuals": stage1a_residuals,
            "x_cross_by_link_gbps": stage1b_result["x_cross_by_link"],
            "residual_capacity_by_link_gbps": stage1b_result["residual_capacity_by_link"],
            "stage1_mwu_iterations": stage1b_result["iterations"],
            "stage1_constraint_violation": stage1b_result["max_violation"],
            "stage1b_constraint_residuals": stage1b_result["constraint_residuals"],
            "stage2_t_star_ms": stage2_result["t_star_ms"],
            "stage2_price_iterations": stage2_result["iterations"],
            "stage2_constraint_violation": stage2_result["max_violation"],
            "stage2_constraint_residuals": stage2_result["constraint_residuals"],
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

        return ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms,
            path_assignments=path_assignments,
            metadata={
                "scheduler": "crossweaver",
                "theta_star": theta_star,
                "stage2_t_star_ms": stage2_result["t_star_ms"],
            },
        )

    def export_debug_state(self) -> dict[str, Any]:
        return dict(self.last_debug_state)

    def _build_demands(self, runtime_state: RuntimeState) -> tuple[list[_FlowDemand], list[_TORDemand]]:
        demands: list[_FlowDemand] = []
        intra_tor_demand_map: dict[tuple[str, str], _TORDemand] = {}
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

    def _stage1a_rate_commitment(self, cross_flow_demands: list[_FlowDemand], link_by_arc: dict[tuple[str, str], Any]) -> tuple[float, dict[str, float]]:
        if not cross_flow_demands:
            return 0.0, {}

        for demand in cross_flow_demands:
            demand.selected_path = self._select_stage1_cross_path(demand.path_candidates, link_by_arc)
            demand.dci_link_ids = self._extract_dci_links_from_path(demand.selected_path, link_by_arc)
            demand.propagation_ms = self._path_latency_ms(demand.dci_link_ids, link_by_arc)
            demand.queue_wait_ms = self._estimate_queue_wait(demand.flow_id)

        theta_low = max((demand.queue_wait_ms + demand.propagation_ms for demand in cross_flow_demands), default=0.0) + 1e-6
        theta_high = max(theta_low * 2.0, 1.0)
        feasible, _ = self._stage1a_feasible(theta_high, cross_flow_demands, link_by_arc)
        guard = 0
        while not feasible and guard < 40:
            theta_high *= 2.0
            feasible, _ = self._stage1a_feasible(theta_high, cross_flow_demands, link_by_arc)
            guard += 1

        best_rates: dict[str, float] = {}
        for _ in range(42):
            theta_mid = (theta_low + theta_high) / 2.0
            feasible, rates = self._stage1a_feasible(theta_mid, cross_flow_demands, link_by_arc)
            if feasible:
                theta_high = theta_mid
                best_rates = rates
            else:
                theta_low = theta_mid
        return theta_high, best_rates

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
        for link in link_by_arc.values():
            if not bool(link.attributes.get("inter_dc", False)):
                continue
            residual = dci_load_by_link.get(link.link_id, 0.0) - float(link.bandwidth_gbps)
            dci_capacity_residual_by_link[link.link_id] = residual
            max_positive_violation = max(max_positive_violation, max(0.0, residual))

        lower_bound_residual_by_flow = {}
        for demand in cross_flow_demands:
            required_rate = max(0.0, (demand.size_mb / max(theta - demand.queue_wait_ms - demand.propagation_ms, 1e-6)) * 8.0 / 1000.0)
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
        for demand in cross_flow_demands:
            denominator = max(theta - demand.queue_wait_ms - demand.propagation_ms, 1e-6)
            rate_gbps = max(0.0, (demand.size_mb / denominator) * 8.0 / 1000.0)
            rate_by_flow[demand.flow_id] = rate_gbps
            for link_id in demand.dci_link_ids:
                demand_sum_by_dci_link[link_id] += rate_gbps

        for link in link_by_arc.values():
            if not bool(link.attributes.get("inter_dc", False)):
                continue
            link_capacity = float(link.bandwidth_gbps)
            if demand_sum_by_dci_link.get(link.link_id, 0.0) - link_capacity > self.feasibility_tolerance:
                return False, {}
        return True, rate_by_flow

    def _stage1b_intra_realization(
        self,
        cross_flow_demands: list[_FlowDemand],
        link_by_arc: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        if not cross_flow_demands:
            residual_capacity = {
                link.link_id: float(link.bandwidth_gbps)
                for link in link_by_arc.values()
            }
            return {
                "x_cross_by_link": {},
                "residual_capacity_by_link": residual_capacity,
                "iterations": 0,
                "max_violation": 0.0,
                "constraint_residuals": {
                    "headroom_residual_by_link_gbps": {},
                    "residual_capacity_non_negative": True,
                },
            }

        lambda_by_link: dict[str, float] = defaultdict(float)
        c_tilde_by_link = {
            link.link_id: max((1.0 - self.headroom_ratio) * float(link.bandwidth_gbps), 1e-6)
            for link in link_by_arc.values()
        }
        average_load_by_link: dict[str, float] = defaultdict(float)
        max_violation = 0.0

        for iteration in range(self.stage1_max_iterations):
            delta_load_by_link: dict[str, float] = defaultdict(float)
            for demand in cross_flow_demands:
                if demand.selected_rate_gbps <= 0.0:
                    continue
                path = self._select_min_price_path(
                    demand.path_candidates,
                    lambda_by_link=lambda_by_link,
                    link_by_arc=link_by_arc,
                    include_inter_dc=False,
                )
                if path:
                    demand.selected_path = path
                link_ids = self._path_link_ids(demand.selected_path, link_by_arc)
                intra_link_ids = [
                    link_id
                    for link_id in link_ids
                    if not bool(self._link_by_id(link_id, link_by_arc).attributes.get("inter_dc", False))
                ]
                for link_id in intra_link_ids:
                    delta_load_by_link[link_id] += demand.selected_rate_gbps

            for link_id, delta_load in delta_load_by_link.items():
                c_tilde = c_tilde_by_link.get(link_id, 1e-6)
                lambda_by_link[link_id] = lambda_by_link[link_id] * exp(self.epsilon * (delta_load / c_tilde))
                average_load_by_link[link_id] += delta_load

            iteration_violation = 0.0
            for link_id, delta_load in delta_load_by_link.items():
                c_tilde = c_tilde_by_link.get(link_id, 1e-6)
                iteration_violation = max(iteration_violation, max(0.0, delta_load - c_tilde))
            max_violation = max(max_violation, iteration_violation)

        x_cross_by_link = {
            link_id: load / max(1, self.stage1_max_iterations)
            for link_id, load in average_load_by_link.items()
        }
        residual_capacity = {}
        headroom_residual_by_link = {}
        for link in link_by_arc.values():
            reserved = x_cross_by_link.get(link.link_id, 0.0)
            residual_capacity[link.link_id] = max(0.0, float(link.bandwidth_gbps) - reserved)
            headroom_cap = max((1.0 - self.headroom_ratio) * float(link.bandwidth_gbps), 0.0)
            headroom_residual_by_link[link.link_id] = reserved - headroom_cap

        residual_non_negative = all(value >= -self.feasibility_tolerance for value in residual_capacity.values())

        return {
            "x_cross_by_link": x_cross_by_link,
            "residual_capacity_by_link": residual_capacity,
            "iterations": self.stage1_max_iterations,
            "max_violation": max_violation,
            "constraint_residuals": {
                "headroom_residual_by_link_gbps": headroom_residual_by_link,
                "headroom_max_positive_violation_gbps": max(0.0, max(headroom_residual_by_link.values(), default=0.0)),
                "residual_capacity_non_negative": residual_non_negative,
            },
        }

    def _stage2_intra_completion(
        self,
        intra_tor_demands: list[_TORDemand],
        residual_capacity_by_link: dict[str, float],
        runtime_state: RuntimeState,
        link_by_arc: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        if not intra_tor_demands:
            return {
                "t_star_ms": 0.0,
                "wcmp_weights": {},
                "iterations": 0,
                "max_violation": 0.0,
            }

        tor_paths = self._build_tor_candidate_paths(runtime_state)

        t_low = 1e-6
        t_high = max((demand.total_size_mb for demand in intra_tor_demands), default=1.0)
        feasible, _, _, _, _ = self._stage2_feasible(
            t_high,
            intra_tor_demands,
            tor_paths,
            residual_capacity_by_link,
            link_by_arc,
        )
        guard = 0
        while not feasible and guard < 40:
            t_high *= 2.0
            feasible, _, _, _, _ = self._stage2_feasible(
                t_high,
                intra_tor_demands,
                tor_paths,
                residual_capacity_by_link,
                link_by_arc,
            )
            guard += 1

        best_path_rate = {}
        best_violation = 0.0
        best_flow_conservation_residual = {}
        best_capacity_residual = {}
        for _ in range(self.stage2_binary_search_rounds):
            t_mid = (t_low + t_high) / 2.0
            feasible, path_rate, load_by_link, violation, flow_conservation_residual = self._stage2_feasible(
                t_mid,
                intra_tor_demands,
                tor_paths,
                residual_capacity_by_link,
                link_by_arc,
            )
            if feasible:
                t_high = t_mid
                best_path_rate = path_rate
                best_violation = violation
                best_flow_conservation_residual = flow_conservation_residual
                best_capacity_residual = {
                    link_id: load_by_link.get(link_id, 0.0) - residual_capacity_by_link.get(link_id, 0.0)
                    for link_id in residual_capacity_by_link
                }
            else:
                t_low = t_mid

        wcmp_weights: dict[tuple[str, str], list[tuple[list[str], float]]] = {}
        for demand in intra_tor_demands:
            key = (demand.tor_source, demand.tor_destination)
            candidate_paths = tor_paths.get(key, [])
            if not candidate_paths:
                continue
            demand_rate = demand.total_size_mb / max(t_high, 1e-9)
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

        return {
            "t_star_ms": t_high,
            "wcmp_weights": wcmp_weights,
            "iterations": self.stage2_max_iterations,
            "max_violation": best_violation,
            "constraint_residuals": {
                "flow_conservation_residual_by_pair_mb_per_ms": best_flow_conservation_residual,
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
    ) -> tuple[bool, dict[tuple[tuple[str, str], int], float], dict[str, float], float, dict[str, float]]:
        lambda_by_link: dict[str, float] = defaultdict(float)
        path_rate: dict[tuple[tuple[str, str], int], float] = defaultdict(float)
        load_by_link: dict[str, float] = defaultdict(float)

        for _ in range(self.stage2_max_iterations):
            path_rate.clear()
            load_by_link.clear()
            for demand in intra_tor_demands:
                key = (demand.tor_source, demand.tor_destination)
                candidate_paths = tor_paths.get(key, [])
                if not candidate_paths:
                    continue
                demand_rate = demand.total_size_mb / max(t_candidate_ms, 1e-9)
                path = self._select_min_price_path(
                    candidate_paths,
                    lambda_by_link=lambda_by_link,
                    link_by_arc=link_by_arc,
                    include_inter_dc=False,
                )
                if not path:
                    path = candidate_paths[0]
                path_index = candidate_paths.index(path)
                path_rate[(key, path_index)] += demand_rate
                for link_id in self._path_link_ids(path, link_by_arc):
                    if bool(self._link_by_id(link_id, link_by_arc).attributes.get("inter_dc", False)):
                        continue
                    load_by_link[link_id] += demand_rate

            for link_id, load in load_by_link.items():
                cap = max(residual_capacity_by_link.get(link_id, 0.0), 1e-9)
                lambda_by_link[link_id] = max(0.0, lambda_by_link[link_id] + self.gamma * (load - cap))

        max_violation = 0.0
        for link_id, load in load_by_link.items():
            cap = residual_capacity_by_link.get(link_id, 0.0)
            max_violation = max(max_violation, max(0.0, load - cap))

        flow_conservation_residual: dict[str, float] = {}
        for demand in intra_tor_demands:
            key = (demand.tor_source, demand.tor_destination)
            candidate_paths = tor_paths.get(key, [])
            served_rate = sum(path_rate.get((key, path_index), 0.0) for path_index in range(len(candidate_paths)))
            required_rate = demand.total_size_mb / max(t_candidate_ms, 1e-9)
            flow_conservation_residual[f"{demand.tor_source}->{demand.tor_destination}"] = served_rate - required_rate

        return (
            max_violation <= self.feasibility_tolerance,
            dict(path_rate),
            dict(load_by_link),
            max_violation,
            flow_conservation_residual,
        )

    def _build_tor_candidate_paths(self, runtime_state: RuntimeState) -> dict[tuple[str, str], list[list[str]]]:
        tor_nodes = [
            node_id
            for node_id, node in runtime_state.topology.nodes.items()
            if node.node_type == "switch" and str(node.attributes.get("role", "")).lower() == "leaf"
        ]
        result: dict[tuple[str, str], list[list[str]]] = {}
        for src in tor_nodes:
            for dst in tor_nodes:
                if src == dst:
                    continue
                if self._node_dc(runtime_state, src) != self._node_dc(runtime_state, dst):
                    continue
                paths = self._enumerate_shortest_paths(runtime_state.topology.adjacency, src, dst, max_paths=8)
                if paths:
                    result[(src, dst)] = paths
        return result

    def _select_stage1_cross_path(self, path_candidates: list[list[str]], link_by_arc: dict[tuple[str, str], Any]) -> list[str]:
        if not path_candidates:
            return []
        ranked = sorted(
            path_candidates,
            key=lambda path: (
                self._inter_dc_hop_count(path, link_by_arc),
                len(path),
                path,
            ),
        )
        return list(ranked[0])

    def _select_min_price_path(
        self,
        path_candidates: list[list[str]],
        lambda_by_link: dict[str, float],
        link_by_arc: dict[tuple[str, str], Any],
        include_inter_dc: bool,
    ) -> list[str]:
        if not path_candidates:
            return []
        best_path = []
        best_cost = float("inf")
        for path in path_candidates:
            cost = 0.0
            for link_id in self._path_link_ids(path, link_by_arc):
                link = self._link_by_id(link_id, link_by_arc)
                if not include_inter_dc and bool(link.attributes.get("inter_dc", False)):
                    continue
                cost += lambda_by_link.get(link_id, 0.0) / max(float(link.bandwidth_gbps), 1e-9)
            if cost < best_cost:
                best_cost = cost
                best_path = list(path)
        return best_path

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

    def _extract_dci_links_from_path(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> list[str]:
        link_ids: list[str] = []
        for src, dst in zip(path, path[1:]):
            link = link_by_arc.get((src, dst))
            if link is None:
                continue
            if bool(link.attributes.get("inter_dc", False)):
                link_ids.append(link.link_id)
        return link_ids

    def _path_link_ids(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> list[str]:
        link_ids: list[str] = []
        for src, dst in zip(path, path[1:]):
            link = link_by_arc.get((src, dst))
            if link is None:
                return []
            link_ids.append(link.link_id)
        return link_ids

    def _path_latency_ms(self, dci_link_ids: list[str], link_by_arc: dict[tuple[str, str], Any]) -> float:
        if not dci_link_ids:
            return 0.0
        by_id = self._link_id_map(link_by_arc)
        return sum(float(by_id[link_id].latency_us) / 1000.0 for link_id in dci_link_ids if link_id in by_id)

    def _estimate_queue_wait(self, flow_id: str) -> float:
        if self.queue_wait_estimation_mode == "observed":
            return float(self.observed_queue_wait_ms_by_flow.get(flow_id, 0.0))
        return 0.0

    def _inter_dc_hop_count(self, path: list[str], link_by_arc: dict[tuple[str, str], Any]) -> int:
        count = 0
        for src, dst in zip(path, path[1:]):
            link = link_by_arc.get((src, dst))
            if link is not None and bool(link.attributes.get("inter_dc", False)):
                count += 1
        return count

    def _fallback_path(self, path_candidates: list[list[str]]) -> list[str]:
        return list(path_candidates[0]) if path_candidates else []

    def _node_dc(self, runtime_state: RuntimeState, node_id: str) -> str:
        node = runtime_state.topology.nodes.get(node_id)
        if node is None:
            return "unknown"
        return str(node.attributes.get("dc", "unknown"))

    def _node_tor(self, runtime_state: RuntimeState, node_id: str) -> str:
        node = runtime_state.topology.nodes.get(node_id)
        if node is None:
            return ""
        if node.node_type == "switch" and str(node.attributes.get("role", "")).lower() == "leaf":
            return node_id
        candidate = str(node.attributes.get("tor_id", ""))
        if candidate:
            return candidate
        for neighbor in runtime_state.topology.adjacency.get(node_id, []):
            neighbor_node = runtime_state.topology.nodes.get(neighbor)
            if neighbor_node is None:
                continue
            if neighbor_node.node_type == "switch" and str(neighbor_node.attributes.get("role", "")).lower() == "leaf":
                return neighbor
        return ""

    def _enumerate_shortest_paths(
        self,
        adjacency: dict[str, list[str]],
        src: str,
        dst: str,
        max_paths: int,
    ) -> list[list[str]]:
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
            for neighbor in adjacency.get(tail, []):
                if neighbor in path:
                    continue
                queue.append(path + [neighbor])
        return results

    def _link_id_map(self, link_by_arc: dict[tuple[str, str], Any]) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for link in link_by_arc.values():
            mapping[link.link_id] = link
        return mapping

    def _link_by_id(self, link_id: str, link_by_arc: dict[tuple[str, str], Any]) -> Any:
        for link in link_by_arc.values():
            if link.link_id == link_id:
                return link
        raise KeyError(link_id)
