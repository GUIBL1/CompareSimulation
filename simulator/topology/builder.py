from __future__ import annotations

from collections import deque

from simulator.config.models import TopologyConfig
from simulator.topology.models import Link
from simulator.topology.models import Node
from simulator.topology.models import TopologyGraph


def build_topology(config: TopologyConfig) -> TopologyGraph:
    if config.topology.mode == "explicit":
        topology = _build_explicit_topology(config)
    elif config.topology.mode == "generated":
        topology = _build_generated_topology(config)
    else:
        raise ValueError(f"Unsupported topology mode: {config.topology.mode}")

    topology.candidate_paths = _build_candidate_paths(
        topology,
        max_paths=max(1, config.routing.max_paths_per_pair),
    )
    return topology


def _build_explicit_topology(config: TopologyConfig) -> TopologyGraph:
    nodes = {
        str(item["node_id"]): Node(
            node_id=str(item["node_id"]),
            node_type=str(item["node_type"]),
            attributes={k: v for k, v in item.items() if k not in {"node_id", "node_type"}},
        )
        for item in config.nodes.explicit_nodes
    }

    links: list[Link] = []
    for item in config.links.explicit_links:
        src = str(item["src"])
        dst = str(item["dst"])
        if src not in nodes or dst not in nodes:
            raise ValueError(f"Explicit link {src}->{dst} references undefined nodes")
        links.append(
            _create_link(
                src=src,
                dst=dst,
                default_bandwidth_gbps=config.links.default_bandwidth_gbps,
                default_latency_us=config.links.default_latency_us,
                default_bidirectional=config.links.bidirectional,
                link_data=item,
            )
        )

    _apply_link_overrides(links, config.links.overrides)
    adjacency = _build_adjacency(nodes.keys(), links)
    return TopologyGraph(name=config.meta.name, nodes=nodes, links=links, adjacency=adjacency)


def _build_generated_topology(config: TopologyConfig) -> TopologyGraph:
    topology_type = config.topology.type.strip().lower()
    if topology_type != "fat_tree":
        raise ValueError(f"Unsupported generated topology type: {config.topology.type}")

    nodes: dict[str, Node] = {}
    links: list[Link] = []
    _build_fat_tree(config, nodes, links)
    _apply_link_overrides(links, config.links.overrides)
    adjacency = _build_adjacency(nodes.keys(), links)
    return TopologyGraph(name=config.meta.name, nodes=nodes, links=links, adjacency=adjacency)


