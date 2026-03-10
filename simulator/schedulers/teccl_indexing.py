from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any

from simulator.topology.models import Link
from simulator.topology.models import TopologyGraph
from simulator.workload.models import UnifiedJob


@dataclass(slots=True, frozen=True)
class TECCLEpoch:
    epoch_index: int
    start_time_ms: float
    end_time_ms: float


@dataclass(slots=True, frozen=True)
class TECCLDirectedEdge:
    edge_id: str
    physical_link_id: str
    src: str
    dst: str
    bandwidth_gbps: float
    latency_us: float
    delay_epochs: int
    capacity_mb_per_epoch: float
    attributes: tuple[tuple[str, Any], ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class TECCLCommodity:
    commodity_id: str
    job_id: str
    demand_id: str
    chunk_id: str
    chunk_index: int
    source_node: str
    destination_nodes: tuple[str, ...]
    size_mb: float
    ready_time_ms: float
    ready_epoch_index: int
    dependency_parent_ids: tuple[str, ...] = field(default_factory=tuple)
    metadata: tuple[tuple[str, Any], ...] = field(default_factory=tuple)


@dataclass(slots=True)
class TECCLNodePartition:
    gpu_nodes: tuple[str, ...]
    switch_nodes: tuple[str, ...]
    relay_nodes: tuple[str, ...]
    all_nodes: tuple[str, ...]


@dataclass(slots=True)
class TECCLIndexBundle:
    node_partition: TECCLNodePartition
    directed_edges: tuple[TECCLDirectedEdge, ...]
    edges_by_src: dict[str, tuple[TECCLDirectedEdge, ...]]
    edges_by_dst: dict[str, tuple[TECCLDirectedEdge, ...]]
    epochs: tuple[TECCLEpoch, ...]
    commodities: tuple[TECCLCommodity, ...]


def build_teccl_index_bundle(
    topology: TopologyGraph,
    jobs: list[UnifiedJob],
    epoch_size_ms: float,
    planning_horizon_epochs: int,
    start_time_ms: float = 0.0,
) -> TECCLIndexBundle:
    if epoch_size_ms <= 0:
        raise ValueError("epoch_size_ms must be positive")
    if planning_horizon_epochs <= 0:
        raise ValueError("planning_horizon_epochs must be positive")

    node_partition = build_node_partition(topology)
    directed_edges = build_directed_edge_index(topology, epoch_size_ms)
    edges_by_src = _group_edges_by_endpoint(directed_edges, endpoint="src")
    edges_by_dst = _group_edges_by_endpoint(directed_edges, endpoint="dst")
    epochs = build_epoch_index(epoch_size_ms, planning_horizon_epochs, start_time_ms=start_time_ms)
    commodities = build_commodity_index(jobs, epoch_size_ms=epoch_size_ms, start_time_ms=start_time_ms)
    return TECCLIndexBundle(
        node_partition=node_partition,
        directed_edges=directed_edges,
        edges_by_src=edges_by_src,
        edges_by_dst=edges_by_dst,
        epochs=epochs,
        commodities=commodities,
    )


def build_node_partition(topology: TopologyGraph) -> TECCLNodePartition:
    gpu_nodes: list[str] = []
    switch_nodes: list[str] = []
    relay_nodes: list[str] = []
    all_nodes = sorted(topology.nodes)

    for node_id in all_nodes:
        node = topology.nodes[node_id]
        if node.node_type == "gpu":
            gpu_nodes.append(node_id)
        elif node.node_type == "switch":
            switch_nodes.append(node_id)
        else:
            relay_nodes.append(node_id)

    return TECCLNodePartition(
        gpu_nodes=tuple(gpu_nodes),
        switch_nodes=tuple(switch_nodes),
        relay_nodes=tuple(relay_nodes),
        all_nodes=tuple(all_nodes),
    )


def build_directed_edge_index(topology: TopologyGraph, epoch_size_ms: float) -> tuple[TECCLDirectedEdge, ...]:
    directed_edges: list[TECCLDirectedEdge] = []
    for link in topology.links:
        directed_edges.append(_link_to_directed_edge(link, link.src, link.dst, epoch_size_ms, reverse=False))
        if link.bidirectional:
            directed_edges.append(_link_to_directed_edge(link, link.dst, link.src, epoch_size_ms, reverse=True))
    directed_edges.sort(key=lambda item: (item.src, item.dst, item.edge_id))
    return tuple(directed_edges)


def build_epoch_index(
    epoch_size_ms: float,
    planning_horizon_epochs: int,
    start_time_ms: float = 0.0,
) -> tuple[TECCLEpoch, ...]:
    return tuple(
        TECCLEpoch(
            epoch_index=epoch_index,
            start_time_ms=start_time_ms + epoch_index * epoch_size_ms,
            end_time_ms=start_time_ms + (epoch_index + 1) * epoch_size_ms,
        )
        for epoch_index in range(planning_horizon_epochs)
    )


def build_commodity_index(
    jobs: list[UnifiedJob],
    epoch_size_ms: float,
    start_time_ms: float = 0.0,
) -> tuple[TECCLCommodity, ...]:
    commodities: list[TECCLCommodity] = []
    for job in sorted(jobs, key=lambda item: item.job_id):
        for demand in sorted(job.communication_demands, key=lambda item: item.demand_id):
            for chunk in sorted(demand.chunks, key=lambda item: item.chunk_index):
                for source_node in sorted(chunk.source_set):
                    destination_nodes = tuple(sorted(destination for destination in chunk.destination_set if destination != source_node))
                    commodity_id = f"{chunk.chunk_id}::{source_node}"
                    ready_epoch_index = _time_to_epoch_index(chunk.ready_time_ms, epoch_size_ms, start_time_ms)
                    commodity_metadata = {
                        "collective_type": demand.collective_type,
                        "job_arrival_time_ms": job.arrival_time_ms,
                        "participant_count": len(job.participants),
                        **dict(chunk.metadata),
                        **dict(demand.metadata),
                    }
                    commodities.append(
                        TECCLCommodity(
                            commodity_id=commodity_id,
                            job_id=job.job_id,
                            demand_id=demand.demand_id,
                            chunk_id=chunk.chunk_id,
                            chunk_index=chunk.chunk_index,
                            source_node=source_node,
                            destination_nodes=destination_nodes,
                            size_mb=chunk.size_mb,
                            ready_time_ms=chunk.ready_time_ms,
                            ready_epoch_index=ready_epoch_index,
                            dependency_parent_ids=tuple(chunk.dependency_parent_ids),
                            metadata=tuple(sorted(commodity_metadata.items())),
                        )
                    )
    commodities.sort(key=lambda item: (item.job_id, item.chunk_index, item.source_node, item.commodity_id))
    return tuple(commodities)


def _group_edges_by_endpoint(
    directed_edges: tuple[TECCLDirectedEdge, ...],
    endpoint: str,
) -> dict[str, tuple[TECCLDirectedEdge, ...]]:
    grouped: dict[str, list[TECCLDirectedEdge]] = {}
    for edge in directed_edges:
        key = edge.src if endpoint == "src" else edge.dst
        grouped.setdefault(key, []).append(edge)
    return {key: tuple(value) for key, value in grouped.items()}


def _link_to_directed_edge(
    link: Link,
    src: str,
    dst: str,
    epoch_size_ms: float,
    reverse: bool,
) -> TECCLDirectedEdge:
    delay_epochs = max(0, ceil((link.latency_us / 1000.0) / epoch_size_ms))
    direction = "rev" if reverse else "fwd"
    capacity_mb_per_epoch = link.bandwidth_gbps * 0.125 * epoch_size_ms
    return TECCLDirectedEdge(
        edge_id=f"{link.link_id}::{direction}",
        physical_link_id=link.link_id,
        src=src,
        dst=dst,
        bandwidth_gbps=link.bandwidth_gbps,
        latency_us=link.latency_us,
        delay_epochs=delay_epochs,
        capacity_mb_per_epoch=capacity_mb_per_epoch,
        attributes=tuple(sorted(link.attributes.items())),
    )


def _time_to_epoch_index(time_ms: float, epoch_size_ms: float, start_time_ms: float) -> int:
    if time_ms <= start_time_ms:
        return 0
    return int((time_ms - start_time_ms) // epoch_size_ms)