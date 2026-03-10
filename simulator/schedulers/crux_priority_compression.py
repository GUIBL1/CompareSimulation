from __future__ import annotations

import hashlib
import random
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field

from simulator.schedulers.crux_model_input import CruxModelInput


@dataclass(slots=True)
class CruxContentionEdge:
    source_job_id: str
    destination_job_id: str
    weight: float
    overlapping_link_ids: list[str] = field(default_factory=list)
    source_flow_ids: list[str] = field(default_factory=list)
    destination_flow_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CruxContentionDag:
    node_ids: list[str]
    edges: list[CruxContentionEdge]
    outgoing_edges: dict[str, list[CruxContentionEdge]]
    incoming_edges: dict[str, list[CruxContentionEdge]]
    metadata: dict[str, object] = field(default_factory=dict)

    def to_debug_dict(self) -> dict[str, object]:
        return {
            "node_ids": list(self.node_ids),
            "edges": [asdict(edge) for edge in self.edges],
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class CruxPriorityCompressionResult:
    hardware_priority_by_job_id: dict[str, int]
    ordered_job_ids: list[str]
    sampled_topological_orders: list[list[str]]
    selected_topological_order: list[str]
    segment_ranges: list[tuple[int, int]]
    segment_job_ids: list[list[str]]
    total_cut_weight: float
    lost_cut_weight: float
    total_edge_weight: float
    used_hardware_priority_count: int
    topological_order_sample_count: int
    metadata: dict[str, object] = field(default_factory=dict)

    def to_debug_dict(self) -> dict[str, object]:
        return {
            "hardware_priority_by_job_id": dict(self.hardware_priority_by_job_id),
            "ordered_job_ids": list(self.ordered_job_ids),
            "sampled_topological_orders": [list(order) for order in self.sampled_topological_orders],
            "selected_topological_order": list(self.selected_topological_order),
            "segment_ranges": [list(item) for item in self.segment_ranges],
            "segment_job_ids": [list(group) for group in self.segment_job_ids],
            "total_cut_weight": self.total_cut_weight,
            "lost_cut_weight": self.lost_cut_weight,
            "total_edge_weight": self.total_edge_weight,
            "used_hardware_priority_count": self.used_hardware_priority_count,
            "topological_order_sample_count": self.topological_order_sample_count,
            "metadata": dict(self.metadata),
        }


def build_contention_dag(model_input: CruxModelInput) -> CruxContentionDag:
    node_ids = sorted(
        model_input.job_by_id,
        key=lambda job_id: (
            -float(model_input.job_by_id[job_id].priority.priority_score_pj if model_input.job_by_id[job_id].priority is not None else 0.0),
            model_input.job_by_id[job_id].arrival_time_ms,
            job_id,
        ),
    )
    selected_link_ids_by_job: dict[str, set[str]] = {}
    selected_flow_ids_by_job: dict[str, list[str]] = {}
    for job_id, job_input in model_input.job_by_id.items():
        selected_link_ids_by_job[job_id] = set()
        selected_flow_ids_by_job[job_id] = []
        for flow_id in job_input.flow_ids:
            flow_input = model_input.flow_by_id.get(flow_id)
            if flow_input is None or not flow_input.selected_path_id:
                continue
            selected_flow_ids_by_job[job_id].append(flow_id)
            path_input = model_input.path_by_id.get(flow_input.selected_path_id)
            if path_input is not None:
                selected_link_ids_by_job[job_id].update(path_input.load.link_ids)

    edges: list[CruxContentionEdge] = []
    outgoing_edges: dict[str, list[CruxContentionEdge]] = {job_id: [] for job_id in node_ids}
    incoming_edges: dict[str, list[CruxContentionEdge]] = {job_id: [] for job_id in node_ids}
    overlap_pair_count = 0
    for source_index, source_job_id in enumerate(node_ids):
        source_job = model_input.job_by_id[source_job_id]
        source_priority = float(source_job.priority.priority_score_pj if source_job.priority is not None else 0.0)
        source_intensity = float(source_job.intensity.intensity_value if source_job.intensity is not None else 0.0)
        source_links = selected_link_ids_by_job[source_job_id]
        for destination_job_id in node_ids[source_index + 1 :]:
            destination_job = model_input.job_by_id[destination_job_id]
            destination_priority = float(destination_job.priority.priority_score_pj if destination_job.priority is not None else 0.0)
            if source_priority <= destination_priority:
                continue
            overlapping_link_ids = sorted(source_links & selected_link_ids_by_job[destination_job_id])
            if not overlapping_link_ids:
                continue
            overlap_pair_count += 1
            edge = CruxContentionEdge(
                source_job_id=source_job_id,
                destination_job_id=destination_job_id,
                weight=source_intensity,
                overlapping_link_ids=overlapping_link_ids,
                source_flow_ids=list(selected_flow_ids_by_job[source_job_id]),
                destination_flow_ids=list(selected_flow_ids_by_job[destination_job_id]),
            )
            edges.append(edge)
            outgoing_edges[source_job_id].append(edge)
            incoming_edges[destination_job_id].append(edge)

    return CruxContentionDag(
        node_ids=node_ids,
        edges=edges,
        outgoing_edges=outgoing_edges,
        incoming_edges=incoming_edges,
        metadata={
            "node_count": len(node_ids),
            "edge_count": len(edges),
            "overlapping_link_pair_count": overlap_pair_count,
            "total_edge_weight": sum(edge.weight for edge in edges),
        },
    )


def compress_contention_dag(
    dag: CruxContentionDag,
    hardware_priority_count: int,
    topological_order_sample_count: int,
) -> CruxPriorityCompressionResult:
    if not dag.node_ids:
        return CruxPriorityCompressionResult(
            hardware_priority_by_job_id={},
            ordered_job_ids=[],
            sampled_topological_orders=[],
            selected_topological_order=[],
            segment_ranges=[],
            segment_job_ids=[],
            total_cut_weight=0.0,
            lost_cut_weight=0.0,
            total_edge_weight=0.0,
            used_hardware_priority_count=0,
            topological_order_sample_count=0,
        )

    sample_count = max(1, topological_order_sample_count)
    sampled_orders = _sample_topological_orders(dag, sample_count)
    total_edge_weight = float(dag.metadata.get("total_edge_weight", 0.0) or 0.0)
    best_order = sampled_orders[0]
    best_cut_weight = -1.0
    best_boundaries: list[tuple[int, int]] = [(0, len(best_order))]
    best_segments: list[list[str]] = [list(best_order)]
    used_priority_count = 1

    for order in sampled_orders:
        segment_count = max(1, min(max(1, hardware_priority_count), len(order)))
        segment_ranges, segment_job_ids, cut_weight = _run_contiguous_partition_dp(dag, order, segment_count)
        if cut_weight > best_cut_weight + 1e-12:
            best_order = list(order)
            best_cut_weight = cut_weight
            best_boundaries = segment_ranges
            best_segments = segment_job_ids
            used_priority_count = len(segment_job_ids)

    hardware_priority_by_job_id: dict[str, int] = {}
    for priority_level, job_group in enumerate(best_segments):
        for job_id in job_group:
            hardware_priority_by_job_id[job_id] = priority_level

    return CruxPriorityCompressionResult(
        hardware_priority_by_job_id=hardware_priority_by_job_id,
        ordered_job_ids=list(best_order),
        sampled_topological_orders=[list(order) for order in sampled_orders],
        selected_topological_order=list(best_order),
        segment_ranges=best_boundaries,
        segment_job_ids=best_segments,
        total_cut_weight=max(best_cut_weight, 0.0),
        lost_cut_weight=max(total_edge_weight - max(best_cut_weight, 0.0), 0.0),
        total_edge_weight=total_edge_weight,
        used_hardware_priority_count=used_priority_count,
        topological_order_sample_count=len(sampled_orders),
        metadata={
            "compression_mode": "dag_max_k_cut_contiguous_dp",
        },
    )


def _sample_topological_orders(dag: CruxContentionDag, sample_count: int) -> list[list[str]]:
    base_seed_material = "|".join(dag.node_ids + [f"{edge.source_job_id}>{edge.destination_job_id}:{edge.weight}" for edge in dag.edges])
    base_seed = int.from_bytes(hashlib.sha1(base_seed_material.encode("utf-8")).digest()[:8], byteorder="big", signed=False)
    unique_orders: list[list[str]] = []
    seen_orders: set[tuple[str, ...]] = set()
    for sample_index in range(sample_count):
        rng = random.Random(base_seed + sample_index)
        order = _sample_single_topological_order(dag, rng, prefer_priority_order=(sample_index == 0))
        order_key = tuple(order)
        if order_key in seen_orders:
            continue
        seen_orders.add(order_key)
        unique_orders.append(order)
    if not unique_orders:
        unique_orders.append(list(dag.node_ids))
    return unique_orders


def _sample_single_topological_order(
    dag: CruxContentionDag,
    rng: random.Random,
    prefer_priority_order: bool,
) -> list[str]:
    indegree = {node_id: len(dag.incoming_edges.get(node_id, [])) for node_id in dag.node_ids}
    available = [node_id for node_id in dag.node_ids if indegree[node_id] == 0]
    ordered: list[str] = []
    while available:
        if prefer_priority_order:
            available.sort(key=lambda node_id: dag.node_ids.index(node_id))
            node_id = available.pop(0)
        else:
            available.sort()
            node_id = available.pop(rng.randrange(len(available)))
        ordered.append(node_id)
        for edge in dag.outgoing_edges.get(node_id, []):
            indegree[edge.destination_job_id] -= 1
            if indegree[edge.destination_job_id] == 0:
                available.append(edge.destination_job_id)
    if len(ordered) != len(dag.node_ids):
        remaining = [node_id for node_id in dag.node_ids if node_id not in ordered]
        ordered.extend(remaining)
    return ordered


def _run_contiguous_partition_dp(
    dag: CruxContentionDag,
    order: list[str],
    segment_count: int,
) -> tuple[list[tuple[int, int]], list[list[str]], float]:
    node_count = len(order)
    if node_count == 0:
        return [], [], 0.0
    segment_count = max(1, min(segment_count, node_count))
    position_by_job_id = {job_id: index + 1 for index, job_id in enumerate(order)}
    crossing_weight = [[0.0 for _ in range(node_count + 1)] for _ in range(node_count + 1)]
    for left_boundary in range(node_count + 1):
        for right_boundary in range(left_boundary + 1, node_count + 1):
            weight = 0.0
            for edge in dag.edges:
                source_position = position_by_job_id[edge.source_job_id]
                destination_position = position_by_job_id[edge.destination_job_id]
                if source_position <= left_boundary < destination_position <= right_boundary:
                    weight += edge.weight
            crossing_weight[left_boundary][right_boundary] = weight

    negative_inf = float("-inf")
    dp = [[negative_inf for _ in range(node_count + 1)] for _ in range(segment_count + 1)]
    parent = [[-1 for _ in range(node_count + 1)] for _ in range(segment_count + 1)]
    dp[1][0] = 0.0
    for prefix_length in range(1, node_count + 1):
        dp[1][prefix_length] = 0.0
    for used_segments in range(2, segment_count + 1):
        for prefix_length in range(used_segments, node_count + 1):
            best_value = negative_inf
            best_parent = -1
            for previous_prefix in range(used_segments - 1, prefix_length):
                candidate = dp[used_segments - 1][previous_prefix] + crossing_weight[previous_prefix][prefix_length]
                if candidate > best_value + 1e-12:
                    best_value = candidate
                    best_parent = previous_prefix
            dp[used_segments][prefix_length] = best_value
            parent[used_segments][prefix_length] = best_parent

    best_segment_count = 1
    best_value = dp[1][node_count]
    for used_segments in range(2, segment_count + 1):
        if dp[used_segments][node_count] > best_value + 1e-12:
            best_value = dp[used_segments][node_count]
            best_segment_count = used_segments

    segment_ranges_reversed: list[tuple[int, int]] = []
    cursor = node_count
    used_segments = best_segment_count
    while used_segments > 1:
        previous_cursor = parent[used_segments][cursor]
        if previous_cursor < 0:
            break
        segment_ranges_reversed.append((previous_cursor, cursor))
        cursor = previous_cursor
        used_segments -= 1
    segment_ranges_reversed.append((0, cursor))
    segment_ranges = list(reversed(segment_ranges_reversed))
    segment_job_ids = [order[start:end] for start, end in segment_ranges]
    return segment_ranges, segment_job_ids, max(best_value, 0.0)