def _build_fat_tree(config: TopologyConfig, nodes: dict[str, Node], links: list[Link]) -> None:
    parameters = config.topology.parameters
    k = int(parameters.get("k", 0))
    hosts_per_tor = int(parameters.get("hosts_per_tor", max(1, k // 2)))
    if k < 2 or k % 2 != 0:
        raise ValueError("fat_tree requires an even k >= 2")

    tors_per_pod = k // 2
    aggs_per_pod = k // 2
    core_per_group = k // 2
    total_tors = k * tors_per_pod
    expected_hosts = total_tors * hosts_per_tor
    expected_switches = total_tors + (k * aggs_per_pod) + (core_per_group * core_per_group)

    if config.nodes.host_count != expected_hosts:
        raise ValueError(
            f"fat_tree host_count mismatch: expected {expected_hosts}, got {config.nodes.host_count}"
        )
    if config.nodes.switch_count != expected_switches:
        raise ValueError(
            f"fat_tree switch_count mismatch: expected {expected_switches}, got {config.nodes.switch_count}"
        )

    default_bandwidth = config.links.default_bandwidth_gbps
    default_latency = config.links.default_latency_us
    host_nic_bandwidth = config.constraints.host_nic_bandwidth_gbps or default_bandwidth
    host_index = 0

    tor_ids: list[list[str]] = []
    agg_ids: list[list[str]] = []
    for pod_index in range(k):
        pod_tors: list[str] = []
        pod_aggs: list[str] = []
        for tor_index in range(tors_per_pod):
            tor_id = f"tor_p{pod_index}_{tor_index}"
            nodes[tor_id] = Node(
                node_id=tor_id,
                node_type="switch",
                attributes={"role": "tor", "pod_index": pod_index, "switch_index": tor_index},
            )
            pod_tors.append(tor_id)
        for agg_index in range(aggs_per_pod):
            agg_id = f"agg_p{pod_index}_{agg_index}"
            nodes[agg_id] = Node(
                node_id=agg_id,
                node_type="switch",
                attributes={"role": "aggregation", "pod_index": pod_index, "switch_index": agg_index},
            )
            pod_aggs.append(agg_id)
        tor_ids.append(pod_tors)
        agg_ids.append(pod_aggs)

    core_ids: list[list[str]] = []
    for group_index in range(core_per_group):
        group_ids: list[str] = []
        for core_index in range(core_per_group):
            core_id = f"core_g{group_index}_{core_index}"
            nodes[core_id] = Node(
                node_id=core_id,
                node_type="switch",
                attributes={"role": "core", "group_index": group_index, "switch_index": core_index},
            )
            group_ids.append(core_id)
        core_ids.append(group_ids)

    for pod_index, pod_tors in enumerate(tor_ids):
        for tor_index, tor_id in enumerate(pod_tors):
            for host_offset in range(hosts_per_tor):
                host_id = f"host_{host_index}"
                nodes[host_id] = Node(
                    node_id=host_id,
                    node_type="host",
                    attributes={"tor_id": tor_id, "pod_index": pod_index, "host_index": host_index},
                )
                links.append(
                    _create_link(
                        src=host_id,
                        dst=tor_id,
                        default_bandwidth_gbps=host_nic_bandwidth,
                        default_latency_us=default_latency,
                        default_bidirectional=config.links.bidirectional,
                    )
                )
                for gpu_index in range(config.nodes.gpu_per_host):
                    gpu_id = f"gpu_{host_index}_{gpu_index}"
                    nodes[gpu_id] = Node(
                        node_id=gpu_id,
                        node_type="gpu",
                        attributes={
                            "host_id": host_id,
                            "tor_id": tor_id,
                            "pod_index": pod_index,
                            "gpu_index": gpu_index,
                        },
                    )
                    links.append(
                        _create_link(
                            src=gpu_id,
                            dst=host_id,
                            default_bandwidth_gbps=host_nic_bandwidth,
                            default_latency_us=0.0,
                            default_bidirectional=True,
                            link_data={"attributes": {"internal": True}},
                        )
                    )
                host_index += 1

            for agg_id in agg_ids[pod_index]:
                links.append(
                    _create_link(
                        src=tor_id,
                        dst=agg_id,
                        default_bandwidth_gbps=default_bandwidth,
                        default_latency_us=default_latency,
                        default_bidirectional=config.links.bidirectional,
                    )
                )

    for pod_index, pod_aggs in enumerate(agg_ids):
        for agg_index, agg_id in enumerate(pod_aggs):
            for core_id in core_ids[agg_index]:
                links.append(
                    _create_link(
                        src=agg_id,
                        dst=core_id,
                        default_bandwidth_gbps=default_bandwidth,
                        default_latency_us=default_latency,
                        default_bidirectional=config.links.bidirectional,
                    )
                )


def _build_adjacency(node_ids, links: list[Link]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for link in links:
        adjacency.setdefault(link.src, []).append(link.dst)
        if link.bidirectional:
            adjacency.setdefault(link.dst, []).append(link.src)
    return {node_id: sorted(set(neighbors)) for node_id, neighbors in adjacency.items()}


def _create_link(
    src: str,
    dst: str,
    default_bandwidth_gbps: float,
    default_latency_us: float,
    default_bidirectional: bool,
    link_data: dict | None = None,
) -> Link:
    link_data = link_data or {}
    attribute_data = dict(link_data.get("attributes", {}))
    for key, value in link_data.items():
        if key not in {"link_id", "src", "dst", "bandwidth_gbps", "latency_us", "bidirectional", "attributes"}:
            attribute_data[key] = value

    return Link(
        link_id=str(link_data.get("link_id", f"{src}->{dst}")),
        src=src,
        dst=dst,
        bandwidth_gbps=float(link_data.get("bandwidth_gbps", default_bandwidth_gbps)),
        latency_us=float(link_data.get("latency_us", default_latency_us)),
        bidirectional=bool(link_data.get("bidirectional", default_bidirectional)),
        attributes=attribute_data,
    )


def _apply_link_overrides(links: list[Link], overrides: list[dict]) -> None:
    for override in overrides:
        for link in links:
            if _matches_override(link, override):
                if "bandwidth_gbps" in override:
                    link.bandwidth_gbps = float(override["bandwidth_gbps"])
                if "latency_us" in override:
                    link.latency_us = float(override["latency_us"])
                if "bidirectional" in override:
                    link.bidirectional = bool(override["bidirectional"])
                extra_attributes = dict(override.get("attributes", {}))
                for key, value in override.items():
                    if key not in {"link_id", "src", "dst", "bandwidth_gbps", "latency_us", "bidirectional", "attributes"}:
                        extra_attributes[key] = value
                if extra_attributes:
                    link.attributes.update(extra_attributes)


def _matches_override(link: Link, override: dict) -> bool:
    if "link_id" in override and str(override["link_id"]) == link.link_id:
        return True
    src = override.get("src")
    dst = override.get("dst")
    if src is None or dst is None:
        return False
    src = str(src)
    dst = str(dst)
    if link.src == src and link.dst == dst:
        return True
    return link.bidirectional and link.src == dst and link.dst == src


def _build_candidate_paths(topology: TopologyGraph, max_paths: int) -> dict[tuple[str, str], list[list[str]]]:
    endpoints = [
        node_id
        for node_id, node in topology.nodes.items()
        if node.node_type in {"gpu", "host"}
    ]
    candidate_paths: dict[tuple[str, str], list[list[str]]] = {}
    for src in endpoints:
        for dst in endpoints:
            if src == dst:
                continue
            paths = _enumerate_shortest_paths(topology.adjacency, src, dst, max_paths)
            if paths:
                candidate_paths[(src, dst)] = paths
    return candidate_paths


def _enumerate_shortest_paths(
    adjacency: dict[str, list[str]],
    src: str,
    dst: str,
    max_paths: int,
) -> list[list[str]]:
    queue: deque[list[str]] = deque([[src]])
    results: list[list[str]] = []
    shortest_length: int | None = None

    while queue and len(results) < max_paths:
        path = queue.popleft()
        current = path[-1]
        if shortest_length is not None and len(path) > shortest_length:
            continue
        if current == dst:
            shortest_length = len(path)
            results.append(path)
            continue
        for neighbor in adjacency.get(current, []):
            if neighbor in path:
                continue
            queue.append(path + [neighbor])

    return results
