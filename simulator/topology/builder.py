from __future__ import annotations

from simulator.config.models import TopologyConfig
from simulator.topology.models import Link
from simulator.topology.models import Node
from simulator.topology.models import TopologyGraph


def build_topology(config: TopologyConfig) -> TopologyGraph:
    if config.topology.mode == "explicit":
        return _build_explicit_topology(config)
    if config.topology.mode == "generated":
        return _build_generated_topology(config)
    raise ValueError(f"Unsupported topology mode: {config.topology.mode}")


def _build_explicit_topology(config: TopologyConfig) -> TopologyGraph:
    nodes = {
        str(item["node_id"]): Node(
            node_id=str(item["node_id"]),
            node_type=str(item["node_type"]),
            attributes={k: v for k, v in item.items() if k not in {"node_id", "node_type"}},
        )
        for item in config.nodes.explicit_nodes
    }
    links = [
        Link(
            link_id=str(item.get("link_id", f"{item['src']}->{item['dst']}")),
            src=str(item["src"]),
            dst=str(item["dst"]),
            bandwidth_gbps=float(item.get("bandwidth_gbps", config.links.default_bandwidth_gbps)),
            latency_us=float(item.get("latency_us", config.links.default_latency_us)),
            bidirectional=bool(item.get("bidirectional", config.links.bidirectional)),
            attributes={k: v for k, v in item.items() if k not in {"link_id", "src", "dst", "bandwidth_gbps", "latency_us", "bidirectional"}},
        )
        for item in config.links.explicit_links
    ]
    return TopologyGraph(name=config.meta.name, nodes=nodes, links=links, adjacency=_build_adjacency(links))


def _build_generated_topology(config: TopologyConfig) -> TopologyGraph:
    nodes: dict[str, Node] = {}
    links: list[Link] = []

    for host_index in range(config.nodes.host_count):
        host_id = f"host_{host_index}"
        nodes[host_id] = Node(node_id=host_id, node_type="host")
        for gpu_index in range(config.nodes.gpu_per_host):
            gpu_id = f"gpu_{host_index}_{gpu_index}"
            nodes[gpu_id] = Node(node_id=gpu_id, node_type="gpu", attributes={"host_id": host_id})

    for switch_index in range(config.nodes.switch_count):
        switch_id = f"switch_{switch_index}"
        nodes[switch_id] = Node(node_id=switch_id, node_type="switch")

    return TopologyGraph(name=config.meta.name, nodes=nodes, links=links, adjacency={node_id: [] for node_id in nodes})


def _build_adjacency(links: list[Link]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for link in links:
        adjacency.setdefault(link.src, []).append(link.dst)
        if link.bidirectional:
            adjacency.setdefault(link.dst, []).append(link.src)
    return adjacency
