from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Node:
    node_id: str
    node_type: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Link:
    link_id: str
    src: str
    dst: str
    bandwidth_gbps: float
    latency_us: float
    bidirectional: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopologyGraph:
    name: str
    nodes: dict[str, Node]
    links: list[Link]
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    candidate_paths: dict[tuple[str, str], list[list[str]]] = field(default_factory=dict)
