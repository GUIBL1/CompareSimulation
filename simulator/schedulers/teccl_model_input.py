from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from simulator.schedulers.teccl_indexing import TECCLCommodity
from simulator.schedulers.teccl_indexing import TECCLDirectedEdge
from simulator.schedulers.teccl_indexing import TECCLIndexBundle
from simulator.schedulers.teccl_indexing import build_teccl_index_bundle
from simulator.topology.models import TopologyGraph
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class TECCLDemandEntry:
    source_node: str
    destination_node: str
    commodity_id: str
    required_amount_mb: float


@dataclass(slots=True)
class TECCLInitialBufferEntry:
    commodity_id: str
    node_id: str
    epoch_index: int
    initial_amount_mb: float


@dataclass(slots=True)
class TECCLModelInput:
    topology_name: str
    epoch_size_ms: float
    planning_horizon_epochs: int
    planning_horizon_ms: float
    index_bundle: TECCLIndexBundle
    demand_entries: tuple[TECCLDemandEntry, ...]
    demand_matrix: dict[tuple[str, str, str], float]
    initial_buffer_entries: tuple[TECCLInitialBufferEntry, ...]
    initial_buffer_matrix: dict[tuple[str, str, int], float]
    capacity_by_edge_and_epoch: dict[tuple[str, int], float]
    delay_epochs_by_edge: dict[str, int]
    commodity_by_id: dict[str, TECCLCommodity]
    edge_by_id: dict[str, TECCLDirectedEdge]
    summary: dict[str, float | int]
    metadata: dict[str, object] = field(default_factory=dict)


def build_teccl_model_input(
    topology: TopologyGraph,
    jobs: list[UnifiedJob],
    epoch_size_ms: float,
    planning_horizon_epochs: int,
    start_time_ms: float = 0.0,
) -> TECCLModelInput:
    index_bundle = build_teccl_index_bundle(
        topology=topology,
        jobs=jobs,
        epoch_size_ms=epoch_size_ms,
        planning_horizon_epochs=planning_horizon_epochs,
        start_time_ms=start_time_ms,
    )
    demand_entries = _build_demand_entries(index_bundle.commodities)
    demand_matrix = {
        (entry.source_node, entry.destination_node, entry.commodity_id): entry.required_amount_mb
        for entry in demand_entries
    }
    initial_buffer_entries = _build_initial_buffer_entries(index_bundle.commodities)
    initial_buffer_matrix = {
        (entry.commodity_id, entry.node_id, entry.epoch_index): entry.initial_amount_mb
        for entry in initial_buffer_entries
    }
    capacity_by_edge_and_epoch = {
        (edge.edge_id, epoch.epoch_index): edge.capacity_mb_per_epoch
        for edge in index_bundle.directed_edges
        for epoch in index_bundle.epochs
    }
    delay_epochs_by_edge = {edge.edge_id: edge.delay_epochs for edge in index_bundle.directed_edges}
    commodity_by_id = {commodity.commodity_id: commodity for commodity in index_bundle.commodities}
    edge_by_id = {edge.edge_id: edge for edge in index_bundle.directed_edges}
    summary = _build_summary(index_bundle, demand_entries, epoch_size_ms, planning_horizon_epochs)
    return TECCLModelInput(
        topology_name=topology.name,
        epoch_size_ms=epoch_size_ms,
        planning_horizon_epochs=planning_horizon_epochs,
        planning_horizon_ms=planning_horizon_epochs * epoch_size_ms,
        index_bundle=index_bundle,
        demand_entries=demand_entries,
        demand_matrix=demand_matrix,
        initial_buffer_entries=initial_buffer_entries,
        initial_buffer_matrix=initial_buffer_matrix,
        capacity_by_edge_and_epoch=capacity_by_edge_and_epoch,
        delay_epochs_by_edge=delay_epochs_by_edge,
        commodity_by_id=commodity_by_id,
        edge_by_id=edge_by_id,
        summary=summary,
        metadata={
            "start_time_ms": start_time_ms,
            "max_ready_epoch_index": max((commodity.ready_epoch_index for commodity in index_bundle.commodities), default=0),
        },
    )


def infer_planning_horizon_epochs(
    jobs: list[UnifiedJob],
    topology: TopologyGraph,
    epoch_size_ms: float,
    max_time_ms: float | None = None,
) -> int:
    if epoch_size_ms <= 0:
        raise ValueError("epoch_size_ms must be positive")
    latest_ready_time_ms = max(
        (chunk.ready_time_ms for job in jobs for demand in job.communication_demands for chunk in demand.chunks),
        default=0.0,
    )
    max_link_delay_ms = max((link.latency_us / 1000.0 for link in topology.links), default=0.0)
    max_candidate_path_hops = max(
        (len(path) - 1 for paths in topology.candidate_paths.values() for path in paths if path),
        default=1,
    )
    worst_case_tail_ms = max_link_delay_ms * max_candidate_path_hops
    upper_bound_ms = max_time_ms if max_time_ms is not None and max_time_ms > 0 else latest_ready_time_ms + worst_case_tail_ms
    return max(1, ceil(upper_bound_ms / epoch_size_ms))


def _build_demand_entries(commodities: tuple[TECCLCommodity, ...]) -> tuple[TECCLDemandEntry, ...]:
    entries: list[TECCLDemandEntry] = []
    for commodity in commodities:
        for destination_node in commodity.destination_nodes:
            entries.append(
                TECCLDemandEntry(
                    source_node=commodity.source_node,
                    destination_node=destination_node,
                    commodity_id=commodity.commodity_id,
                    required_amount_mb=commodity.size_mb,
                )
            )
    entries.sort(key=lambda item: (item.source_node, item.destination_node, item.commodity_id))
    return tuple(entries)


def _build_initial_buffer_entries(commodities: tuple[TECCLCommodity, ...]) -> tuple[TECCLInitialBufferEntry, ...]:
    entries = [
        TECCLInitialBufferEntry(
            commodity_id=commodity.commodity_id,
            node_id=commodity.source_node,
            epoch_index=commodity.ready_epoch_index,
            initial_amount_mb=commodity.size_mb,
        )
        for commodity in commodities
    ]
    entries.sort(key=lambda item: (item.epoch_index, item.node_id, item.commodity_id))
    return tuple(entries)


def _build_summary(
    index_bundle: TECCLIndexBundle,
    demand_entries: tuple[TECCLDemandEntry, ...],
    epoch_size_ms: float,
    planning_horizon_epochs: int,
) -> dict[str, float | int]:
    total_demand_mb = sum(entry.required_amount_mb for entry in demand_entries)
    return {
        "epoch_size_ms": epoch_size_ms,
        "planning_horizon_epochs": planning_horizon_epochs,
        "node_count": len(index_bundle.node_partition.all_nodes),
        "gpu_node_count": len(index_bundle.node_partition.gpu_nodes),
        "switch_node_count": len(index_bundle.node_partition.switch_nodes),
        "relay_node_count": len(index_bundle.node_partition.relay_nodes),
        "directed_edge_count": len(index_bundle.directed_edges),
        "commodity_count": len(index_bundle.commodities),
        "destination_pair_count": len(demand_entries),
        "total_demand_mb": total_demand_mb,
    }